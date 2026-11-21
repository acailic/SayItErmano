"""In-sandbox daemon lifecycle (ADR-0006 / issue #11).

Inside the Flatpak sandbox there is no systemd user instance to run the
daemon unit, so the GTK app process owns the daemon: on startup
``ensure_daemon`` checks the control socket (ADR-0001) and spawns a
detached ``sayit-ermano daemon`` when nothing is listening. The
daemon's flock singleton (``cli._acquire_daemon_lock``) makes races
harmless — e.g. the login autostart (Background portal,
``command=["daemon"]``) racing the user opening Settings: one of the
two daemons exits immediately, exactly like the deb's autostart +
systemd pair at login.

Host installs are untouched: ``ensure_daemon`` is a no-op unless
``in_flatpak()`` (the systemd user unit / /etc/xdg/autostart own the
daemon there).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from . import paths
from .control_server import probe_live

# The spawned daemon binds its control socket only AFTER startup (model
# preload, hotkey, tray — see Daemon.run), and a first run downloads a
# model first, so "started" cannot mean "socket visible": the poller
# thread just logs when the socket appears; callers that need the
# daemon use the existing degraded-mode recovery in gtkui.client.
POLL_INTERVAL_S = 0.5


def in_flatpak() -> bool:
    """True inside the Flatpak sandbox. The manifest sets
    SAYITERMANO_FLATPAK=1 (finish-args); FLATPAK_ID is set by flatpak
    itself and also covers dev/override runs."""
    return (os.environ.get("SAYITERMANO_FLATPAK") == "1"
            or bool(os.environ.get("FLATPAK_ID")))


def daemon_alive(path: Path | None = None) -> bool:
    return probe_live(path or paths.socket_path())


def _spawn_argv() -> list[str]:
    """The daemon command for THIS install: the console script next to
    the interpreter (the Flatpak's /app/bin) → PATH → module fallback
    (mirrors session.toggle_command's resolution)."""
    sibling = Path(sys.executable).parent / "sayit-ermano"
    if sibling.is_file():
        return [str(sibling), "daemon"]
    found = shutil.which("sayit-ermano")
    if found:
        return [found, "daemon"]
    return [sys.executable, "-m", "fluidvoice", "daemon"]


def ensure_daemon(log: Callable[[str], None] = (lambda m: None),
                  *, spawn: Callable[[list[str]], object] | None = None,
                  alive: Callable[[], bool] | None = None,
                  spawner_ok: Callable[[], bool] = in_flatpak) -> str:
    """Start the dictation daemon if it is not running (sandbox only).

    Returns the end state:

    - ``"alive"``        — daemon already answering on the socket
    - ``"started"``     — spawn issued; a poller thread logs when the
                           socket comes up (first runs download a model,
                           so this can legitimately take minutes)
    - ``"out-of-sandbox"`` — host install; the unit/autostart owns it
    - ``"spawn-failed: …"`` — the spawn itself failed

    ``spawn``/``alive`` are override seams for tests; ``spawner_ok``
    gates the whole behavior (default: ``in_flatpak``).
    """
    if not spawner_ok():
        return "out-of-sandbox"
    is_alive = alive or daemon_alive
    if is_alive():
        return "alive"
    argv = _spawn_argv()
    try:
        if spawn is not None:
            spawn(argv)
        else:
            # Detached: the daemon outlives the Settings window; the
            # sandbox stays up while it runs (flatpak keeps it alive
            # for the app lifetime, the same role the unit plays on
            # host installs).
            subprocess.Popen(
                argv, start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001 - reported, never fatal to the app
        log(f"WARN could not start the daemon: {e}")
        return f"spawn-failed: {e}"

    def _poll() -> None:
        while True:
            try:
                if is_alive():
                    log("daemon ready (control socket up)")
                    return
            except Exception:  # noqa: S110 — keep polling; best-effort log
                pass
            threading.Event().wait(POLL_INTERVAL_S)

    threading.Thread(target=_poll, name="fluidvoice-daemon-owner",
                     daemon=True).start()
    return "started"
