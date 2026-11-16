"""Contratos de los adaptadores remotos: parseo y fail-open/fail-closed."""

from __future__ import annotations

import numpy as np

from incidentes.domain.entities import IncidentType, Suspicion
from incidentes.services.cameras import remote


def _frame() -> np.ndarray:
    return np.zeros((16, 16, 3), dtype=np.uint8)


def test_detector_remoto_parsea_sospechas(monkeypatch) -> None:
    monkeypatch.setattr(
        remote,
        "post_json",
        lambda *args, **kwargs: {
            "annotated_b64": "",
            "incidents": [
                {"type": "PELEA", "confidence": 0.82, "label": "grupo"}
            ],
            "alert": False,
        },
    )
    detector = remote.RemoteDetector("cam-1", "http://inferencia")
    result = detector.analyze(_frame())

    assert len(result.incidents) == 1
    assert result.incidents[0].type == IncidentType.FIGHT
    assert result.incidents[0].confidence == 0.82
    assert result.alert is False


def test_detector_remoto_es_fail_open(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("inferencia caída")

    monkeypatch.setattr(remote, "post_json", boom)
    detector = remote.RemoteDetector("cam-1", "http://inferencia")
    frame = _frame()
    result = detector.analyze(frame)

    assert result.incidents == ()
    assert result.alert is False
    assert result.annotated_frame is frame


def test_verificador_remoto_parsea_veredicto(monkeypatch) -> None:
    monkeypatch.setattr(
        remote,
        "post_json",
        lambda *args, **kwargs: {
            "violent": True,
            "confidence": 0.9,
            "type": "PELEA",
            "reason": "forcejeo",
        },
    )
    verifier = remote.RemoteVerifier("http://inferencia", model="qwen")
    verdict = verifier.verify(
        b"\xff\xd8jpeg",
        Suspicion(frame=1, time_sec=0.1, type="PELEA", label="x"),
    )

    assert verdict.violent is True
    assert verdict.type == IncidentType.FIGHT
    assert verdict.reason == "forcejeo"
    assert verifier.name == "remoto/ollama/qwen"


def test_verificador_remoto_es_fail_closed(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("ollama caído")

    monkeypatch.setattr(remote, "post_json", boom)
    verifier = remote.RemoteVerifier("http://inferencia")
    verdict = verifier.verify(
        b"\xff\xd8jpeg",
        Suspicion(frame=1, time_sec=0.1, type="PELEA", label="x"),
    )

    assert verdict.violent is False
    assert "verifier-remoto-error" in verdict.reason


def test_ping_del_verificador_lee_ollama(monkeypatch) -> None:
    monkeypatch.setattr(remote, "get_json", lambda *args, **kwargs: {"ollama": True})
    assert remote.RemoteVerifier("http://inferencia").ping() is True

    monkeypatch.setattr(remote, "get_json", lambda *args, **kwargs: {"ollama": False})
    assert remote.RemoteVerifier("http://inferencia").ping() is False
