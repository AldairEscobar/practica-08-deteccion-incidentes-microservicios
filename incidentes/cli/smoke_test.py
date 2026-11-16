"""Prueba de humo del stack: comprueba gateway, cámaras, inferencia y alertas.

Requiere que el stack esté arrancado::

    python -m incidentes.cli.stack

En otra terminal::

    python -m incidentes.cli.smoke_test
"""

from __future__ import annotations

import json
import sys
import urllib.request

from incidentes.services.settings import GATEWAY_URL


def _get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, json.loads(response.read().decode())


def main() -> int:
    try:
        code, health = _get(f"{GATEWAY_URL}/health")
    except Exception as exc:
        print(f"ERROR: el gateway no responde en {GATEWAY_URL}: {exc}")
        print("Arranca el stack con: python -m incidentes.cli.stack")
        return 1

    print(f"Gateway {GATEWAY_URL}: HTTP {code}, estado={health.get('status')}")
    services = health.get("servicios", {})
    for name, info in services.items():
        print(f"  {name:<10} {info.get('status')}")

    try:
        _, info = _get(f"{GATEWAY_URL}/api/v1/info")
        print(f"Versión {info.get('version')} · PWA en {info.get('pwa')}")
    except Exception as exc:
        print(f"AVISO: /api/v1/info no responde: {exc}")

    if health.get("status") != "ok":
        print("ERROR: algún servicio está caído; revisa las terminales del stack.")
        return 1
    print("OK: los tres servicios internos responden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
