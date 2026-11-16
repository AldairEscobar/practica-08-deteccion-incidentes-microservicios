"""Pruebas del runtime de cámaras (sin YOLO ni red)."""

from __future__ import annotations

import threading

import numpy as np

from incidentes.domain.entities import ProcessingProgress, SourceState
from incidentes.shared.cameras import CameraStore
from incidentes.shared.supervisor import CameraSupervisor


class RecordingBus:
    """Sustituto del publicador que guarda los mensajes publicados."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def publish_event(self, kind: str, data) -> None:
        with self._lock:
            self.events.append({"kind": kind, "data": data})


class FakeUseCase:
    """Caso de uso mínimo: arranca, espera y termina al pedir stop."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.stopped = False
        self.frame = np.zeros((16, 16, 3), dtype=np.uint8)
        self._state = SourceState.CONNECTING

    @property
    def source_state(self) -> SourceState:
        return self._state

    def execute(self, video_path, output_path, listener, live=False) -> None:
        self._state = SourceState.LIVE
        listener.on_progress(ProcessingProgress(0, 0), self.frame)
        self.started.set()
        self.release.wait(5)
        listener.on_completed("")

    def stop(self) -> None:
        self.stopped = True
        self._state = SourceState.STOPPED
        self.release.set()


def test_supervisor_arranca_analiza_y_detiene(tmp_path) -> None:
    store = CameraStore(tmp_path / "cameras.db")
    camera = store.add("Entrada", "rtsp://192.168.1.50/stream")
    bus = RecordingBus()
    use_case = FakeUseCase()
    supervisor = CameraSupervisor(store, bus, lambda current: use_case)

    status_now = supervisor.start(camera.id)
    assert status_now is not None
    assert status_now["running"] is True
    assert use_case.started.wait(2)

    live = supervisor.status(camera.id)
    assert live is not None
    assert live["state"] == "en_vivo"
    assert any(event["kind"] == "estado" for event in bus.events)

    runtime = supervisor.runtime(camera.id)
    assert runtime is not None
    snapshot = runtime.snapshot()
    assert snapshot is not None and snapshot.startswith(b"\xff\xd8")

    assert supervisor.stop(camera.id) is True
    assert use_case.stopped is True
    assert supervisor.runtime(camera.id) is None
    stopped = supervisor.status(camera.id)
    assert stopped is not None
    assert stopped["state"] == "detenida"
    store.close()


def test_camara_sin_arrancar_aparece_detenida(tmp_path) -> None:
    store = CameraStore(tmp_path / "cameras.db")
    camera = store.add("Patio", "rtsp://10.0.0.9/1", enabled=False)
    supervisor = CameraSupervisor(store, RecordingBus(), lambda current: FakeUseCase())

    statuses = supervisor.statuses()
    assert len(statuses) == 1
    assert statuses[0]["id"] == camera.id
    assert statuses[0]["state"] == "detenida"
    assert statuses[0]["running"] is False
    assert supervisor.start("cam-inexistente") is None
    store.close()
