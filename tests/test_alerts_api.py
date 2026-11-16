"""Pruebas del servicio de alertas: tickets, evidencia y eventos."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from incidentes.services.alerts.app import create_app
from incidentes.shared.store import AlertStore


class FakePublisher:
    """Publicador en memoria para no depender del gateway."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.closed = False

    def publish_event(self, kind: str, data) -> None:
        self.events.append((kind, data))

    def close(self) -> None:
        self.closed = True


def _client(tmp_path):
    store = AlertStore(tmp_path / "alerts.db")
    publisher = FakePublisher()
    app = create_app(
        alert_store=store,
        event_publisher=publisher,
        notifier=lambda *args, **kwargs: False,
    )
    return TestClient(app), store, publisher


def test_api_guarda_y_sirve_json_y_captura(tmp_path) -> None:
    client, store, publisher = _client(tmp_path)

    jpeg = b"\xff\xd8\xff\xe0captura-de-prueba"
    response = client.post(
        "/api/v1/alertas",
        json={
            "client_id": "camara-01",
            "type": "PELEA",
            "confidence": 0.93,
            "label": "pelea",
            "reason": "forcejeo entre dos personas",
            "timestamp": "2026-10-22T12:00:00+00:00",
            "location": {"address": "Centro"},
            "snapshot_b64": base64.b64encode(jpeg).decode(),
        },
    )
    assert response.status_code == 201, response.text
    ticket = response.json()["ticket"]
    assert publisher.events[-1][0] == "alerta"

    detail = client.get(f"/api/v1/alertas/{ticket}")
    assert detail.status_code == 200
    record = detail.json()
    assert record["has_snapshot"] is True
    assert record["alert"]["snapshot_b64"] is None
    assert record["snapshot_url"] == f"/api/v1/alertas/{ticket}/captura"
    assert record["snapshot_sha256"]

    evidence = client.get(record["snapshot_url"])
    assert evidence.status_code == 200
    assert evidence.content == jpeg
    assert evidence.headers["content-type"] == "image/jpeg"

    as_json = client.get(f"/api/v1/alertas/{ticket}/json")
    assert as_json.status_code == 200
    assert as_json.json()["ticket"] == ticket
    assert "attachment" in as_json.headers["content-disposition"]

    updated = client.patch(
        f"/api/v1/alertas/{ticket}", json={"status": "atendida"}
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "atendida"
    assert publisher.events[-1][0] == "alerta_actualizada"


def test_historial_y_ticket_inexistente(tmp_path) -> None:
    client, store, _ = _client(tmp_path)
    assert client.get("/api/v1/alertas").json() == []
    assert client.get("/api/v1/alertas/ALT-NOPE").status_code == 404
    assert client.get("/api/v1/alertas/ALT-NOPE/captura").status_code == 404
    assert client.patch(
        "/api/v1/alertas/ALT-NOPE", json={"status": "atendida"}
    ).status_code == 404
    store.close()
