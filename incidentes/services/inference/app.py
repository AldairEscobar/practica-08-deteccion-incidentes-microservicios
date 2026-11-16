"""Servicio de inferencia (puerto 8002).

Mantiene un ``YoloIncidentDetector`` por cámara porque la heurística tiene
estado temporal (movimiento entre frames y persistencia de señales). La parte
de Ollama es stateless. Si el servicio se reinicia, el estado se reconstruye
con los siguientes frames.

No tiene base de datos ni habla con los demás servicios: recibe un JPEG y
devuelve detecciones, o recibe una sospecha y devuelve un veredicto.
"""

from __future__ import annotations

import base64
import binascii
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from ultralytics import YOLO

from incidentes import __version__
from incidentes.config import AppConfig
from incidentes.domain.entities import (
    DetectionConfig,
    IncidentType,
    Suspicion,
)
from incidentes.infrastructure.detection.incident_detector import YoloIncidentDetector
from incidentes.infrastructure.verification.ollama_verifier import (
    OllamaViolenceVerifier,
)

log = logging.getLogger(__name__)

CFG = AppConfig.from_env()
DETECTION_CONFIG = DetectionConfig(
    model_path=CFG.model_path,
    person_class_id=CFG.person_class_id,
    confidence=CFG.confidence,
    motion_threshold=CFG.motion_threshold,
    proximity_factor=CFG.proximity_factor,
    min_persistence=CFG.suspicion_min_persistence,
    cooldown_frames=CFG.cooldown_frames,
)
VERIFIER = OllamaViolenceVerifier(
    model=CFG.ollama_model,
    base_url=CFG.ollama_url,
    timeout_sec=CFG.ollama_timeout_sec,
)

_DETECTORS: dict[str, YoloIncidentDetector] = {}
_DETECTORS_LOCK = threading.Lock()
# Un solo modelo compartido y un solo lock: YOLO no garantiza ser thread-safe.
_INFERENCE_LOCK = threading.Lock()
_SHARED_MODEL: Any | None = None

PREVIEW_MAX_WIDTH = 960
PREVIEW_QUALITY = 80


class AnalyzeIn(BaseModel):
    """Frame JPEG en base64 que envía el servicio de cámaras."""

    camera_id: str = Field(min_length=1)
    image_b64: str = Field(min_length=1)


class IncidentOut(BaseModel):
    """Sospecha detectada por YOLO + heurísticas."""

    type: str
    confidence: float
    label: str


class AnalyzeOut(BaseModel):
    """Frame anotado y sospechas del frame."""

    annotated_b64: str
    incidents: list[IncidentOut] = Field(default_factory=list)
    alert: bool = False


class SuspicionIn(BaseModel):
    """Contexto de la sospecha que se manda a Ollama."""

    frame: int = 0
    time_sec: float = 0.0
    type: str = IncidentType.NOTHING
    label: str = ""
    heuristic_confidence: float = 0.0
    timestamp: str = ""


class VerifyIn(BaseModel):
    """JPEG de la sospecha + contexto para Ollama."""

    suspicion: SuspicionIn
    image_b64: str = Field(min_length=1)


class VerifyOut(BaseModel):
    """Veredicto de la etapa 2."""

    violent: bool
    confidence: float
    type: str
    reason: str = ""
    verifier: str


def _decode_b64(image_b64: str) -> bytes:
    try:
        return base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_b64 inválido") from exc


def _decode_frame(image_b64: str) -> np.ndarray:
    raw = _decode_b64(image_b64)
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="JPEG inválido")
    return frame


