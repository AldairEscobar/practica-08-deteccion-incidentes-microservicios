"""Configuración compartida por los servicios de cámaras e inferencia.

Los valores se pueden sobrescribir con variables de entorno con el prefijo
``INCIDENTES_`` (ver ``.env.example``). La configuración es inmutable: se crea
una vez en el arranque de cada servicio.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"La variable {name} debe ser numérica, no {raw!r}") from exc


@dataclass(frozen=True)
class AppConfig:
    """Parámetros de detección, verificación y alerta."""

    # --- Detección (etapa 1) ---
    model_path: str = "yolo11n.pt"
    # Modelo propio opcional (p. ej. un .pt entrenado en peleas). Si el archivo
    # existe se usa en lugar de la heurística; si no, se usa YOLO-COCO.
    violence_model_path: str = "violence.pt"
    person_class_id: int = 0
    confidence: float = 0.5
    motion_threshold: float = 12.0
    proximity_factor: float = 0.7
    suspicion_min_persistence: int = 2  # bajo a propósito: recall alto
    cooldown_frames: int = 30

    # --- Verificación (etapa 2, Ollama) ---
    ollama_model: str = "qwen2.5vl:7b"
    ollama_url: str = "http://localhost:11434"
    ollama_timeout_sec: int = 120
    verify_cooldown_sec: float = 8.0  # mínimo entre envíos a la IA
    verify_drain_timeout_sec: float = 180.0  # espera máxima al cerrar
    snapshot_width: int = 480  # ancho del JPEG que recibe el modelo

    # --- Alerta ---
    alert_timeout_sec: float = 5.0  # no bloquear la detección si alertas cae

    @classmethod
    def from_env(cls) -> AppConfig:
        """Construye la configuración leyendo ``INCIDENTES_*`` del entorno."""
        return cls(
            model_path=_env_str("INCIDENTES_MODEL_PATH", cls.model_path),
            violence_model_path=_env_str(
                "INCIDENTES_VIOLENCE_MODEL", cls.violence_model_path
            ),
            confidence=_env_float("INCIDENTES_CONFIDENCE", cls.confidence),
            motion_threshold=_env_float(
                "INCIDENTES_MOTION_THRESHOLD", cls.motion_threshold
            ),
            proximity_factor=_env_float(
                "INCIDENTES_PROXIMITY_FACTOR", cls.proximity_factor
            ),
            ollama_model=_env_str("INCIDENTES_OLLAMA_MODEL", cls.ollama_model),
            ollama_url=_env_str("INCIDENTES_OLLAMA_URL", cls.ollama_url),
            alert_timeout_sec=_env_float(
                "INCIDENTES_ALERT_TIMEOUT", cls.alert_timeout_sec
            ),
        )
