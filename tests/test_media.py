"""MPRIS media pause during dictation (playerctl-backed, upstream
pause-if-playing / resume-what-we-paused semantics)."""
from __future__ import annotations

import copy

import pytest

from fluidvoice.config import DEFAULTS
from fluidvoice.media import MediaController


class FakePlayerctl:
    """playerctl double: players with statuses, records pause/play calls."""

    def __init__(self, players: dict[str, str]):
        self.players = dict(players)
        self.calls: list[str] = []

    def __call__(self, args, timeout=2.0):
        if args == ["-l"]:
            return "\n".join(self.players)
        # args: -p NAME status|pause|play
        name, cmd = args[1], args[2]
        if cmd == "status":
            return self.players.get(name, "")
        self.calls.append(f"{cmd}:{name}")
        if cmd == "pause":
            self.players[name] = "Paused"
        elif cmd == "play":
            self.players[name] = "Playing"
        return ""


@pytest.fixture()
def cfg():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def no_playerctl(monkeypatch):
    import fluidvoice.media as media
    monkeypatch.setattr(media.shutil, "which", lambda n: None)


@pytest.fixture()
def with_playerctl(monkeypatch):
    import fluidvoice.media as media
    monkeypatch.setattr(media.shutil, "which", lambda n: "/usr/bin/playerctl")
    return media


class TestPauseIfPlaying:
    def test_pauses_only_playing_players(self, with_playerctl, monkeypatch):
        fake = FakePlayerctl({"spotify": "Playing", "mpv": "Paused"})
        monkeypatch.setattr(with_playerctl, "_run", fake)
        m = MediaController()
        assert m.pause_if_playing() is True
        assert fake.calls == ["pause:spotify"]

    def test_resume_only_what_we_paused_and_only_if_still_paused(
            self, with_playerctl, monkeypatch):
        fake = FakePlayerctl({"spotify": "Playing"})
        monkeypatch.setattr(with_playerctl, "_run", fake)
        m = MediaController()
        m.pause_if_playing()
        # user manually resumed spotify before our dictation ended:
        # we must NOT resume again (double-resume guard, upstream behavior)
        fake.players["spotify"] = "Playing"
        m.resume()
        m.resume()  # memory cleared - still nothing
        assert fake.calls == ["pause:spotify"]

    def test_resume_is_once(self, with_playerctl, monkeypatch):
        fake = FakePlayerctl({"a": "Playing"})
        monkeypatch.setattr(with_playerctl, "_run", fake)
        m = MediaController()
        m.pause_if_playing()
        m.resume()
        m.resume()  # second call: nothing remembered
        assert fake.calls.count("play:a") == 1

    def test_nothing_playing_is_a_noop(self, with_playerctl, monkeypatch):
        fake = FakePlayerctl({"mpv": "Paused"})
        monkeypatch.setattr(with_playerctl, "_run", fake)
        m = MediaController()
        assert m.pause_if_playing() is False
        assert fake.calls == []

    def test_missing_playerctl_is_silent(self, no_playerctl):
        m = MediaController()
        assert m.pause_if_playing() is False
        m.resume()  # must not raise

    def test_playerctl_crash_is_swallowed(self, with_playerctl, monkeypatch):
        def boom(args, timeout=2.0):
            raise TimeoutError("playerctl hung")

        monkeypatch.setattr(with_playerctl, "_run", boom)
        m = MediaController()
        assert m.pause_if_playing() is False


class TestDaemonWiring:
    def test_pause_and_resume_across_a_dictation(self, cfg, quiet_ui,
                                                  monkeypatch):
        import fluidvoice.daemon as dm  # noqa: F401 (dm.Daemon below)
        from fluidvoice import media as cap_media
        from tests.test_daemon import StubRecorder

        calls = []
        monkeypatch.setattr(
            cap_media.MediaController, "pause_if_playing",
            lambda self: calls.append("pause") or True)
        monkeypatch.setattr(cap_media.MediaController, "resume",
                            lambda self: calls.append("resume"))
        cfg["recording"]["pause_media"] = True
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        d.backend = type("B", (), {"name": "stub"})()
        d.toggle()
        assert calls == ["pause"]
        d.cancel()
        assert calls == ["pause", "resume"]

    def test_disabled_via_config(self, cfg, quiet_ui, monkeypatch):
        import fluidvoice.daemon as dm  # noqa: F401 (dm.Daemon below)
        from fluidvoice import media as cap_media
        from tests.test_daemon import StubRecorder

        calls = []
        monkeypatch.setattr(
            cap_media.MediaController, "pause_if_playing",
            lambda self: calls.append("pause") or True)
        cfg["recording"]["pause_media"] = False
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: None,
                      use_hotkey=False, use_sounds=False)
        d.toggle()
        assert calls == []  # never invoked

    def test_config_default_on(self):
        assert DEFAULTS["recording"]["pause_media"] is True


from tests.test_daemon import quiet_ui  # noqa: E402,F401  (shared fixture)
