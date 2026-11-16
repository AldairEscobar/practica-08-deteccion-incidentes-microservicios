"""El gateway recibe eventos internos y los difunde por WebSocket."""

from __future__ import annotations

from fastapi.testclient import TestClient

import incidentes.services.gateway.app as gateway


def test_evento_interno_llega_al_websocket(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "API_KEY", "")
    monkeypatch.setattr(gateway, "INTERNAL_TOKEN", "")

    with (
        TestClient(gateway.app) as client,
        client.websocket_connect("/ws/eventos") as websocket,
    ):
        hello = websocket.receive_json()
        assert hello["kind"] == "hola"

        response = client.post(
            "/internal/eventos",
            json={"kind": "alerta", "data": {"ticket": "ALT-1"}},
        )
        assert response.status_code == 200
        assert response.json() == {"ok": True}

        event = websocket.receive_json()
        assert event == {"kind": "alerta", "data": {"ticket": "ALT-1"}}


def test_token_interno_se_valida(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "API_KEY", "")
    monkeypatch.setattr(gateway, "INTERNAL_TOKEN", "secreto")

    with TestClient(gateway.app) as client:
        denied = client.post(
            "/internal/eventos", json={"kind": "alerta", "data": {}}
        )
        assert denied.status_code == 401

        allowed = client.post(
            "/internal/eventos",
            json={"kind": "alerta", "data": {}},
            headers={"X-Internal-Token": "secreto"},
        )
        assert allowed.status_code == 200
