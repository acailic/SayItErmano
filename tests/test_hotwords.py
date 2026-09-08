"""Vocabulary boosting (model.hotwords, upstream #916): config coercion,
backend pass-through (faster-whisper hotwords / torch initial_prompt),
preview prompt merge, doctor + engine-reload registration."""
from __future__ import annotations

import copy

import pytest

from fluidvoice.config import (ALLOWED_SETTINGS, DEFAULTS, ENGINE_KEYS,
                               _SAVE_WHITELIST, coerce_setting)


def test_defaults_and_registration():
    assert DEFAULTS["model"]["hotwords"] == []
    assert "hotwords" in ALLOWED_SETTINGS["model"]
    assert "hotwords" in _SAVE_WHITELIST["model"]
    assert "model.hotwords" in ENGINE_KEYS  # change reloads the engine


def test_coercion():
    ok, v = coerce_setting("model", "hotwords",
                           ["SayItErmano", " PipeWire ", "SayItErmano"])
    assert ok and v == ["SayItErmano", "PipeWire"]  # strip + dedupe
    ok, v = coerce_setting("model", "hotwords", [])
    assert ok and v == []
    for bad in ([""], ["x" * 65], ["ok", 7],
                [f"w{i}" for i in range(129)], "word"):
        ok, _ = coerce_setting("model", "hotwords", bad)
        assert not ok, bad


def test_fw_backend_passes_hotwords(monkeypatch):
    from fluidvoice.backends import faster_whisper_backend as fw

    captured = {}

    class FakeWM:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, path, **kwargs):
            captured.update(kwargs)

            class Seg:
                text = "hi"
                start = 0.0
                end = 1.0
                avg_logprob = None

            class Info:
                language = "en"
                duration = 1.0

            return iter([Seg()]), Info()

    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"]["hotwords"] = ["SayItErmano", "PipeWire"]
    monkeypatch.setattr(fw, "preload_cuda_libs", lambda: None)
    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeWM)
    be = fw.FasterWhisperBackend(cfg)
    be.transcribe(__import__("pathlib").Path("/dev/null"))
    assert captured["hotwords"] == "SayItErmano PipeWire"

    cfg2 = copy.deepcopy(DEFAULTS)
    be2 = fw.FasterWhisperBackend(cfg2)
    assert be2.hotwords is None  # unset -> None, not ""


def test_torch_backend_passes_hotwords(monkeypatch):
    from fluidvoice.backends import torch_whisper as tw

    captured = {}

    class FakeWhisper:
        def load_model(self, name, device=None):
            return self

        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            return {"text": "hi", "language": "en", "segments": []}

    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"]["hotwords"] = ["kubernetes"]
    be = tw.TorchWhisperBackend.__new__(tw.TorchWhisperBackend)
    be._whisper = FakeWhisper()
    be.model_name = "tiny"
    be.language = None
    be.hotwords = " ".join(cfg["model"]["hotwords"]) or None
    be.device = "cpu"
    be._model = None
    be.transcribe(__import__("pathlib").Path("/dev/null"))
    assert captured["initial_prompt"] == "kubernetes"


def test_preview_prompt_merge():
    from fluidvoice.preview import preview_transcriber

    class Model:
        def transcribe(self, buf, **kwargs):
            self.seen = kwargs
            return [], None

    class BE:
        name = "faster-whisper"
        _model = Model()

    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"]["hotwords"] = ["SayItErmano"]
    made = preview_transcriber(cfg, BE, "auto")
    assert made is not None
    fn, name = made
    fn(b"", "rolling ctx")
    assert BE._model.seen["initial_prompt"] == "SayItErmano rolling ctx"
    fn(b"", None)
    assert BE._model.seen["initial_prompt"] == "SayItErmano"


def test_doctor_line():
    from fluidvoice import doctor
    c = copy.deepcopy(DEFAULTS)
    assert not any("hotwords" in ln
                   for ln in doctor._models_cache_lines(c))
    c["model"]["hotwords"] = ["a", "b"]
    assert any("hotwords: 2" in ln
               for ln in doctor._models_cache_lines(c))


def test_doctor_warns_past_twenty():
    from fluidvoice import doctor
    c = copy.deepcopy(DEFAULTS)
    c["model"]["hotwords"] = [f"w{i}" for i in range(25)]
    lines = "\n".join(doctor._models_cache_lines(c))
    assert "hotwords: 25" in lines and "WARN" in lines
    c["model"]["hotwords"] = ["a", "b"]
    assert "WARN" not in "\n".join(doctor._models_cache_lines(c))


def test_hotword_hit_rate_logged(tmp_path, monkeypatch):
    from fluidvoice import daemon as dm

    logs = []
    cfg = copy.deepcopy(DEFAULTS)
    cfg["model"]["hotwords"] = ["SayItErmano", "PipeWire", "unheard"]
    monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "h.jsonl")

    class B:
        name = "stub"
        surfaces_detected_language = False

        def transcribe(self, wav, language=None):
            return {"text": "using SayItErmano with pipewire today",
                    "language": None, "duration": None, "segments": []}

    pipe = dm.DictationPipeline(cfg, B(), inserter=lambda t, c: "typed",
                                history_writer=lambda e, w: None,
                                logger=logs.append)
    import struct, wave
    wav = tmp_path / "u.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"".join(struct.pack("<h", 8000)
                                for _ in range(16000)))
    pipe.run(wav, None)
    assert any("hotwords: 2/3" in m for m in logs)
