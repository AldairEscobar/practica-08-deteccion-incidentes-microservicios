"""API Gateway (puerto 8000).

Es el único proceso que ve el navegador:

- sirve la PWA instalable;
- proxifica ``/api/v1/camaras`` al servicio de cámaras y
  ``/api/v1/alertas`` al servicio de alertas;
- mantiene el WebSocket ``/ws/eventos``;
- recibe los eventos internos de los servicios y los difunde a la PWA.

Los servicios internos no se exponen: solo hablan con el gateway o entre ellos.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from incidentes import __version__
from incidentes.services.settings import (
    ALERTS_URL,
    API_KEY,
    CAMERAS_URL,
    INFERENCE_URL,
    INTERNAL_TOKEN,
)
from incidentes.shared.events import EventBus

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).with_name("web")
CACHE_HEADERS = {
    "content-type",
    "cache-control",
    "content-disposition",
    "etag",
    "last-modified",
}

bus = EventBus()
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    """Cliente HTTP compartido; se crea perezosamente."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=2.0),
            follow_redirects=False,
        )
    return _client


@asynccontextmanager
async def lifespan(_: FastAPI):
    bus.bind_loop(asyncio.get_running_loop())
    log.info("gateway listo en http://127.0.0.1:8000 (PWA en /)")
    try:
        yield
    finally:
        bus.unbind()
        if _client is not None:
            await _client.aclose()


app = FastAPI(
    title="Gateway de detección de incidentes",
    version=__version__,
    description="PWA, proxy REST y WebSocket de la práctica 8.",
    lifespan=lifespan,
)


def require_api_key(request: Request) -> None:
    """Valida la clave en el borde; los servicios internos no la exigen."""
    if not API_KEY:
        return
    header = request.headers.get("x-api-key")
    query = request.query_params.get("api_key")
    if header != API_KEY and query != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida o ausente",
        )


def _response_headers(upstream: httpx.Headers) -> dict[str, str]:
    """Copia solo cabeceras seguras; httpx ya descomprimió el cuerpo."""
    return {
        key: value
        for key, value in upstream.items()
        if key.lower() in CACHE_HEADERS
    }


async def _proxy(request: Request, base: str, path: str) -> Response:
    """Reenvía una petición al servicio indicado y devuelve su respuesta."""
    require_api_key(request)
    url = f"{base.rstrip('/')}{path}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    body = await request.body()
    try:
        upstream = await client().request(
            request.method,
            url,
            params=request.query_params,
            content=body or None,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"servicio no disponible: {exc}"[:200],
        ) from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers),
    )


# ---------------------------------------------------------------------------
# Salud y descubrimiento
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, Any]:
    """Comprueba los tres servicios internos; un fallo no tumba el gateway."""
    services = {
        "camaras": f"{CAMERAS_URL}/health",
        "inferencia": f"{INFERENCE_URL}/health",
        "alertas": f"{ALERTS_URL}/health",
    }
    results: dict[str, Any] = {}
    for name, url in services.items():
        try:
            response = await client().get(url, timeout=6.0)
            results[name] = (
                response.json()
                if response.status_code == 200
                else {"status": "down", "code": response.status_code}
            )
        except Exception:
            results[name] = {"status": "down"}
    ok = all(item.get("status") == "ok" for item in results.values())
    return {
        "status": "ok" if ok else "degradado",
        "gateway": "ok",
        "servicios": results,
    }


@app.get("/api/v1/info", include_in_schema=False)
def info() -> dict[str, Any]:
    return {
        "service": "gateway",
        "version": __version__,
        "pwa": "/",
        "docs": "/docs",
        "servicios": {
            "camaras": CAMERAS_URL,
            "inferencia": INFERENCE_URL,
            "alertas": ALERTS_URL,
        },
    }


# ---------------------------------------------------------------------------
# Proxy REST
# ---------------------------------------------------------------------------
@app.get("/api/v1/camaras/{camera_id}/stream")
async def camera_stream(camera_id: str, request: Request) -> StreamingResponse:
    """Proxy MJPEG: se reenvía el stream tal cual, sin bufferizar."""
    require_api_key(request)
    url = f"{CAMERAS_URL}/api/v1/camaras/{camera_id}/stream"

    async def relay():
        async with client().stream(
            "GET", url, params=request.query_params
        ) as upstream:
            if upstream.status_code >= 400:
                return
            async for chunk in upstream.aiter_raw():
                yield chunk

    return StreamingResponse(
        relay(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.api_route("/api/v1/camaras", methods=["GET", "POST"])
async def cameras_root(request: Request) -> Response:
    return await _proxy(request, CAMERAS_URL, "/api/v1/camaras")


@app.api_route(
    "/api/v1/camaras/{path:path}",
    methods=["GET", "POST", "PATCH", "DELETE"],
)
async def cameras_proxy(path: str, request: Request) -> Response:
    return await _proxy(request, CAMERAS_URL, f"/api/v1/camaras/{path}")


@app.api_route("/api/v1/alertas", methods=["GET", "POST"])
async def alerts_root(request: Request) -> Response:
    return await _proxy(request, ALERTS_URL, "/api/v1/alertas")


@app.api_route("/api/v1/alertas/{path:path}", methods=["GET", "PATCH"])
async def alerts_proxy(path: str, request: Request) -> Response:
    return await _proxy(request, ALERTS_URL, f"/api/v1/alertas/{path}")


# ---------------------------------------------------------------------------
# WebSocket y eventos internos
# ---------------------------------------------------------------------------
async def _service_count(url: str, field: str) -> int:
    """Cuenta cámaras/alertas para el saludo del WebSocket; 0 si falla."""
    try:
        response = await client().get(url, timeout=3.0)
        if response.status_code == 200:
            return int(response.json().get(field, 0))
    except Exception:
        pass
    return 0


@app.websocket("/ws/eventos")
async def events_socket(websocket: WebSocket) -> None:
    """Difunde a la PWA las alertas, la bitácora y los estados."""
    if API_KEY and websocket.query_params.get("api_key") != API_KEY:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = bus.subscribe()
    try:
        counts = {
            "camaras": await _service_count(f"{CAMERAS_URL}/health", "camaras"),
            "alertas": await _service_count(f"{ALERTS_URL}/health", "alertas"),
        }
        await websocket.send_json(
            {"kind": "hola", "data": {"version": __version__, **counts}}
        )
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=20.0)
            except TimeoutError:
                message = {"kind": "ping", "data": None}
            await websocket.send_json(message)
    except WebSocketDisconnect:
        log.debug("PWA desconectada del bus de eventos")
    except Exception:
        log.debug("WebSocket cerrado de forma inesperada", exc_info=True)
    finally:
        bus.unsubscribe(queue)


class InternalEvent(BaseModel):
    """Sobre ``{kind, data}`` que publican los servicios internos."""

    kind: str = Field(min_length=1)
    data: Any = None


@app.post("/internal/eventos", include_in_schema=False)
async def internal_event(
    event: InternalEvent,
    x_internal_token: str | None = Header(default=None),
) -> dict[str, bool]:
    """Recibe un evento de un servicio y lo difunde a los WebSocket."""
    if INTERNAL_TOKEN and x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="token interno inválido")
    bus.publish_event(event.kind, event.data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# PWA (se monta al final para no tapar las rutas de la API)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
