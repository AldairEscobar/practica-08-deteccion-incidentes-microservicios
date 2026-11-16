"""Servicio de cámaras (puerto 8001).

Dueño del registro de cámaras y de los hilos de captura. Por cada frame llama
al servicio de inferencia; cuando la IA confirma, el caso de uso envía la
alerta al servicio de alertas. Los estados y la bitácora se publican en el
gateway para que la PWA los reciba por WebSocket.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from incidentes import __version__
from incidentes.config import AppConfig
from incidentes.services.cameras.factory import build_use_case_factory
from incidentes.services.events import HttpEventPublisher
from incidentes.services.http import delete
from incidentes.services.settings import (
    ALERTS_URL,
    CAMERAS_DB_PATH,
    INFERENCE_URL,
)
from incidentes.shared.cameras import CameraStore
from incidentes.shared.models import CameraIn, CameraOut, CameraUpdate
from incidentes.shared.supervisor import CameraSupervisor

log = logging.getLogger(__name__)

CFG = AppConfig.from_env()
AUTOSTART = os.getenv("CAMERAS_AUTOSTART", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

camera_store = CameraStore(CAMERAS_DB_PATH)
publisher = HttpEventPublisher()
supervisor = CameraSupervisor(
    camera_store,
    publisher,
    build_use_case_factory(CFG, INFERENCE_URL, ALERTS_URL),
)


def _reset_inference(camera_id: str) -> None:
    """Olvida el estado heurístico de la cámara en el servicio de inferencia."""
    try:
        delete(f"{INFERENCE_URL}/v1/camaras/{camera_id}")
    except Exception:
        log.debug("no se pudo limpiar el estado de inferencia de %s", camera_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if AUTOSTART:
        supervisor.start_all()
    log.info("servicio de cámaras listo (%d registradas)", camera_store.count())
    try:
        yield
    finally:
        supervisor.stop_all()
        publisher.close()
        camera_store.close()


app = FastAPI(
    title="Servicio de cámaras",
    version=__version__,
    description="Registro, captura, heurísticas y vista previa MJPEG.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    statuses = supervisor.statuses()
    return {
        "status": "ok",
        "service": "camaras",
        "camaras": camera_store.count(),
        "en_vivo": sum(1 for item in statuses if item.get("running")),
    }


@app.get("/api/v1/info", include_in_schema=False)
def info() -> dict:
    return {
        "service": "camaras",
        "version": __version__,
        "camaras": camera_store.count(),
        "inferencia": INFERENCE_URL,
        "alertas": ALERTS_URL,
    }


@app.get("/api/v1/camaras", response_model=list[CameraOut])
def list_cameras() -> list[dict]:
    return supervisor.statuses()


@app.post(
    "/api/v1/camaras",
    response_model=CameraOut,
    status_code=status.HTTP_201_CREATED,
)
def create_camera(payload: CameraIn) -> dict:
    """Registra la cámara y, si está habilitada, arranca su análisis."""
    camera = camera_store.add(
        payload.name,
        payload.source,
        address=payload.address,
        lat=payload.lat,
        lon=payload.lon,
        enabled=payload.enabled,
    )
    log.warning(
        "CÁMARA registrada: %s (%s) fuente=%s",
        camera.name,
        camera.id,
        camera.source.split("://", 1)[0] if "://" in camera.source else "archivo",
    )
    if payload.enabled:
        supervisor.start(camera.id)
    return supervisor.status(camera.id) or {}


@app.patch("/api/v1/camaras/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: str, payload: CameraUpdate) -> dict:
    previous = camera_store.get(camera_id)
    if previous is None:
        raise HTTPException(status_code=404, detail=f"cámara {camera_id} no existe")

    fields = payload.model_dump(exclude_unset=True)
    for key in ("name", "source", "address", "enabled"):
        if key in fields and fields[key] is None:
            fields.pop(key)
    updated = camera_store.update(camera_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"cámara {camera_id} no existe")

    source_changed = "source" in fields and updated.source != previous.source
    if source_changed or updated.enabled is False:
        supervisor.stop(camera_id)
        _reset_inference(camera_id)
    if updated.enabled and (source_changed or not previous.enabled):
        supervisor.start(camera_id)

    return supervisor.status(camera_id) or {}


@app.delete("/api/v1/camaras/{camera_id}")
def delete_camera(camera_id: str) -> dict:
    if not supervisor.delete(camera_id):
        raise HTTPException(status_code=404, detail=f"cámara {camera_id} no existe")
    _reset_inference(camera_id)
    log.info("cámara %s eliminada", camera_id)
    return {"removed": camera_id}


@app.post("/api/v1/camaras/{camera_id}/iniciar", response_model=CameraOut)
def start_camera(camera_id: str) -> dict:
    if supervisor.start(camera_id) is None:
        raise HTTPException(status_code=404, detail=f"cámara {camera_id} no existe")
    return supervisor.status(camera_id) or {}


@app.post("/api/v1/camaras/{camera_id}/detener", response_model=CameraOut)
def stop_camera(camera_id: str) -> dict:
    status_now = supervisor.status(camera_id)
    if status_now is None:
        raise HTTPException(status_code=404, detail=f"cámara {camera_id} no existe")
    supervisor.stop(camera_id)
    _reset_inference(camera_id)
    return supervisor.status(camera_id) or status_now


@app.get("/api/v1/camaras/{camera_id}/snapshot")
def camera_snapshot(camera_id: str) -> Response:
    """Último frame anotado disponible, útil como miniatura."""
    runtime = supervisor.runtime(camera_id)
    jpeg = runtime.snapshot() if runtime is not None else None
    if jpeg is None:
        raise HTTPException(
            status_code=404, detail=f"cámara {camera_id} sin imagen todavía"
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/v1/camaras/{camera_id}/stream")
def camera_stream(camera_id: str) -> Response:
    """Vista previa MJPEG del frame anotado (para un ``<img>`` en la PWA)."""
    runtime = supervisor.runtime(camera_id)
    if runtime is None:
        raise HTTPException(
            status_code=404, detail=f"cámara {camera_id} no está en marcha"
        )
    return StreamingResponse(
        runtime.mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
