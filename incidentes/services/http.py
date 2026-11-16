"""Cliente HTTP mínimo (stdlib) para las llamadas entre servicios."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


def get_json(url: str, timeout: float = 5.0) -> Any:
    """GET y decodifica JSON. Puede lanzar."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def post_json(url: str, payload: dict[str, Any], timeout: float = 5.0) -> Any:
    """POST JSON y decodifica la respuesta. Puede lanzar."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def delete(url: str, timeout: float = 2.0) -> None:
    """DELETE best-effort. Puede lanzar."""
    request = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()
