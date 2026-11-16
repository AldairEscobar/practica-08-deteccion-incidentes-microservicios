"""Adaptadores remotos del servicio de cámaras.

``RemoteDetector`` envía cada frame al servicio de inferencia y recibe el frame
anotado + las sospechas. ``RemoteVerifier`` envía la sospecha a Ollama a través
del mismo servicio. Ambos son **fail-open/fail-closed**:

- si la inferencia no responde, el detector devuelve el frame sin sospechas y
  el pipeline sigue (no se inventan alarmas);
- si la verificación falla, el veredicto es ``violent=False`` (fail-closed).
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Any

import cv2
import numpy as np

from incidentes.domain.entities import (
    FrameAnalysis,
    Incident,
    IncidentType,
    Suspicion,
    Verdict,
)
from incidentes.domain.ports import IncidentDetector, ViolenceVerifier
from incidentes.services.http import get_json, post_json
from incidentes.services.settings import (
    DETECT_TIMEOUT_SEC,
    VERIFY_TIMEOUT_SEC,
)

log = logging.getLogger(__name__)

FRAME_MAX_WIDTH = 960
FRAME_QUALITY = 80
# Tras un fallo de red, no se intenta inferencia durante este tiempo. Evita que
# un servicio caído bloquee el bucle de captura frame a frame.
INFERENCE_COOLDOWN_SEC = 5.0


def encode_frame(frame: Any) -> str:
    """Redimensiona y codifica un frame BGR como JPEG base64."""
    height, width = frame.shape[:2]
    if width > FRAME_MAX_WIDTH:
        scale = FRAME_MAX_WIDTH / float(width)
        frame = cv2.resize(
            frame,
            (FRAME_MAX_WIDTH, max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, FRAME_QUALITY]
    )
    if not ok:
        raise ValueError("no se pudo codificar el frame")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def decode_frame(image_b64: str) -> Any | None:
    """Decodifica un JPEG base64 a BGR; ``None`` si no es válido."""
    try:
        raw = base64.b64decode(image_b64, validate=True)
        return cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    except (binascii.Error, ValueError, cv2.error):
        return None


class RemoteDetector(IncidentDetector):
    """Etapa 1 delegada al servicio de inferencia."""

    def __init__(
        self,
        camera_id: str,
        inference_url: str,
        timeout_sec: float = DETECT_TIMEOUT_SEC,
    ) -> None:
        self._camera_id = camera_id
        self._url = f"{inference_url.rstrip('/')}/v1/analizar"
        self._timeout = timeout_sec
        self._down_until = 0.0
        self._last_error_log = 0.0

    def analyze(self, frame: Any) -> FrameAnalysis:
        """Analiza el frame; si la inferencia falla, devuelve el frame original."""
        now = time.monotonic()
        if now < self._down_until:
            return FrameAnalysis(frame, (), False)
        try:
            image_b64 = encode_frame(frame)
            body = post_json(
                self._url,
                {"camera_id": self._camera_id, "image_b64": image_b64},
                timeout=self._timeout,
            )
            incidents = tuple(
                Incident(
                    type=str(item.get("type", IncidentType.NOTHING)),
                    confidence=float(item.get("confidence", 0.0)),
                    label=str(item.get("label", "")),
                )
                for item in body.get("incidents", [])
            )
            annotated = decode_frame(str(body.get("annotated_b64", "")))
            return FrameAnalysis(
                annotated if annotated is not None else frame,
                incidents,
                bool(body.get("alert", False)),
            )
        except Exception as exc:
            self._down_until = time.monotonic() + INFERENCE_COOLDOWN_SEC
            if now - self._last_error_log >= 10.0:
                self._last_error_log = now
                log.warning(
                    "inferencia no disponible para %s (fail-open): %s",
                    self._camera_id,
                    exc,
                )
            return FrameAnalysis(frame, (), False)


class RemoteVerifier(ViolenceVerifier):
    """Etapa 2 delegada al servicio de inferencia (Ollama)."""

    def __init__(
        self,
        inference_url: str,
        timeout_sec: float = VERIFY_TIMEOUT_SEC,
        model: str = "",
    ) -> None:
        self._base_url = inference_url.rstrip("/")
        self._url = f"{self._base_url}/v1/verificar"
        self._timeout = timeout_sec
        self._model = model

    @property
    def name(self) -> str:
        return f"remoto/ollama/{self._model}" if self._model else "remoto/ollama"

    def ping(self) -> bool:
        """True si el servicio de inferencia responde y Ollama está arriba."""
        try:
            body = get_json(f"{self._base_url}/health", timeout=6.0)
            return bool(body.get("ollama"))
        except Exception:
            return False

    def verify(self, jpeg_bytes: bytes, suspicion: Suspicion) -> Verdict:
        """Envía la imagen y el contexto; ante cualquier error, no alarma."""
        try:
            body = post_json(
                self._url,
                {
                    "suspicion": {
                        "frame": suspicion.frame,
                        "time_sec": suspicion.time_sec,
                        "type": suspicion.type,
                        "label": suspicion.label,
                        "heuristic_confidence": suspicion.heuristic_confidence,
                        "timestamp": suspicion.timestamp,
                    },
                    "image_b64": base64.b64encode(jpeg_bytes).decode("ascii"),
                },
                timeout=self._timeout,
            )
            return Verdict(
                violent=bool(body.get("violent", False)),
                confidence=float(body.get("confidence", 0.0)),
                type=str(body.get("type", IncidentType.NOTHING)),
                reason=str(body.get("reason", "")),
            )
        except Exception as exc:
            log.warning(
                "verificación remota falló (fail-closed, sin alarma): %s", exc
            )
            return Verdict(
                False, 0.0, IncidentType.NOTHING, f"verifier-remoto-error: {exc}"[:200]
            )
