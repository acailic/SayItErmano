"""Sandbox daemon lifecycle + Background-portal autostart (#11).

Unit-tier coverage for the two code paths the Flatpak adds:

* ``fluidvoice.daemon_owner`` — the GTK app process owns the daemon
  inside the sandbox (no systemd user units): probe the control
  socket, spawn detached when dead, never touch host installs;
* ``fluidvoice.portal_background`` — login autostart replaces the
  systemd unit via the freedesktop Background portal; the sync facade
  never raises and reports failures for the Settings row.

No D-Bus daemon, no display, no processes: portal objects and the
spawner are fakes at the documented seams. (The Settings row itself
is gtkui code — that lane lives in tests/gtkui under needs_display.)
"""
from __future__ import annotations

import sys
import threading

import pytest

from fluidvoice import daemon_owner
from fluidvoice import portal_background as pb


class _FakePath:
    """Path stand-in for daemon_owner's sibling-script lookup — avoids
    monkeypatching pathlib globally."""

    def __init__(self, path: str, exists: set[str]):
        self._path = str(path)
        self._exists = exists

    @property
    def parent(self) -> "_FakePath":
        return _FakePath(self._path.rsplit("/", 1)[0] or "/",
                         self._exists)

    def __truediv__(self, other: str) -> "_FakePath":
        return _FakePath(f"{self._path}/{other}", self._exists)

    def is_file(self) -> bool:
        return self._path in self._exists

    def __str__(self) -> str:
        return self._path


class TestInFlatpak:
    def test_marker_env(self, monkeypatch):
        monkeypatch.setenv("SAYITERMANO_FLATPAK", "1")
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert daemon_owner.in_flatpak()

    def test_flatpak_id_env(self, monkeypatch):
        monkeypatch.delenv("SAYITERMANO_FLATPAK", raising=False)
        monkeypatch.setenv("FLATPAK_ID", "io.github.acailic.SayItErmano")
        assert daemon_owner.in_flatpak()

    def test_host_install_is_not_sandboxed(self, monkeypatch):
        monkeypatch.delenv("SAYITERMANO_FLATPAK", raising=False)
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert not daemon_owner.in_flatpak()


class TestEnsureDaemon:
    def test_host_install_is_a_no_op(self):
        spawned = []
        state = daemon_owner.ensure_daemon(
            spawn=spawned.append, alive=lambda: False,
            spawner_ok=lambda: False)
        assert state == "out-of-sandbox"
        assert spawned == []

    def test_running_daemon_is_left_alone(self):
        spawned = []
        state = daemon_owner.ensure_daemon(
            spawn=spawned.append, alive=lambda: True,
            spawner_ok=lambda: True)
        assert state == "alive"
        assert spawned == []

    def test_dead_daemon_is_spawned_with_daemon_argv(self):
        spawned = []
        state = daemon_owner.ensure_daemon(spawn=spawned.append,
                                           alive=lambda: False,
                                           spawner_ok=lambda: True)
        assert state == "started"
        assert len(spawned) == 1
        argv = spawned[0]
        assert isinstance(argv, list)
        assert argv[-1] == "daemon"

    def test_spawn_failure_is_reported_not_raised(self):
        def boom(argv):
            raise OSError("no space left")

        state = daemon_owner.ensure_daemon(spawn=boom, alive=lambda: False,
                                           spawner_ok=lambda: True)
        assert state == "spawn-failed: no space left"

    def test_poller_logs_when_the_socket_appears(self):
        logs = []
        calls = {"n": 0}

        def alive():
            calls["n"] += 1
            return calls["n"] > 2  # dead at spawn time, up 2 polls later

        state = daemon_owner.ensure_daemon(logs.append,
                                           spawn=lambda a: None,
                                           alive=alive,
                                           spawner_ok=lambda: True)
        assert state == "started"
        for _ in range(500):  # <= ~10 s of wall, exits early on the log
            if any("control socket up" in m for m in logs):
                break
            threading.Event().wait(0.02)
        assert any("control socket up" in m for m in logs), logs


class TestSpawnArgv:
    def test_prefers_the_sibling_console_script(self, monkeypatch):
        # the Flatpak layout: /app/bin/python3 next to /app/bin/sayit-ermano
        sibling = f"{sys.executable.rsplit('/', 1)[0]}/sayit-ermano"
        monkeypatch.setattr(
            daemon_owner, "Path",
            lambda p: _FakePath(str(p), exists={sibling}))
        assert daemon_owner._spawn_argv() == [sibling, "daemon"]

    def test_path_lookup_then_module_fallback(self, monkeypatch):
        monkeypatch.setattr(
            daemon_owner, "Path", lambda p: _FakePath(str(p), exists=set()))
        monkeypatch.setattr(daemon_owner.shutil, "which",
                            lambda name: f"/usr/bin/{name}"
                            if name == "sayit-ermano" else None)
        assert daemon_owner._spawn_argv() == ["/usr/bin/sayit-ermano",
                                              "daemon"]

        monkeypatch.setattr(daemon_owner.shutil, "which", lambda name: None)
        assert daemon_owner._spawn_argv() == \
            [sys.executable, "-m", "fluidvoice", "daemon"]


