"""Publicador de eventos por HTTP hacia el gateway.

Sin broker externo: cada servicio encola el evento y un hilo lo POSTea a
``/internal/eventos`` del gateway, que lo difunde por WebSocket. El contrato es
*fire-and-forget*: si el gateway está caído, la detección sigue y el evento se
pierde (se registra en el log). Para producción, sustituir por Redis/NATS.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import urllib.request
from typing import Any

from incidentes.services.settings import EVENT_TIMEOUT_SEC, GATEWAY_URL, INTERNAL_TOKEN

log = logging.getLogger(__name__)

QUEUE_SIZE = 256


class HttpEventPublisher:
    """Cola acotada + hilo emisor; seguro desde cualquier hilo."""

    def __init__(
        self,
        gateway_url: str = GATEWAY_URL,
        token: str = INTERNAL_TOKEN,
    ) -> None:
        self._url = f"{gateway_url.rstrip('/')}/internal/eventos"
        self._token = token
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=QUEUE_SIZE
        )
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="event-publisher"
        )
        self._thread.start()

    def publish_event(self, kind: str, data: Any) -> None:
        """Encola un evento. Si la cola está llena, lo descarta."""
        try:
            self._queue.put_nowait({"kind": kind, "data": data})
        except queue.Full:
            log.warning("cola de eventos llena: se descarta %s", kind)

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            try:
                if message is None:
                    return
                self._post(message)
            finally:
                self._queue.task_done()

    def _post(self, message: dict[str, Any]) -> None:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["X-Internal-Token"] = self._token
        request = urllib.request.Request(
            self._url,
            data=json.dumps(message, default=str).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=EVENT_TIMEOUT_SEC) as response:
                response.read()
        except Exception as exc:
            log.debug("no se pudo publicar el evento %s: %s", message.get("kind"), exc)

    def close(self) -> None:
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        self._thread.join(timeout=2)
