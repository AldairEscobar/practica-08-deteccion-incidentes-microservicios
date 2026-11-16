"""Composition root del servicio de cámaras.

Conecta el caso de uso de la práctica anterior con los adaptadores remotos:
inferencia (YOLO/Ollama) y alertas (tickets). El pipeline local no cambia.
"""

from __future__ import annotations

from functools import partial

from incidentes.application.detect_incidents import DetectIncidentsUseCase
from incidentes.config import AppConfig
from incidentes.domain.entities import DetectionConfig, GeoLocation
from incidentes.infrastructure.alerting.http_alert_client import HttpAlertDispatcher
from incidentes.infrastructure.video.frames import encode_snapshot, mark_pending
from incidentes.infrastructure.video.opencv_reader import get_video_info
from incidentes.infrastructure.video.sources import create_reader
from incidentes.services.cameras.remote import RemoteDetector, RemoteVerifier
from incidentes.services.settings import DETECT_TIMEOUT_SEC, VERIFY_TIMEOUT_SEC
from incidentes.shared.cameras import Camera
from incidentes.shared.supervisor import UseCaseFactory


def build_use_case_factory(
    config: AppConfig,
    inference_url: str,
    alerts_url: str,
) -> UseCaseFactory:
    """Devuelve una fábrica ``Camera -> DetectIncidentsUseCase``."""
    detection_config = DetectionConfig(
        model_path=config.model_path,
        person_class_id=config.person_class_id,
        confidence=config.confidence,
        motion_threshold=config.motion_threshold,
        proximity_factor=config.proximity_factor,
        min_persistence=config.suspicion_min_persistence,
        cooldown_frames=config.cooldown_frames,
    )

    def factory(camera: Camera) -> DetectIncidentsUseCase:
        detector = RemoteDetector(
            camera.id,
            inference_url,
            timeout_sec=DETECT_TIMEOUT_SEC,
        )
        verifier = RemoteVerifier(
            inference_url,
            timeout_sec=VERIFY_TIMEOUT_SEC,
            model=config.ollama_model,
        )
        dispatcher = HttpAlertDispatcher(
            base_url=alerts_url,
            client_id=camera.id,
            location=GeoLocation(
                lat=camera.lat,
                lon=camera.lon,
                address=camera.address,
            ),
            timeout_sec=config.alert_timeout_sec,
            include_snapshot=True,
            alerts_path="/api/v1/alertas",
        )
        return DetectIncidentsUseCase(
            suspicion_detector=detector,
            verifier=verifier,
            reader_factory=create_reader,
            writer_factory=None,
            info_provider=get_video_info,
            snapshot_encoder=partial(encode_snapshot, max_width=config.snapshot_width),
            pending_marker=mark_pending,
            config=detection_config,
            verify_cooldown_sec=config.verify_cooldown_sec,
            drain_timeout_sec=config.verify_drain_timeout_sec,
            alert_dispatcher=dispatcher,
            alert_once=False,
            status_reader=None,
        )

    return factory