# -- Background portal ------------------------------------------------------


class _FakePortal:
    """Async stand-in at the portal_factory seam: records the request,
    answers from a scriptable outcome or raises a scriptable error."""

    def __init__(self, outcome=None, error=None, error_detail=""):
        self.outcome = outcome if outcome is not None else {
            "background": True, "autostart": True}
        self.error = error
        self.error_detail = error_detail
        self.requests: list[dict] = []
        self.closed = False

    async def connect(self):
        if self.error == "connect":
            raise pb.BackgroundPortalUnavailable(self.error_detail)

    async def close(self):
        self.closed = True

    async def request_background(self, **kwargs):
        if self.error == "request":
            raise pb.BackgroundPortalUnavailable(self.error_detail)
        self.requests.append(kwargs)
        return self.outcome


class TestSetAutostart:
    def test_enable_requests_the_daemon_commandline(self):
        portal = _FakePortal()
        ok, _detail = pb.set_autostart(True, portal_factory=lambda: portal)
        assert ok
        (request,) = portal.requests
        assert request["autostart"] is True
        assert request["commandline"] == ["sayit-ermano", "daemon"]
        assert portal.closed

    def test_disable_omits_the_commandline(self):
        portal = _FakePortal(outcome={"background": True, "autostart": False})
        ok, _detail = pb.set_autostart(False, portal_factory=lambda: portal)
        assert ok
        (request,) = portal.requests
        assert request["autostart"] is False
        # disabling sends no commandline — the backend clears its entry
        assert not request["commandline"]

    def test_refusal_is_reported_not_raised(self):
        portal = _FakePortal(error="request",
                             error_detail="portal request refused (code 1)")
        ok, detail = pb.set_autostart(True, portal_factory=lambda: portal)
        assert not ok
        assert "refused" in detail

    def test_connect_failure_maps_to_a_failure_detail(self):
        portal = _FakePortal(error="connect",
                             error_detail="No module named 'dbus_next'")
        ok, detail = pb.set_autostart(True, portal_factory=lambda: portal)
        assert not ok
        assert "dbus_next" in detail

    def test_grant_without_the_autostart_flag_is_a_refusal(self, monkeypatch):
        """The portal can only refuse a grant — a Response that lacks
        autostart=true must surface as a refusal, not a silent miss."""
        import asyncio
        import types

        fake_dbus_next = types.ModuleType("dbus_next")
        fake_dbus_next.Message = lambda **kw: kw
        fake_dbus_next.Variant = lambda sig, value: (sig, value)
        monkeypatch.setitem(sys.modules, "dbus_next", fake_dbus_next)

        portal = pb.BackgroundPortal()

        class _Reply:
            body = ["/org/freedesktop/portal/desktop/request/x"]

        async def fake_call(msg, timeout=None):
            return _Reply()

        async def fake_wait(path):
            return {"background": True, "autostart": False}

        portal._call = fake_call
        portal._await_response = fake_wait
        with pytest.raises(pb.BackgroundPortalUnavailable):
            asyncio.run(portal.request_background(
                reason="r", autostart=True,
                commandline=["sayit-ermano", "daemon"]))


class TestSandboxDataLocations:
    """Flatpak points the XDG env vars under ~/.var/app/<id>; the app
    must follow them so config/history/models land in the per-app home
    (first-run model download survives restarts there, and
    `flatpak uninstall --delete-data` reclaims everything)."""

    def test_paths_follow_the_flatpak_xdg_redirect(self, tmp_path,
                                                   monkeypatch):
        from fluidvoice import paths
        app_home = tmp_path / ".var" / "app" / "io.github.acailic.SayItErmano"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(app_home / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(app_home / "data"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(app_home / "cache"))
        monkeypatch.delenv("SAYITERMANO_CONFIG", raising=False)
        monkeypatch.delenv("SAYITERMANO_SOCKET", raising=False)

        assert paths.models_dir() == \
            app_home / "cache" / "sayit-ermano" / "models"
        assert paths.history_file().parent == \
            app_home / "data" / "sayit-ermano"
        assert paths.config_file().parent == \
            app_home / "config" / "sayit-ermano"


class TestAutostartStateSidecar:
    def test_round_trip(self, tmp_path):
        sidecar = tmp_path / "autostart-state.json"
        assert pb.load_autostart_state(sidecar) is False
        pb.save_autostart_state(True, sidecar)
        assert pb.load_autostart_state(sidecar) is True
        pb.save_autostart_state(False, sidecar)
        assert pb.load_autostart_state(sidecar) is False

    def test_missing_or_corrupt_file_reads_false(self, tmp_path):
        assert pb.load_autostart_state(tmp_path / "nope.json") is False
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert pb.load_autostart_state(bad) is False

    def test_save_failure_never_raises(self, tmp_path):
        # a directory in the file's place: the write must fail quietly
        pb.save_autostart_state(True, tmp_path)  # tmp_path IS a dir
        assert pb.load_autostart_state(tmp_path) is False
