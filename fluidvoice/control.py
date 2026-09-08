"""Unix-socket control channel (`sayit-ermano toggle|cancel|status`)."""
from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Callable

from . import paths


class ControlError(RuntimeError):
    pass


def _probe_live(path: Path) -> bool:
    """True when a daemon ANSWERS at path (read-only status probe)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(str(path))
            s.sendall(b'{"action": "status"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        return buf.strip().startswith(b"{")
    except OSError:
        return False


def serve(handler: Callable[[dict], dict], path: Path | None = None,
          ready: threading.Event | None = None) -> socket.socket:
    """Start a background thread serving JSON-line requests. Returns the socket."""
    path = path or paths.socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _probe_live(path):
        # never steal a socket a LIVE daemon is answering: sandboxed
        # second instances (isolated XDG_CONFIG_HOME, shared runtime dir)
        # would otherwise unlink the production daemon's control channel
        raise ControlError(
            f"another sayit-ermano daemon is answering at {path} - "
            "refusing to steal its control socket (point "
            "SAYITERMANO_SOCKET elsewhere for a second instance)")
    path.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(8)

    def _loop() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                break  # socket closed -> shutdown
            with conn:
                try:
                    conn.settimeout(10)
                    buf = b""
                    while b"\n" not in buf:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    if not buf.strip():
                        continue
                    try:
                        req = json.loads(buf.decode())
                    except json.JSONDecodeError:
                        resp = {"ok": False, "error": "invalid JSON"}
                    else:
                        try:
                            resp = dict(handler(req))
                        except Exception as e:  # noqa: BLE001
                            resp = {"ok": False, "error": str(e)}
                    conn.sendall(json.dumps(resp).encode() + b"\n")
                except OSError:
                    pass

    threading.Thread(target=_loop, name="fluidvoice-control", daemon=True).start()
    if ready is not None:
        ready.set()
    return srv


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
