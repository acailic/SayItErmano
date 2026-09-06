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

import pytest

from fluidvoice import backends, config as config_mod
from fluidvoice.config import DEFAULTS, KNOWN_LANGUAGES
from fluidvoice import daemon as dm


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
# Phase 2 - runtime precedence + wrong-language guard
# ---------------------------------------------------------------------------

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
                                                "whisper.cpp", "parakeet"}

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
        from fluidvoice.backends.faster_whisper_backend \
            import FasterWhisperBackend
        from fluidvoice.backends.torch_whisper import TorchWhisperBackend
        from fluidvoice.backends.whisper_cpp import WhisperCppBackend
        from fluidvoice.backends.parakeet_onnx import ParakeetOnnxBackend
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
