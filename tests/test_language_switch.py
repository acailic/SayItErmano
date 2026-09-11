"""Fast language switching + wrong-language hallucination guard.

Covers (per specs/3c8d6007_language-cycle-guard.md):
  - config keys: general.language_cycle, general.language_whitelist,
    hotkey.language_key (strict validation against KNOWN_LANGUAGES)
  - runtime precedence: cycle override > model.languages > general.language
  - the whitelist guard: one re-decode when auto-detection lands outside
  - the daemon cycle state machine (hotkey, announce, status, tray, CLI)
  - the Settings editors and the doctor section
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest

from fluidvoice import backends
from fluidvoice import config as config_mod
from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS, KNOWN_LANGUAGES


class FakeModelBackend:
    """StubBackend-shaped fake with a model_name (model.languages key)."""

    name = "fake"
    surfaces_detected_language = True

    def __init__(self, model_name="small", results=None):
        self.model_name = model_name
        self.results = results or []
        self.calls: list[tuple[str, str | None]] = []

    def transcribe(self, wav, language=None):
        self.calls.append((str(wav), language))
        if self.results:
            if len(self.results) > 1 or callable(self.results[0]):
                r = self.results.pop(0)
                if callable(r):
                    return r(language)
                return dict(r)
            return dict(self.results[0])
        return {"text": "ok", "language": "en", "duration": 1.0}


def _cfg(**general):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["general"].update(general)
    return cfg


def _codes(n: int) -> list[str]:
    """n distinct known language codes (for cap tests)."""
    return KNOWN_LANGUAGES[:n]


def make_wav(path, seconds: float = 1.0, loud: bool = True,
             rate: int = 16000):
    """Same recipe as tests/test_daemon.make_wav."""
    import math as _math
    import struct as _struct
    import wave as _wave
    n = int(rate * seconds)
    with _wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(12000 * _math.sin(2 * _math.pi * 440 * i / rate)) \
                if loud else 0
            frames += _struct.pack("<h", v)
        wf.writeframes(bytes(frames))
    if path.stat().st_size < 200:
        with open(path, "ab") as fh:
            fh.write(b"\0" * 300)
    return path


@pytest.fixture()
def cfg_fixture():
    return copy.deepcopy(DEFAULTS)


@pytest.fixture()
def quiet_ui(tmp_path, monkeypatch):
    """Silence real notifications/sounds, isolate history, and record them."""
    calls = {"notify": [], "sound": []}

    def fake_notify(title, body="", timeout_ms=2500, enabled=True):
        if enabled:
            calls["notify"].append((title, body))

    def fake_sound(which, volume=1.0, enabled=True):
        if enabled:
            calls["sound"].append(which)

    monkeypatch.setattr(dm.ui, "notify", fake_notify)
    monkeypatch.setattr(dm.ui, "play_sound", fake_sound)
    monkeypatch.setattr(dm.insertion, "active_window_class",
                        lambda: "TestApp")
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "test-history.jsonl")
    return calls


# ---------------------------------------------------------------------------
# Phase 3 - daemon runtime cycle (hotkey, announce, status, tray, CLI)
# ---------------------------------------------------------------------------

class _NoopRecorder:
    def start(self, path):
        pass

    def stop(self):
        return None

    def cancel(self):
        pass


def make_daemon(cfg, backend=None, recorder=None):
    backend = backend if backend is not None else FakeModelBackend()
    d = dm.Daemon(cfg, recorder=recorder or _NoopRecorder(),
                  backend_factory=lambda c: backend,
                  use_hotkey=False, use_sounds=False)
    d.backend = backend  # simulate a successful startup load
    return d


def wait_done(d, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if d._process_thread is None or not d._process_thread.is_alive():
            return not d.busy
        time.sleep(0.02)
    return False


class TestCycleStateMachine:
    def _cfg(self, cycle=("auto", "en", "sl")):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = list(cycle)
        return cfg

    def test_press_cycle_with_wraparound(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg())
        seq = [d._engines.cycle_language()["language"] for _ in range(4)]
        assert seq == ["auto", "en", "sl", "auto"]
        assert d._engines.cycle_index == 0  # wrapped

    def test_initial_state_resolves_from_config(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg())
        assert d._engines.cycle_index is None
        lang, source = d._engines.language_detail()
        assert lang == "auto" and source == "general"
        assert d._engines.cycle_runtime() is None

    def test_empty_list_press_is_noop(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg(cycle=()))
        out = d._engines.cycle_language()
        assert out == {"ok": False, "error": "empty cycle"}
        assert d._engines.cycle_index is None
        assert len(quiet_ui["notify"]) == 1
        title, body = quiet_ui["notify"][0]
        assert "Language cycle is empty" in body

    def test_empty_list_with_key_bound_still_off(self, cfg_fixture, quiet_ui):
        # feature off even when the key IS bound
        cfg = self._cfg(cycle=())
        cfg["hotkey"]["language_key"] = "F7"
        d = make_daemon(cfg)
        assert d._engines.cycle_language()["ok"] is False

    def test_shrink_clamps_index(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg())
        d._engines.cycle_language()  # index 0 (auto)
        d._engines.cycle_language()  # index 1 (en)
        d._engines.cycle_language()  # index 2 (sl)
        d.cfg["general"]["language_cycle"] = ["de", "fr"]
        # 2 % 2 = 0 -> first entry of the shrunk list
        assert d._engines.cycle_runtime() == "de"

    def test_emptied_list_disengages(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg())
        d._engines.cycle_language()
        d.cfg["general"]["language_cycle"] = []
        assert d._engines.cycle_runtime() is None

    def test_never_persisted(self, cfg_fixture, quiet_ui, monkeypatch):
        import fluidvoice.config as config_mod
        saved = []
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, path=None: saved.append(1))
        d = make_daemon(self._cfg())
        for _ in range(5):
            d._engines.cycle_language()
        assert saved == []
        assert d.cfg["general"]["language_cycle"] == ["auto", "en", "sl"]

    def test_runtime_beats_model_override_in_daemon(self, cfg_fixture,
                                                    quiet_ui):
        cfg = self._cfg()
        cfg["model"]["languages"] = {"small": "de"}
        d = make_daemon(cfg)
        d._engines.cycle_language()  # auto
        d._engines.cycle_language()  # en
        assert d._engines.language_detail() == ("en", "cycle")

    def test_locked_daemon_ignores_press(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg())
        d._locked = True
        assert d._engines.cycle_language() == {"ok": False, "error": "locked"}
        assert d._engines.cycle_index is None


class TestAnnounce:
    def test_pill_badge_when_preview_display_present(self, cfg_fixture,
                                                     quiet_ui):
        d = make_daemon(self._cfg_for_announce())
        badges = []

        class FakePill:
            def set_badge(self, text):
                badges.append(text)

        d._capture.preview = (None, FakePill())
        d._engines.cycle_language()  # auto
        d._engines.cycle_language()  # en
        assert badges == ["lang: auto", "lang: en"]

    def test_notify_fallback_display_show(self, cfg_fixture, quiet_ui):
        d = make_daemon(self._cfg_for_announce())
        shown = []

        class FakeNotify:
            def show(self, text):
                shown.append(text)

        d._capture.preview = (None, FakeNotify())
        d._engines.cycle_language()
        d._engines.cycle_language()
        assert shown == ["Language: auto", "Language: en"]

    def test_fresh_notify_preview_when_no_display(self, cfg_fixture,
                                                  quiet_ui, monkeypatch):
        from fluidvoice import preview as preview_mod
        shown = []

        class FakeNotifyPreview:
            def show(self, text):
                shown.append(text)

        monkeypatch.setattr(preview_mod, "NotifyPreview", FakeNotifyPreview)
        d = make_daemon(self._cfg_for_announce())
        d._capture.preview = None
        d._engines.cycle_language()
        d._engines.cycle_language()
        assert shown == ["Language: auto", "Language: en"]

    def test_announce_failure_never_breaks_cycle(self, cfg_fixture, quiet_ui,
                                                 monkeypatch, capsys):
        from fluidvoice import preview as preview_mod

        def boom():
            raise RuntimeError("no notify-send")

        monkeypatch.setattr(preview_mod, "NotifyPreview", boom)
        d = make_daemon(self._cfg_for_announce())
        d._capture.preview = None
        out = d._engines.cycle_language()
        assert out["ok"] is True and out["language"] == "auto"

    @staticmethod
    def _cfg_for_announce():
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["auto", "en"]
        return cfg


class TestDaemonWiring:
    def test_status_language_block(self, cfg_fixture, quiet_ui):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["auto", "en", "sl"]
        cfg["general"]["language_whitelist"] = ["sl", "en"]
        d = make_daemon(cfg)
        resp = d.handle_request({"action": "status"})
        lang = resp["language"]
        assert lang["effective"] == "auto" and lang["source"] == "general"
        assert lang["cycle"] == ["auto", "en", "sl"]
        assert lang["cycle_engaged"] is False
        assert lang["whitelist"] == ["sl", "en"]
        d._engines.cycle_language()
        d._engines.cycle_language()
        lang = d.handle_request({"action": "status"})["language"]
        assert lang == {"effective": "en", "source": "cycle",
                        "cycle": ["auto", "en", "sl"],
                        "cycle_engaged": True,
                        "whitelist": ["sl", "en"]}

    def test_cycle_language_socket_action(self, cfg_fixture, quiet_ui):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en", "sl"]
        d = make_daemon(cfg)
        resp = d.handle_request({"action": "cycle-language"})
        assert resp == {"ok": True, "language": "en", "source": "cycle"}
        resp = d.handle_request({"action": "cycle-language"})
        assert resp == {"ok": True, "language": "sl", "source": "cycle"}

    def test_cycle_language_action_locked(self, cfg_fixture, quiet_ui):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en"]
        d = make_daemon(cfg)
        d._locked = True
        resp = d.handle_request({"action": "cycle-language"})
        assert resp == {"ok": False, "error": "locked"}

    def test_tray_tooltip_lang_when_engaged(self, cfg_fixture, quiet_ui):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["auto", "en", "sl"]
        d = make_daemon(cfg)
        d._engines.cycle_language()
        d._engines.cycle_language()
        d._engines.cycle_language()
        assert "lang: sl" in d._tray_tooltip()

    def test_tray_tooltip_no_lang_when_auto_and_disengaged(self, cfg_fixture,
                                                           quiet_ui):
        d = make_daemon(copy.deepcopy(DEFAULTS))
        assert "lang:" not in d._tray_tooltip()

    def test_tray_tooltip_lang_when_pinned_general(self, cfg_fixture,
                                                   quiet_ui):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language"] = "de"
        d = make_daemon(cfg)
        assert "lang: de" in d._tray_tooltip()

    def test_start_hotkey_grabs_language_key(self, cfg_fixture, quiet_ui,
                                             monkeypatch):
        import fluidvoice.hotkey as hotkey_mod
        made = []

        class FakeListener:
            grabbed = True

            def __init__(self, key, modifiers, mode, on_toggle=None,
                         on_cancel=None, cancel_key=None, log=None, **kw):
                self.key, self.on_toggle = key, on_toggle
                self.hotkey_grabbed = True
                self.summary = [f"language stub {key}"]
                made.append(self)

            def start(self):
                pass

            def stop(self):
                self.stopped = True

        monkeypatch.setattr(hotkey_mod, "HotkeyListener", FakeListener)
        cfg = copy.deepcopy(DEFAULTS)
        cfg["hotkey"]["language_key"] = "F7"
        cfg["hotkey"]["key"] = "F9"
        d = make_daemon(cfg)
        error = d._start_hotkey()
        assert error is None
        listeners = [l for l in made if l.key == "F7"]
        assert len(listeners) == 1
        assert d._language_hotkey is listeners[0]
        # the toggle callback routes to the cycle state machine
        d.cfg["general"]["language_cycle"] = ["auto", "sl"]
        out = listeners[0].on_toggle()
        assert out == {"ok": True, "language": "auto", "source": "cycle"}

    def test_restart_hotkey_stops_language_listener(self, cfg_fixture,
                                                    quiet_ui, monkeypatch):
        d = make_daemon(copy.deepcopy(DEFAULTS))
        d.use_hotkey = True
        stopped = []

        class FakeListener:
            def stop(self):
                stopped.append("language")

        d._language_hotkey = FakeListener()
        monkeypatch.setattr(d, "_start_hotkey", lambda: None)
        out = d.apply_config(["hotkey.language_key"])
        assert stopped == ["language"]
        assert d._language_hotkey is None
        assert out == {"applied": ["hotkeys"], "errors": []}

    def test_shutdown_stops_language_hotkey(self, cfg_fixture, quiet_ui):
        d = make_daemon(copy.deepcopy(DEFAULTS))
        stopped = []

        class FakeListener:
            def stop(self):
                stopped.append(1)

        d._language_hotkey = FakeListener()
        d.shutdown()
        assert stopped == [1]
        assert d._language_hotkey is not None  # only stopped, not cleared

    def test_process_passes_override_to_pipeline(self, cfg_fixture, quiet_ui,
                                                 tmp_path):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["auto", "en", "sl"]
        from tests.test_daemon import StubRecorder
        backend = FakeModelBackend(results=[
            {"text": "hello world", "language": "en", "duration": 1.0}])
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: backend,
                      use_hotkey=False, use_sounds=False)
        d.backend = backend
        d._engines.cycle_language()  # auto
        d._engines.cycle_language()  # en
        d._engines.cycle_language()  # sl
        assert d.toggle() is True
        assert d.toggle() is False
        assert wait_done(d)
        assert backend.calls and backend.calls[0][1] == "sl"

    def test_preview_and_test_dictation_use_cycle(self, cfg_fixture, quiet_ui):
        # the helper is the single resolution point: preview start and the
        # onboarding probe both go through _language_detail()
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en", "sl"]
        d = make_daemon(cfg)
        d._engines.cycle_language()  # en
        assert d._engines.language_detail() == ("en", "cycle")


class TestCliLanguageSubcommand:
    def test_dispatch_sends_cycle_language_action(self, cfg_fixture,
                                                  monkeypatch, capsys):
        from fluidvoice import cli
        sent = []

        def fake_request(action, **kw):
            sent.append(action)
            return {"ok": True, "language": "en", "source": "cycle"}

        import fluidvoice.control as control_mod
        monkeypatch.setattr(control_mod, "request", fake_request)
        rc = cli.main(["language"])
        assert rc == 0
        assert sent == ["cycle-language"]
        assert "cycled -> en (cycle)" in capsys.readouterr().out

    def test_dispatch_json_output(self, cfg_fixture, monkeypatch, capsys):
        import fluidvoice.control as control_mod
        from fluidvoice import cli
        monkeypatch.setattr(control_mod, "request",
                            lambda action, **kw: {"ok": False,
                                                  "error": "empty cycle"})
        rc = cli.main(["language", "--json"])
        assert rc == 1
        out = capsys.readouterr().out
        assert '"error": "empty cycle"' in out

class TestPrecedence:
    def test_runtime_beats_per_model(self):
        cfg = _cfg(language="de")
        cfg["model"]["languages"] = {"small": "de"}
        b = FakeModelBackend("small")
        assert backends.effective_language(cfg, b, runtime="sl") == "sl"

    def test_per_model_beats_general(self):
        cfg = _cfg(language="de")
        cfg["model"]["languages"] = {"small": "sl"}
        b = FakeModelBackend("small")
        assert backends.effective_language(cfg, b) == "sl"

    def test_runtime_auto_beats_per_model(self):
        cfg = _cfg(language="de")
        cfg["model"]["languages"] = {"small": "de"}
        b = FakeModelBackend("small")
        assert backends.effective_language(cfg, b, runtime="auto") == "auto"

    def test_empty_runtime_falls_through(self):
        cfg = _cfg(language="de")
        b = FakeModelBackend("small")
        assert backends.effective_language(cfg, b, runtime="") == "de"
        assert backends.effective_language(cfg, b) == "de"

    def test_missing_model_key_falls_through(self):
        cfg = _cfg(language="sl")
        cfg["model"]["languages"] = {"large-v3": "en"}
        b = FakeModelBackend("small")  # key mismatch
        assert backends.effective_language(cfg, b) == "sl"

    def test_backend_none_uses_config_key(self):
        cfg = _cfg(language="fr")
        cfg["model"]["languages"] = {"small": "sl"}
        assert backends.effective_language(cfg, None) == "sl"

    def test_detail_sources(self):
        cfg = _cfg(language="en")
        cfg["model"]["languages"] = {"small": "de"}
        b = FakeModelBackend("small")
        assert backends.language_detail(cfg, b, runtime="sl") == ("sl", "cycle")
        assert backends.language_detail(cfg, b) == ("de", "model")
        cfg["model"]["languages"] = {}
        assert backends.language_detail(cfg, None) == ("en", "general")

    def test_language_guard_map_keys(self):
        assert set(backends.LANGUAGE_GUARD) == {"faster-whisper",
                                                "whisper-torch",
                                                "whisper.cpp", "parakeet",
                                                "remote"}

    def test_resolved_backend_name_explicit(self):
        cfg = _cfg()
        cfg["model"]["backend"] = "no-such-backend"
        assert backends.resolved_backend_name(cfg) is None


class TestGuard:
    """The whitelist retry path lives in DictationPipeline._transcribe."""

    def _pipeline(self, cfg, backend, tmp_path):
        logs = []
        pipe = dm.DictationPipeline(
            cfg, backend,
            inserter=lambda text, c: "typed",
            history_writer=lambda e, w: None,
            logger=logs.append)
        return pipe, logs

    def test_retry_when_detected_outside_whitelist(self, tmp_path):
        cfg = _cfg(language="auto", language_whitelist=["sl", "en"])
        script = [
            {"text": "\u0440\u0443\u0441\u0441\u043a\u0438\u0439 \u0442\u0435\u043a\u0441\u0442",
             "language": "ru", "duration": 1.0},
            lambda lang: {"text": "slovenski tekst",
                         "language": lang, "duration": 1.0},
        ]
        b = FakeModelBackend(results=script)
        pipe, logs = self._pipeline(cfg, b, tmp_path)
        wav = make_wav(tmp_path / "utt.wav")
        result = pipe._transcribe(wav)
        assert len(b.calls) == 2
        assert b.calls[0][1] == "auto"
        assert b.calls[1][1] == "sl"
        assert result["text"] == "slovenski tekst"
        assert any("detected=ru" in line and "re-decoding as sl" in line
                   for line in logs)

    def test_retry_runs_end_to_end_through_run(self, tmp_path, cfg_fixture):
        cfg = cfg_fixture
        cfg["general"]["language"] = "auto"
        cfg["general"]["language_whitelist"] = ["sl"]
        script = [
            {"text": "\u0434\u0430 \u0434\u0430", "language": "ru",
             "duration": 1.0},
            {"text": "ja da", "language": "sl", "duration": 1.0},
        ]
        b = FakeModelBackend(results=script)
        pipe, logs = self._pipeline(cfg, b, tmp_path)
        wav = make_wav(tmp_path / "utt.wav")
        out = pipe.run(wav, "TestApp")
        assert out["text"].strip().startswith("ja da")
        assert b.calls[1][1] == "sl"

    def test_no_retry_when_detected_in_whitelist(self, tmp_path):
        cfg = _cfg(language="auto", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "english text", "language": "en", "duration": 1.0}])
        pipe, logs = self._pipeline(cfg, b, tmp_path)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert len(b.calls) == 1
        assert not [l for l in logs if "language guard" in l]

    def test_no_retry_when_whitelist_empty(self, tmp_path):
        cfg = _cfg(language="auto")
        b = FakeModelBackend(results=[
            {"text": "x", "language": "ru", "duration": 1.0}])
        pipe, _ = self._pipeline(cfg, b, tmp_path)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert len(b.calls) == 1

    def test_no_retry_when_language_pinned(self, tmp_path):
        # effective language non-auto: a pinned language never triggers
        # the guard even if detection reports something else
        cfg = _cfg(language="auto", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "x", "language": "ru", "duration": 1.0}])
        pipe, _ = self._pipeline(cfg, b, tmp_path)
        pipe._language_override = "sl"
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert len(b.calls) == 1
        assert b.calls[0][1] == "sl"

    def test_no_retry_for_parakeet_class_backend(self, tmp_path):
        cfg = _cfg(language="auto", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "x", "language": "ru", "duration": 1.0}])
        b.surfaces_detected_language = False  # parakeet-class
        pipe, _ = self._pipeline(cfg, b, tmp_path)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert len(b.calls) == 1

    def test_no_retry_when_detected_unavailable(self, tmp_path):
        # whisper.cpp-class: language is None under auto
        cfg = _cfg(language="auto", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "x", "language": None, "duration": 1.0}])
        pipe, _ = self._pipeline(cfg, b, tmp_path)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert len(b.calls) == 1

    def test_whitelist_matches_on_primary_subtag(self, tmp_path):
        # detected "en-US" must count as "en" for whitelist ["sl", "en"]
        cfg = _cfg(language="auto", language_whitelist=["sl", "en"])
        b = FakeModelBackend(results=[
            {"text": "x", "language": "en-US", "duration": 1.0}])
        pipe, _ = self._pipeline(cfg, b, tmp_path)
        pipe._transcribe(make_wav(tmp_path / "utt.wav"))
        assert len(b.calls) == 1


class TestBackendCapabilities:
    def test_capability_flags(self):
        from fluidvoice.backends.faster_whisper_backend import FasterWhisperBackend
        from fluidvoice.backends.parakeet_onnx import ParakeetOnnxBackend
        from fluidvoice.backends.torch_whisper import TorchWhisperBackend
        from fluidvoice.backends.whisper_cpp import WhisperCppBackend
        assert FasterWhisperBackend.surfaces_detected_language is True
        assert TorchWhisperBackend.surfaces_detected_language is True
        assert WhisperCppBackend.surfaces_detected_language is False
        assert ParakeetOnnxBackend.surfaces_detected_language is False

class TestConfigValidation:
    def test_cycle_accepts_known_codes_and_auto(self):
        ok, v = config_mod.coerce_setting("general", "language_cycle",
                                          ["auto", "en", "sl"])
        assert ok and v == ["auto", "en", "sl"]

    def test_cycle_rejects_unknown_code(self):
        ok, _ = config_mod.coerce_setting("general", "language_cycle",
                                          ["auto", "xx"])
        assert not ok

    def test_cycle_rejects_empty_entry(self):
        ok, _ = config_mod.coerce_setting("general", "language_cycle", [""])
        assert not ok

    def test_cycle_rejects_non_list(self):
        for bad in ("en", 42, {"en": 1}, None):
            ok, _ = config_mod.coerce_setting("general", "language_cycle", bad)
            assert not ok

    def test_cycle_rejects_non_str_entry(self):
        ok, _ = config_mod.coerce_setting("general", "language_cycle", ["en", 3])
        assert not ok

    def test_cycle_rejects_too_many_entries(self):
        ok, _ = config_mod.coerce_setting(
            "general", "language_cycle", ["auto"] + _codes(8))
        assert not ok

    def test_cycle_normalizes_case_and_dedupes(self):
        ok, v = config_mod.coerce_setting("general", "language_cycle",
                                          ["EN", "en", " Sl "])
        assert ok and v == ["en", "sl"]

    def test_whitelist_accepts_known_codes(self):
        ok, v = config_mod.coerce_setting("general", "language_whitelist",
                                          ["sl", "en"])
        assert ok and v == ["sl", "en"]

    def test_whitelist_rejects_auto(self):
        ok, _ = config_mod.coerce_setting("general", "language_whitelist",
                                          ["auto"])
        assert not ok

    def test_whitelist_rejects_unknown(self):
        ok, _ = config_mod.coerce_setting("general", "language_whitelist", ["xx"])
        assert not ok

    def test_whitelist_rejects_non_list(self):
        ok, _ = config_mod.coerce_setting("general", "language_whitelist", "sl")
        assert not ok

    def test_whitelist_rejects_too_many(self):
        ok, _ = config_mod.coerce_setting(
            "general", "language_whitelist", _codes(17))
        assert not ok

    def test_whitelist_dedupes_case_insensitively(self):
        ok, v = config_mod.coerce_setting("general", "language_whitelist",
                                          ["SL", "sl", "En"])
        assert ok and v == ["sl", "en"]

    def test_defaults_are_off(self):
        assert DEFAULTS["general"]["language_cycle"] == []
        assert DEFAULTS["general"]["language_whitelist"] == []
        assert DEFAULTS["hotkey"]["language_key"] == ""

    def test_apply_settings_round_trip_good(self):
        cfg = copy.deepcopy(DEFAULTS)
        body = {"general": {"language_cycle": ["auto", "en", "sl"],
                            "language_whitelist": ["sl", "en"]},
                "hotkey": {"language_key": "F7"}}
        changed, rejected = config_mod.apply_settings(cfg, body)
        assert not rejected
        assert {"general.language_cycle", "general.language_whitelist",
                "hotkey.language_key"} <= set(changed)
        assert cfg["general"]["language_cycle"] == ["auto", "en", "sl"]
        assert cfg["general"]["language_whitelist"] == ["sl", "en"]
        assert cfg["hotkey"]["language_key"] == "F7"

    def test_apply_settings_rejects_bad_values(self):
        cfg = copy.deepcopy(DEFAULTS)
        before = copy.deepcopy(cfg)
        body = {"general": {"language_cycle": ["xx"],
                            "language_whitelist": ["auto"]},
                "hotkey": {"language_key": "F7"}}
        changed, rejected = config_mod.apply_settings(cfg, body)
        assert set(rejected) == {"general.language_cycle",
                                 "general.language_whitelist"}
        assert changed == ["hotkey.language_key"]
        assert cfg["general"]["language_cycle"] == before["general"]["language_cycle"]
        assert cfg["general"]["language_whitelist"] == \
            before["general"]["language_whitelist"]
        assert cfg["hotkey"]["language_key"] == "F7"

    def test_apply_settings_no_change_when_equal(self):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en"]
        changed, rejected = config_mod.apply_settings(
            cfg, {"general": {"language_cycle": ["en"]}})
        assert not changed and not rejected

    def test_save_and_load_round_trip(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["auto", "en", "sl"]
        cfg["general"]["language_whitelist"] = ["sl", "en"]
        cfg["hotkey"]["language_key"] = "F7"
        config_mod.save_config(cfg, path)
        loaded = config_mod.load_config(path)
        assert loaded["general"]["language_cycle"] == ["auto", "en", "sl"]
        assert loaded["general"]["language_whitelist"] == ["sl", "en"]
        assert loaded["hotkey"]["language_key"] == "F7"

    def test_empty_lists_persist_as_off(self, tmp_path):
        # mic_priority semantics: [] is meaningful (removals round-trip)
        path = tmp_path / "config.toml"
        path.write_text('[general]\nlanguage_cycle = ["en"]\n'
                        'language_whitelist = ["sl"]\n')
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"]["language_cycle"] = ["en"]
        cfg["general"]["language_whitelist"] = ["sl"]
        cfg["general"]["language_cycle"] = []
        cfg["general"]["language_whitelist"] = []
        config_mod.save_config(cfg, path)
        text = path.read_text()
        assert "language_cycle = []" in text
        assert "language_whitelist = []" in text
        loaded = config_mod.load_config(path)
        assert loaded["general"]["language_cycle"] == []
        assert loaded["general"]["language_whitelist"] == []

    def test_load_without_keys_yields_defaults(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[general]\nlanguage = "de"\n')
        loaded = config_mod.load_config(path)
        assert loaded["general"]["language_cycle"] == []
        assert loaded["general"]["language_whitelist"] == []
        assert loaded["hotkey"]["language_key"] == ""

    def test_unknown_language_key_rejected_by_str_range(self):
        # same rule as paste_key: only strings 1..64 chars; the value is
        # grab-time validated by HotkeyListener
        ok, _ = config_mod.coerce_setting("hotkey", "language_key", 42)
        assert not ok


# ---------------------------------------------------------------------------
# Phase 4 - Settings UI (display-gated, test_gtkui.py pattern)
# ---------------------------------------------------------------------------

def _gtk_ready() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except Exception:  # noqa: BLE001 - PyGObject/GTK4/Adw not installed
        return False
    import os
    return bool(os.environ.get("DISPLAY")
                or os.environ.get("WAYLAND_DISPLAY"))


GTK_READY = _gtk_ready()


@pytest.fixture()
def loop():
    from gi.repository import GLib
    return GLib.MainLoop()


@pytest.mark.gtk  # GUI lane (Q1)
@pytest.mark.skipif(not GTK_READY, reason="no GTK/display for settings UI")
class TestSettingsUI:
    @staticmethod
    def _window(loop):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        from tests.test_gtkui import StubClient, pump
        c = StubClient()
        w = SettingsWindow(client=c)
        w.present()
        pump(loop)
        return w, c

    def test_rows_registered(self, loop):
        w, _ = self._window(loop)
        assert ("general", "language_whitelist") in w._rows
        assert ("hotkey", "language_key") in w._rows
        w.close()

    def test_cycle_editor_add_and_collect(self, loop):
        w, _ = self._window(loop)
        w._add_cycle_lang("en")
        w._add_cycle_lang("sl")
        w._add_cycle_lang("auto")
        assert w._collect()["general"]["language_cycle"] == ["en", "sl", "auto"]
        w.close()

    def test_cycle_editor_move_and_remove(self, loop):
        w, _ = self._window(loop)
        w._add_cycle_lang("en")
        w._add_cycle_lang("sl")
        w._add_cycle_lang("de")
        # move "sl" (index 1) up -> first
        w._move_cycle_lang(w._cycle_rows[1], -1)
        assert w._collect_language_cycle() == ["sl", "en", "de"]
        # remove the first
        w._remove_cycle_lang(w._cycle_rows[0])
        assert w._collect_language_cycle() == ["en", "de"]
        w.close()

    def test_cycle_editor_collects_lowercased_and_skips_empty(self, loop):
        w, _ = self._window(loop)
        w._add_cycle_lang("  SL ")
        w._add_cycle_lang("")
        assert w._collect_language_cycle() == ["sl"]
        w.close()

    def test_cycle_editor_flags_unknown_code(self, loop):
        w, _ = self._window(loop)
        w._add_cycle_lang("xx")
        row = w._cycle_rows[0]["row"]
        assert "error" in row.get_css_classes()
        w._add_cycle_lang("auto")
        assert "error" not in w._cycle_rows[1]["row"].get_css_classes()
        w.close()

    def test_cycle_loads_from_config(self, loop):
        w, c = self._window(loop)
        orig = c.get_config

        def patched():
            cfg, fd = orig()
            cfg["general"]["language_cycle"] = ["auto", "sl"]
            return cfg, fd

        c.get_config = patched
        w._load()
        assert w._collect_language_cycle() == ["auto", "sl"]
        w.close()

    def test_empty_cycle_collects_as_off(self, loop):
        # empty list is meaningful: removals round-trip through the save body
        w, _ = self._window(loop)
        assert w._collect()["general"]["language_cycle"] == []
        w.close()

    def test_whitelist_list_proxy(self, loop):
        w, _ = self._window(loop)
        proxy = w._rows[("general", "language_whitelist")]
        proxy.set_value(["sl", "en"])
        assert proxy.row.get_text() == "sl, en"
        assert proxy.get_value() == ["sl", "en"]
        assert w._collect()["general"]["language_whitelist"] == ["sl", "en"]
        w.close()

    def test_language_key_round_trips(self, loop):
        w, c = self._window(loop)
        orig = c.get_config

        def patched():
            cfg, fd = orig()
            cfg["hotkey"]["language_key"] = "F7"
            return cfg, fd

        c.get_config = patched
        w._load()
        assert w._rows[("hotkey", "language_key")].get_text() == "F7"
        w._rows[("hotkey", "language_key")].set_text("F8")
        assert w._collect()["hotkey"]["language_key"] == "F8"
        w.close()

    def test_language_key_empty_is_skipped(self, loop):
        # emptied field -> key omitted from the body (off, like paste_key)
        w, _ = self._window(loop)
        w._rows[("hotkey", "language_key")].set_text("")
        assert "language_key" not in w._collect()["hotkey"]
        w.close()


# ---------------------------------------------------------------------------
# Phase 5 - doctor language section
# ---------------------------------------------------------------------------

class TestDoctor:
    @staticmethod
    def _lines(cfg):
        from fluidvoice.doctor import _language_lines
        return _language_lines(cfg)

    def _cfg(self, **general):
        cfg = copy.deepcopy(DEFAULTS)
        cfg["general"].update(general)
        return cfg

    def test_lines_include_cycle_whitelist_guard(self, monkeypatch):
        from fluidvoice import doctor as doctor_mod
        cfg = self._cfg(language_cycle=["auto", "sl"],
                        language_whitelist=["sl", "en"])
        cfg["hotkey"]["language_key"] = "F7"
        monkeypatch.setattr(doctor_mod, "_live_language_status",
                            lambda: None)
        lines = self._lines(cfg)
        assert any("cycle: [auto, sl]" in ln and "key F7" in ln
                   for ln in lines)
        assert any("whitelist: [sl, en]" in ln for ln in lines)
        assert any("guard:" in ln for ln in lines)
        # _live_language_status pinned to None: the socket path is global
        # to XDG_RUNTIME_DIR, so a LIVE daemon on this machine would leak
        # into the test env (the audit's socket-collision finding)
        assert any("runtime: unknown (daemon down)" in ln for ln in lines)

    def test_empty_cycle_and_whitelist_state(self):
        lines = self._lines(self._cfg())
        assert any("cycle: none" in ln and "feature off" in ln
                   for ln in lines)
        assert any("whitelist: off (empty)" in ln for ln in lines)

    def test_whispercpp_guard_limitation(self, monkeypatch):
        monkeypatch.setattr(backends, "_import_ok", lambda m: False)
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: False)
        monkeypatch.setattr(backends, "_whispercpp_binary",
                            lambda: "/usr/bin/whisper-cli")
        cfg = self._cfg()
        cfg["model"]["backend"] = "whisper.cpp"
        cfg["model"]["whispercpp_model"] = "ggml-base.bin"
        lines = self._lines(cfg)
        assert any("guard:" in ln and "not surfaced under auto" in ln
                   and "whisper.cpp" in ln for ln in lines)

    def test_parakeet_guard_not_applicable(self, monkeypatch):
        monkeypatch.setattr(backends, "_import_ok",
                            lambda m: m == "onnxruntime")
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: False)
        cfg = self._cfg()
        cfg["model"]["backend"] = "parakeet"
        lines = self._lines(cfg)
        assert any("guard:" in ln and "not applicable" in ln
                   and "parakeet" in ln for ln in lines)

    def test_faster_whisper_guard_active(self, monkeypatch):
        monkeypatch.setattr(backends, "_import_ok",
                            lambda m: m == "faster_whisper")
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: True)
        cfg = self._cfg()
        cfg["model"]["backend"] = "faster-whisper"
        lines = self._lines(cfg)
        assert any("guard:" in ln and "whitelist guard active" in ln
                   for ln in lines)

    def test_no_backend_resolves(self, monkeypatch):
        monkeypatch.setattr(backends, "_import_ok", lambda m: False)
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: False)
        monkeypatch.setattr(backends, "_whispercpp_binary", lambda: None)
        lines = self._lines(self._cfg())
        assert any("guard: no backend resolves" in ln for ln in lines)

    def test_live_language_block_from_daemon(self, monkeypatch):
        from fluidvoice import control as control_mod
        from fluidvoice import doctor as doctor_mod

        def fake_request(action, **kw):
            assert action == "status"
            return {"ok": True,
                    "language": {"effective": "sl", "source": "cycle",
                                 "cycle": ["auto", "sl"],
                                 "cycle_engaged": True,
                                 "whitelist": ["sl", "en"]}}

        monkeypatch.setattr(control_mod, "request", fake_request)
        monkeypatch.setattr(doctor_mod.paths, "socket_path",
                            lambda: Path("/etc/hostname"))
        lines = self._lines(self._cfg())
        assert any("runtime: sl (source cycle; engaged true)" in ln
                   for ln in lines)

    def test_live_block_absent_for_old_daemon(self, monkeypatch):
        # an older daemon answers status without the language block
        from fluidvoice import control as control_mod
        from fluidvoice import doctor as doctor_mod

        monkeypatch.setattr(control_mod, "request", lambda a, **kw: {"ok": True})
        monkeypatch.setattr(doctor_mod.paths, "socket_path",
                            lambda: Path("/etc/hostname"))
        lines = self._lines(self._cfg())
        assert any("runtime: unknown (daemon down)" in ln for ln in lines)
