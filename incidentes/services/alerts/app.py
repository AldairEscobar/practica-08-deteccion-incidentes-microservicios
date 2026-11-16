"""Servicio de alertas (puerto 8003).

Es el dueño de la base de tickets y de la evidencia. El servicio de cámaras le
manda un POST cuando la IA confirma un incidente; este calcula el despacho,
persiste la captura y publica ``alerta``/``alerta_actualizada`` en el gateway.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from incidentes import __version__
from incidentes.services.events import HttpEventPublisher
from incidentes.services.settings import ALERTS_DB_PATH
from incidentes.shared.dispatch import DispatchPlan, notify_units, plan_dispatch
from incidentes.shared.models import (
    AlertAck,
    AlertIn,
    AlertRecord,
    StatusUpdate,
)
from incidentes.shared.store import AlertStore

log = logging.getLogger(__name__)


def create_app(
    alert_store: AlertStore | None = None,
    event_publisher: object | None = None,
    notifier: Callable[..., bool] = notify_units,
) -> FastAPI:
    """Construye el servicio; los tests inyectan store y publicador falsos."""
    store = alert_store or AlertStore(ALERTS_DB_PATH)
    publisher = event_publisher or HttpEventPublisher()

    def register_alert(alert: AlertIn) -> AlertRecord:
        """Calcula el plan, persiste y avisa a las unidades (si hay webhook)."""
        plan: DispatchPlan = plan_dispatch(alert)
        record = AlertRecord(
            ticket="",
            status="registrada",
            priority=plan["priority"],
            eta_min=plan["eta_min"],
            units=plan["units"],
            received_at=datetime.now(UTC).isoformat(timespec="seconds"),
            alert=alert,
        )
        saved = store.add(record)
        if notifier(saved.ticket, alert, plan):
            store.update_status(saved.ticket, "despachada")
            saved = saved.model_copy(update={"status": "despachada"})
        log.warning("ALERTA %s (%s): %s", saved.ticket, saved.status, plan["message"])
        return saved

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            publisher.close()  # type: ignore[attr-defined]
            store.close()

    app = FastAPI(
        title="Servicio de alertas",
        version=__version__,
        description="Tickets, prioridad, ETA y evidencia persistida.",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "alertas", "alertas": store.count()}

    @app.get("/api/v1/info", include_in_schema=False)
    def info() -> dict:
        return {
            "service": "alertas",
            "version": __version__,
            "alertas": store.count(),
        }

    @app.post(
        "/api/v1/alertas",
        response_model=AlertAck,
        status_code=status.HTTP_201_CREATED,
    )
    def receive_alert(alert: AlertIn) -> AlertAck:
        saved = register_alert(alert)
        plan = plan_dispatch(alert)
        publisher.publish_event("alerta", saved.model_dump())  # type: ignore[attr-defined]
        return AlertAck(
            ticket=saved.ticket,
            status=saved.status,
            priority=saved.priority,
            eta_min=saved.eta_min,
            units=saved.units,
            message=f"{plan['notify']} Ticket {saved.ticket}.",
            received_at=saved.received_at,
        )

    @app.get("/api/v1/alertas", response_model=list[AlertRecord])
    def list_alerts(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
        return store.list(limit)

    @app.get("/api/v1/alertas/{ticket}", response_model=AlertRecord)
    def get_alert(ticket: str) -> dict:
        record = store.get(ticket)
        if record is None:
            raise HTTPException(status_code=404, detail=f"ticket {ticket} no existe")
        return record

    @app.get("/api/v1/alertas/{ticket}/json")
    def get_alert_json(ticket: str) -> JSONResponse:
        record = store.get(ticket)
        if record is None:
            raise HTTPException(status_code=404, detail=f"ticket {ticket} no existe")
        return JSONResponse(
            content=record,
            headers={"Content-Disposition": f'attachment; filename="{ticket}.json"'},
        )

    @app.get("/api/v1/alertas/{ticket}/captura")
    def get_alert_snapshot(ticket: str) -> Response:
        evidence = store.get_snapshot(ticket)
        if evidence is None:
            raise HTTPException(
                status_code=404, detail=f"ticket {ticket} no tiene captura"
            )
        data, mime = evidence
        return Response(
            content=data,
            media_type=mime or "image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.patch("/api/v1/alertas/{ticket}", response_model=AlertRecord)
    def update_alert(ticket: str, update: StatusUpdate) -> dict:
        record = store.update_status(ticket, update.status)
        if record is None:
            raise HTTPException(status_code=404, detail=f"ticket {ticket} no existe")
        log.info("estado de %s -> %s", ticket, update.status)
        publisher.publish_event("alerta_actualizada", record)  # type: ignore[attr-defined]
        return record

    return app


app = create_app()
