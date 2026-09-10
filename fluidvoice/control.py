"""Unix-socket control channel (`sayit-ermano toggle|cancel|status`).

Server side now lives in ``control_server.ControlServer`` (one accept
thread + eight workers, bounded/validated, deterministic shutdown);
this module keeps the historical ``serve()``/``request()`` API - and the
byte-identical JSON-line protocol - for the daemon, CLI and tests.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path
from threading import Event
from typing import Callable

from . import paths
from .control_server import (
    IDLE_TIMEOUT_S,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    SOCKET_MODE,
    WORKERS,
    ControlError,
    ControlServer,
    probe_live,
)

__all__ = ["ControlError", "ControlServer", "serve", "request",
           "probe_live", "MAX_REQUEST_BYTES", "MAX_RESPONSE_BYTES",
           "IDLE_TIMEOUT_S", "SOCKET_MODE", "WORKERS"]


def serve(handler: Callable[[dict], dict], path: Path | None = None,
          ready: Event | None = None) -> ControlServer:
    """Start a background ControlServer serving JSON-line requests.

    Returns the server: ``shutdown()`` (or the socket-style ``close()``)
    stops it deterministically, joining the accept thread and every
    worker. Callers that only ever called ``.close()`` on the old raw
    listening socket keep working unchanged."""
    server = ControlServer(handler, path or paths.socket_path())
    server.start(ready=ready)
    return server


def request(action: str, **kwargs) -> dict:
    """Send one command to a running daemon."""
    path = paths.socket_path()
    if not path.exists():
        raise ControlError(f"daemon not running (no socket at {path}) - start it with `fluidvoice daemon`")
    payload = dict(kwargs, action=action)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(15)
        try:
            sock.connect(str(path))
        except (ConnectionRefusedError, OSError) as e:
            raise ControlError(f"cannot reach daemon: {e}") from e
        sock.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    if not buf.strip():
        raise ControlError("empty response from daemon")
    return json.loads(buf.decode())