def _encode_frame(frame: Any) -> str:
    height, width = frame.shape[:2]
    if width > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / float(width)
        frame = cv2.resize(
            frame,
            (PREVIEW_MAX_WIDTH, max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(
        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY]
    )
    if not ok:
        raise HTTPException(status_code=500, detail="no se pudo codificar el frame")
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def _detector(camera_id: str) -> YoloIncidentDetector:
    """Crea (o reutiliza) el detector de una cámara con el modelo compartido."""
    if _SHARED_MODEL is None:
        raise HTTPException(status_code=503, detail="modelo YOLO no cargado")
    with _DETECTORS_LOCK:
        detector = _DETECTORS.get(camera_id)
        if detector is None:
            detector = YoloIncidentDetector(
                DETECTION_CONFIG,
                violence_model_path=CFG.violence_model_path,
                mark_confirmed=False,
                model=_SHARED_MODEL,
            )
            _DETECTORS[camera_id] = detector
        return detector


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _SHARED_MODEL
    _SHARED_MODEL = YOLO(CFG.model_path)
    # Calienta el modelo para que el primer frame real no agote el timeout.
    _SHARED_MODEL(np.zeros((320, 320, 3), dtype=np.uint8), verbose=False)
    log.info("inferencia lista; modelo=%s verificador=%s", CFG.model_path, VERIFIER.name)
    try:
        yield
    finally:
        with _DETECTORS_LOCK:
            _DETECTORS.clear()
            _SHARED_MODEL = None


app = FastAPI(
    title="Servicio de inferencia",
    version=__version__,
    description="YOLO para proponer sospechas y Ollama para confirmarlas.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, Any]:
    """Estado del modelo y de Ollama (puede tardar si Ollama no responde)."""
    return {
        "status": "ok",
        "service": "inferencia",
        "model": CFG.model_path,
        "verifier": VERIFIER.name,
        "ollama": VERIFIER.ping(),
        "model_loaded": _SHARED_MODEL is not None,
        "camaras": len(_DETECTORS),
    }


@app.get("/api/v1/info", include_in_schema=False)
def info() -> dict[str, Any]:
    return {
        "service": "inferencia",
        "model": CFG.model_path,
        "verifier": VERIFIER.name,
        "endpoints": ["/v1/analizar", "/v1/verificar", "/health"],
    }


@app.post("/v1/analizar", response_model=AnalyzeOut)
def analyze(payload: AnalyzeIn) -> AnalyzeOut:
    """Analiza un frame y devuelve el frame anotado + sospechas."""
    frame = _decode_frame(payload.image_b64)
    detector = _detector(payload.camera_id)
    try:
        with _INFERENCE_LOCK:
            analysis = detector.analyze(frame)
    except Exception as exc:
        log.exception("fallo de YOLO en la cámara %s", payload.camera_id)
        raise HTTPException(
            status_code=503, detail=f"inferencia no disponible: {exc}"[:200]
        ) from exc

    annotated = analysis.annotated_frame if analysis.annotated_frame is not None else frame
    return AnalyzeOut(
        annotated_b64=_encode_frame(annotated),
        incidents=[
            IncidentOut(
                type=incident.type,
                confidence=incident.confidence,
                label=incident.label,
            )
            for incident in analysis.incidents
        ],
        alert=analysis.alert,
    )


@app.post("/v1/verificar", response_model=VerifyOut)
def verify(payload: VerifyIn) -> VerifyOut:
    """Confirma o descarta una sospecha con Ollama (fail-closed)."""
    jpeg = _decode_b64(payload.image_b64)
    suspicion = Suspicion(
        frame=payload.suspicion.frame,
        time_sec=payload.suspicion.time_sec,
        type=payload.suspicion.type,
        label=payload.suspicion.label,
        heuristic_confidence=payload.suspicion.heuristic_confidence,
        timestamp=payload.suspicion.timestamp,
    )
    verdict = VERIFIER.verify(jpeg, suspicion)
    return VerifyOut(
        violent=verdict.violent,
        confidence=verdict.confidence,
        type=verdict.type,
        reason=verdict.reason,
        verifier=VERIFIER.name,
    )


@app.delete("/v1/camaras/{camera_id}")
def reset_camera(camera_id: str) -> dict[str, Any]:
    """Olvida el estado heurístico de una cámara detenida."""
    with _DETECTORS_LOCK:
        removed = _DETECTORS.pop(camera_id, None) is not None
    return {"removed": camera_id, "was_loaded": removed}
