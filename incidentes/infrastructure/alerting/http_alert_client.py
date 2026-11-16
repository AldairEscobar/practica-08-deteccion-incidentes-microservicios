"""Adaptador de salida hacia el servicio de alertas (REST).

Solo usa la librería estándar (``urllib``) para no añadir dependencias al
servicio de cámaras. Contrato **fail-open**: nunca lanza; si alertas está
caído, la captura sigue funcionando y la alerta se reporta como no entregada.

El servicio de alertas decide la ayuda a enviar según el tipo de incidente y
la ubicación que este adaptador adjunta (``client_id`` + ubicación).
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime

from incidentes.domain.entities import AlertAck, AlertPayload, GeoLocation
from incidentes.domain.ports import AlertDispatcher

log = logging.getLogger(__name__)


class HttpAlertDispatcher(AlertDispatcher):
    """Cliente REST del servicio de alertas."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        client_id: str = "camara-01",
        location: GeoLocation | None = None,
        timeout_sec: float = 5.0,
        api_key: str = "",
        include_snapshot: bool = True,
        alerts_path: str = "/api/v1/alertas",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._location = location or GeoLocation()
        self._timeout = timeout_sec
        self._api_key = api_key
        self._include_snapshot = include_snapshot
        self._alerts_path = alerts_path

    # ------------------------------------------------------------------ utils
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    # ----------------------------------------------------------------- alerta
    def _build_payload(self, alert: AlertPayload) -> dict:
        timestamp = alert.timestamp or datetime.now(UTC).isoformat(
            timespec="seconds"
        )
        body: dict = {
            "client_id": self._client_id,
            "type": alert.type,
            "confidence": alert.confidence,
            "label": alert.label,
            "frame": alert.frame,
            "time_sec": alert.time_sec,
            "video": alert.video,
            "verified_by": alert.verified_by,
            "reason": alert.reason,
            "timestamp": timestamp,
            "location": {
                "lat": self._location.lat,
                "lon": self._location.lon,
                "address": self._location.address,
            },
        }
        if self._include_snapshot and alert.snapshot_jpeg:
            body["snapshot_b64"] = base64.b64encode(alert.snapshot_jpeg).decode()
        return body

    def dispatch(self, alert: AlertPayload) -> AlertAck:
        """Envía la alerta. Nunca lanza: devuelve ``delivered=False`` si falla."""
        try:
            request = urllib.request.Request(
                f"{self._base_url}{self._alerts_path}",
                data=json.dumps(self._build_payload(alert)).encode(),
                headers=self._headers(),
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode())
            return AlertAck(
                delivered=True,
                ticket=str(body.get("ticket", "")),
                status=str(body.get("status", "recibida")),
                priority=str(body.get("priority", "")),
                eta_min=body.get("eta_min"),
                message=str(body.get("message", "")),
            )
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
            with contextlib.suppress(Exception):
                detail += f": {exc.read().decode()[:200]}"
            log.error("alertas rechazó la alerta (%s): %s", exc.code, detail)
            return AlertAck(False, detail=detail[:200])
        except Exception as exc:
            # Fail-open: no se pudo entregar, pero la detección local continúa.
            log.error(
                "no se pudo enviar la alerta a alertas (%s): %s",
                self._base_url,
                exc,
            )
            return AlertAck(False, detail=f"dispatch-error: {exc}"[:200])
