"""Arranca los cuatro microservicios en local (Windows/Linux/macOS).

No usa Docker ni Redis: cada servicio es un proceso uvicorn y los eventos van
por HTTP al gateway. Para el informe, la topología es la misma que en
producción; solo cambia el transporte del bus.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parents[2]

BACKENDS = [
    ("inferencia", "incidentes.services.inference.app:app", 8002),
    ("camaras", "incidentes.services.cameras.app:app", 8001),
    ("alertas", "incidentes.services.alerts.app:app", 8003),
]
GATEWAY = ("gateway", "incidentes.services.gateway.app:app", 8000)


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    env["GATEWAY_URL"] = f"http://{HOST}:8000"
    env["CAMERAS_URL"] = f"http://{HOST}:8001"
    env["INFERENCE_URL"] = f"http://{HOST}:8002"
    env["ALERTS_URL"] = f"http://{HOST}:8003"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _start(name: str, target: str, port: int, env: dict[str, str]):
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        target,
        "--host",
        HOST,
        "--port",
        str(port),
    ]
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    print(f"  {name:<10} http://{HOST}:{port}")
    return process


def _wait_ready(
    name: str, port: int, process: subprocess.Popen, timeout: float = 90.0
) -> bool:
    """Espera a que el servicio responda /health (YOLO tarda en importar)."""
    url = f"http://{HOST}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            # Ollama puede tardar ~5 s en responder al ping del /health.
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    env = _environment()
    processes: list[tuple[str, subprocess.Popen]] = []
    print("Arrancando microservicios...")
    try:
        print()
        print("Esperando a que los servicios estén listos...")
        for name, target, port in BACKENDS:
            process = _start(name, target, port, env)
            processes.append((name, process))
            if not _wait_ready(name, port, process):
                print(f"ERROR: {name} no arrancó; revisa su log arriba.")
                return 1
            print(f"  {name:<10} listo")

        name, target, port = GATEWAY
        processes.append((name, _start(name, target, port, env)))
        if not _wait_ready(name, port, processes[-1][1]):
            print("ERROR: el gateway no arrancó; revisa su log arriba.")
            return 1

        print()
        print("PWA:  http://127.0.0.1:8000")
        print("Docs: http://127.0.0.1:8000/docs")
        print("Ctrl+C para detener los cuatro servicios.")
        print()

        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print()
                    print(f"{name} terminó con código {code}. Deteniendo el resto.")
                    return code or 1
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        print("Deteniendo servicios...")
        return 0
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
