"""In-sandbox daemon ownership (ADR-0006, issue #11).

Host installs run the daemon from a systemd user unit or the packaged
XDG autostart entry. A Flatpak sandbox has neither, so the GTK app
process OWNS the daemon there: it spawns ``fluidvoice daemon`` as a
child, holds the GApplication alive for exactly as long as the child
lives, and terminates it on app shutdown. The daemon's own singleton
lock (cli._acquire_daemon_lock) plus the socket probe below keep a
manually-started ``flatpak run --command=sayit-ermano <id> daemon``
from racing this path.

Login autostart is the Background portal's job (portal_autostart.py):
the portal writes the host-side ``~/.config/autostart`` entry, which
launches ``sayit-ermano app --background`` — the held, windowless
primary instance this module supervises from.

This module is deliberately gi-free (hold/release/child-watch are
injected callbacks) so the ownership logic runs headless in the unit
tier; the GTK wiring lives in gtkui/application.py.
"""
from __future__ import annotations

import os
import subprocess
import sys

#: Headless/tests escape hatch, mirroring daemon._spawn_app's
#: SAYITERMANO_NO_APP_SPAWN.
SUPPRESS_ENV = "SAYITERMANO_NO_DAEMON_SPAWN"

#: stderr/stdout are inherited so the daemon's logs follow the owner
#: process (journal under the autostart unit, terminal when manual).
DAEMON_ARGV = [sys.executable, "-m", "fluidvoice", "daemon"]


def in_flatpak(info_path: str = "/.flatpak-info") -> bool:
    """True inside a Flatpak sandbox: FLATPAK_ID is always set there;
    the marker file catches the no-env corner."""
    return bool(os.environ.get("FLATPAK_ID")) or os.path.exists(info_path)


def daemon_live() -> bool:
    """A daemon answers on the control socket (read-only status probe,
    the same one doctor uses)."""
    from ..control_server import probe_live
    from ..paths import socket_path
    return probe_live(socket_path())


def _spawn_daemon() -> subprocess.Popen:
    return subprocess.Popen(DAEMON_ARGV)


class DaemonOwner:
    """Spawn-and-supervise the daemon child from the app process.

    ``child_watch(pid, on_exit)`` schedules ``on_exit()`` once when the
    child terminates (GLib.child_watch_add in the GTK wiring). The
    hold/release pair keeps the owning GApplication resident while the
    daemon child lives and lets it exit when the child is gone.
    """

    def __init__(self, *, hold, release, child_watch,
                 spawn=_spawn_daemon, probe=daemon_live,
                 sandboxed=in_flatpak, log=None) -> None:
        self._hold = hold
        self._release = release
        self._child_watch = child_watch
        self._spawn = spawn
        self._probe = probe
        self._sandboxed = sandboxed
        self.log = log or (lambda m: print(f"[sayit-ermano] {m}", flush=True))
        self._proc: subprocess.Popen | None = None
        self._released = True

    @property
    def owns(self) -> bool:
        """True while this process owns a live daemon child."""
        return self._proc is not None and self._proc.poll() is None

    def ensure(self) -> bool:
        """Start owning the daemon if that is this process's job.

        Returns True when a child was spawned. No-op outside the
        sandbox (host installs keep their unit/autostart daemon), when
        suppressed, and when a daemon already answers on the socket.
        """
        if self._proc is not None:
            return self.owns
        if os.environ.get(SUPPRESS_ENV):
            return False
        if not self._sandboxed():
            return False
        if self._probe():
            return False  # someone else owns it already
        try:
            self._proc = self._spawn()
        except Exception as e:  # noqa: BLE001 — ownership is best-effort
            self.log(f"WARN could not start the daemon: {e}")
            return False
        self._released = False
        self._hold()
        self._child_watch(self._proc.pid, self._child_gone)
        self.log("owning the dictation daemon (flatpak lifecycle)")
        return True

    def _child_gone(self) -> None:
        self._proc = None
        if not self._released:
            self._released = True
            self._release()

    def shutdown(self) -> None:
        """Terminate the child so it never outlives the owner app."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):  # noqa: S110 — teardown
            pass  # already dead, or refusing: the session scope reaps it
        self._child_gone()
