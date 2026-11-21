"""In-sandbox daemon ownership (issue #11, ADR-0006): the pure
DaemonOwner logic against fake spawner/probe/watch callbacks — no GTK,
no D-Bus, no subprocess (the GTK wiring in gtkui/application.py is
three injected callbacks, exercised by the display lane).

Covers: spawn/skip decisions (host, suppress-env, live daemon, spawn
failure), the hold/child-watch/release lifecycle (including the
release-once invariant and re-ownership after the child is gone), and
shutdown terminating the child so it never outlives the owner.
"""
from __future__ import annotations

from fluidvoice.gtkui.daemon_owner import (
    SUPPRESS_ENV,
    DaemonOwner,
    in_flatpak,
)


class FakeProc:
    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.terminated = False
        self.waited = False
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self.waited = True
        self._alive = False
        return 0


class OwnerHarness:
    """DaemonOwner with every dependency faked and recorded."""

    def __init__(self, *, proc=None, live=False, sandboxed=True,
                 spawn_error=None):
        self.proc = proc or FakeProc()
        self.holds = 0
        self.releases = 0
        self.watched = []
        self.spawn_calls = 0
        self.probe_calls = 0
        self.logs = []
        self.owner = DaemonOwner(
            hold=self._hold, release=self._release,
            child_watch=self._watch,
            spawn=self._spawn, probe=self._probe,
            sandboxed=lambda: sandboxed,
            log=self.logs.append)
        self._live = live
        self._spawn_error = spawn_error

    def _hold(self):
        self.holds += 1

    def _release(self):
        self.releases += 1

    def _watch(self, pid, on_exit):
        self.watched.append((pid, on_exit))

    def _spawn(self):
        self.spawn_calls += 1
        if self._spawn_error:
            raise self._spawn_error
        return self.proc

    def _probe(self):
        self.probe_calls += 1
        return self._live

    def fire_child_exit(self):
        assert self.watched, "no child watch was registered"
        self.watched[0][1]()


class TestInFlatpak:
    def test_flapak_id_env(self, monkeypatch):
        monkeypatch.setenv("FLATPAK_ID", "io.github.acailic.SayItErmano")
        assert in_flatpak() is True

    def test_marker_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        marker = tmp_path / ".flatpak-info"
        marker.write_text("[Section]\n")
        assert in_flatpak(info_path=str(marker)) is True

    def test_host_is_not_flatpak(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert in_flatpak(info_path=str(tmp_path / "absent")) is False


class TestEnsureDecisions:
    def test_host_never_spawns(self):
        h = OwnerHarness(sandboxed=False)
        assert h.owner.ensure() is False
        assert h.spawn_calls == 0 and h.holds == 0

    def test_suppress_env_wins_even_in_sandbox(self, monkeypatch):
        monkeypatch.setenv(SUPPRESS_ENV, "1")
        h = OwnerHarness()
        assert h.owner.ensure() is False
        assert h.spawn_calls == 0

    def test_live_daemon_means_someone_else_owns_it(self):
        h = OwnerHarness(live=True)
        assert h.owner.ensure() is False
        assert h.spawn_calls == 0 and h.probe_calls == 1

    def test_spawn_failure_is_logged_not_raised(self):
        h = OwnerHarness(spawn_error=OSError("noexec"))
        assert h.owner.ensure() is False
        assert h.holds == 0 and h.logs

    def test_sandbox_spawns_holds_and_watches(self):
        h = OwnerHarness()
        assert h.owner.ensure() is True
        assert h.spawn_calls == 1 and h.holds == 1 and h.releases == 0
        assert h.watched and h.watched[0][0] == h.proc.pid
        assert h.owner.owns is True

    def test_ensure_twice_does_not_double_spawn(self):
        h = OwnerHarness()
        h.owner.ensure()
        assert h.owner.ensure() is True
        assert h.spawn_calls == 1 and h.holds == 1


class TestChildLifecycle:
    def test_child_exit_releases_the_hold_once(self):
        h = OwnerHarness()
        h.owner.ensure()
        h.fire_child_exit()
        h.fire_child_exit()  # watch must be one-shot in effect
        assert h.releases == 1 and h.owner.owns is False

    def test_reownership_after_child_death(self):
        h = OwnerHarness()
        h.owner.ensure()
        h.fire_child_exit()
        assert h.owner.ensure() is True  # next start owns it again
        assert h.spawn_calls == 2 and h.holds == 2

    def test_shutdown_terminates_and_releases(self):
        h = OwnerHarness()
        h.owner.ensure()
        h.owner.shutdown()
        assert h.proc.terminated and h.proc.waited
        assert h.releases == 1 and h.owner.owns is False

    def test_shutdown_without_child_is_a_noop(self):
        h = OwnerHarness()
        h.owner.shutdown()
        assert h.releases == 0

    def test_shutdown_after_child_exit_is_safe(self):
        h = OwnerHarness()
        h.owner.ensure()
        h.fire_child_exit()
        h.owner.shutdown()  # second sweep on a dead child
        assert h.releases == 1
