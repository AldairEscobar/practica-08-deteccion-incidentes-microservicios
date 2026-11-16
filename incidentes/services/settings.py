"""Configuración compartida por los servicios (URLs, puertos y rutas).

Cada servicio se configura por entorno. El lanzador ``incidentes.cli.stack``
rellena estas variables para que todos apunten a los puertos locales.
"""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


GATEWAY_URL = _env("GATEWAY_URL", "http://127.0.0.1:8000")
CAMERAS_URL = _env("CAMERAS_URL", "http://127.0.0.1:8001")
INFERENCE_URL = _env("INFERENCE_URL", "http://127.0.0.1:8002")
ALERTS_URL = _env("ALERTS_URL", "http://127.0.0.1:8003")

INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
API_KEY = os.getenv("CENTRAL_API_KEY", "")

CAMERAS_DB_PATH = _env("CAMERAS_DB_PATH", "data/cameras.db")
ALERTS_DB_PATH = _env("ALERTS_DB_PATH", "data/alerts.db")

# Un frame puede tardar en el primer uso (carga de YOLO); los eventos no deben
# bloquear el pipeline.
DETECT_TIMEOUT_SEC = float(_env("DETECT_TIMEOUT_SEC", "8.0"))
VERIFY_TIMEOUT_SEC = float(_env("VERIFY_TIMEOUT_SEC", "130.0"))
EVENT_TIMEOUT_SEC = float(_env("EVENT_TIMEOUT_SEC", "2.0"))
HTTP_TIMEOUT_SEC = float(_env("HTTP_TIMEOUT_SEC", "5.0"))
