from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fluidvoice import backends
from fluidvoice.config import DEFAULTS


def cfg(**model_overrides):
    import copy
    c = copy.deepcopy(DEFAULTS)
    c["model"].update(model_overrides)
    return c


class TestResolveModelName:
    def test_auto_gpu_default(self, monkeypatch):
        monkeypatch.setattr(backends, "cuda_available", lambda: True)
        assert backends.resolve_model_name("auto") == "small"

    def test_auto_cpu_default(self, monkeypatch):
        monkeypatch.setattr(backends, "cuda_available", lambda: False)
        assert backends.resolve_model_name("auto") == "base"

    def test_aliases(self):
        assert backends.resolve_model_name("turbo") == "large-v3-turbo"
        assert backends.resolve_model_name("LARGE") == "large-v3"
        assert backends.resolve_model_name(" large-v3-turbo ") == "large-v3-turbo"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="unknown model"):
            backends.resolve_model_name("gpt-4o-audio")

    def test_every_resolvable_model_has_a_repo(self):
        for name in ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"):
            assert name in backends.FW_MODEL_REPOS


class TestLoadBackendSelection:
    """Backend priority with faked availability - no real models touched."""

    @pytest.fixture()
    def fakes(self, monkeypatch):
        from fluidvoice.backends import faster_whisper_backend as fw
        from fluidvoice.backends import torch_whisper as tw
        from fluidvoice.backends import whisper_cpp as wc

        made: list[str] = []

        class FakeFW:
            name = "faster-whisper"

            def __init__(self, c):
                made.append("faster-whisper")

        class FakeTorch:
            name = "whisper-torch"

            def __init__(self, c):
                made.append("whisper-torch")

        class FakeCpp:
            name = "whisper.cpp"

            def __init__(self, c):
                made.append("whisper.cpp")

        monkeypatch.setattr(fw, "FasterWhisperBackend", FakeFW)
        monkeypatch.setattr(tw, "TorchWhisperBackend", FakeTorch)
        monkeypatch.setattr(wc, "WhisperCppBackend", FakeCpp)
        return made

    def test_faster_whisper_gpu_wins(self, monkeypatch, fakes):
        monkeypatch.setattr(backends, "_import_ok", lambda m: m == "faster_whisper")
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: True)
        b = backends.load_backend(cfg())
        assert b.name == "faster-whisper"

    def test_torch_gpu_when_fw_cpu_only(self, monkeypatch, fakes):
        monkeypatch.setattr(backends, "_import_ok", lambda m: True)
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: False)
        monkeypatch.setattr(backends, "cuda_available", lambda: True)
        b = backends.load_backend(cfg())
        assert b.name == "whisper-torch"

    def test_faster_whisper_cpu_when_no_gpu(self, monkeypatch, fakes):
        monkeypatch.setattr(backends, "_import_ok", lambda m: m == "faster_whisper")
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: False)
        monkeypatch.setattr(backends, "cuda_available", lambda: False)
        b = backends.load_backend(cfg())
        assert b.name == "faster-whisper"

    def test_whisper_cpp_explicit(self, monkeypatch, fakes):
        monkeypatch.setattr(backends, "_whispercpp_binary", lambda: "/usr/bin/whisper-cli")
        monkeypatch.setattr(backends, "_import_ok", lambda m: False)
        b = backends.load_backend(cfg(backend="whisper.cpp",
                                      whispercpp_model="/models/ggml-base.bin"))
        assert b.name == "whisper.cpp"

    def test_nothing_available(self, monkeypatch, fakes):
        monkeypatch.setattr(backends, "_import_ok", lambda m: False)
        monkeypatch.setattr(backends, "_whispercpp_binary", lambda: None)
        with pytest.raises(RuntimeError, match="no speech backend"):
            backends.load_backend(cfg())

    def test_explicit_unknown_backend(self):
        with pytest.raises(ValueError, match="unknown backend"):
            backends.load_backend(cfg(backend="deepgram"))


class TestWhisperCppBinary:
    def test_finds_known_names(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which",
                            lambda n: f"/usr/bin/{n}" if n == "whisper-cli" else None)
        assert backends._whispercpp_binary() == "/usr/bin/whisper-cli"

    def test_none_found(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, "which", lambda n: None)
        assert backends._whispercpp_binary() is None


class TestWhisperCppModelResolution:
    """name-or-path resolution of model.whispercpp_model (Phase 2 plan)."""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path, monkeypatch):
        import fluidvoice.backends.whisper_cpp as wc
        monkeypatch.setattr(wc, "_whispercpp_binary", lambda: "/fake/whisper-cli")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        self.wc = wc
        self.tmp = tmp_path

    def test_absolute_existing_path_passthrough(self):
        p = self.tmp / "m.bin"
        p.write_bytes(b"x")
        be = self.wc.WhisperCppBackend(cfg(whispercpp_model=str(p)))
        assert be.model == str(p)

    def test_missing_path_raises(self):
        with pytest.raises(RuntimeError, match="not found"):
            self.wc.WhisperCppBackend(
                cfg(whispercpp_model=str(self.tmp / "nope.bin")))

    def test_catalog_name_resolves_to_managed_cache(self):
        from fluidvoice import model_catalog
        model_catalog.gguf_dir().mkdir(parents=True)
        model_catalog.gguf_path("ggml-base.bin").write_bytes(b"x")
        be = self.wc.WhisperCppBackend(cfg(whispercpp_model="ggml-base.bin"))
        assert be.model == str(model_catalog.gguf_path("ggml-base.bin"))

    def test_catalog_name_missing_mentions_settings(self):
        with pytest.raises(RuntimeError, match="not downloaded"):
            self.wc.WhisperCppBackend(cfg(whispercpp_model="ggml-base.bin"))
        try:
            self.wc.WhisperCppBackend(cfg(whispercpp_model="ggml-base.bin"))
        except RuntimeError as e:
            assert "Settings" in str(e)

    def test_unknown_bare_name_lists_catalog(self):
        with pytest.raises(RuntimeError, match="unknown whisper.cpp model"):
            self.wc.WhisperCppBackend(cfg(whispercpp_model="ggml-bogus.bin"))
        try:
            self.wc.WhisperCppBackend(cfg(whispercpp_model="ggml-bogus.bin"))
        except RuntimeError as e:
            assert "ggml-base.bin" in str(e)

    def test_empty_value_requires_setting(self):
        with pytest.raises(RuntimeError, match="required"):
            self.wc.WhisperCppBackend(cfg(whispercpp_model=""))

    def test_home_relative_path_expands(self, monkeypatch, tmp_path_factory):
        home = tmp_path_factory.mktemp("home")
        monkeypatch.setenv("HOME", str(home))
        (home / "m.bin").write_bytes(b"x")
        be = self.wc.WhisperCppBackend(cfg(whispercpp_model="~/m.bin"))
        assert be.model == str(home / "m.bin")


class TestUpstreamNameAliases:
    def test_whisper_prefix_aliases(self):
        assert backends.resolve_model_name("whisper-small") == "small"
        assert backends.resolve_model_name("whisper-large-turbo") == "large-v3-turbo"
        assert backends.resolve_model_name("WHISPER-BASE") == "base"


class TestParakeetSelection:
    """Explicit parakeet wiring: load_backend, resolve_model_name, status."""

    @pytest.fixture()
    def pk_fakes(self, monkeypatch):
        from fluidvoice.backends import parakeet_onnx as pk

        made: list[str] = []

        class FakePK:
            name = "parakeet"

            def __init__(self, c):
                made.append("parakeet")

        monkeypatch.setattr(pk, "ParakeetOnnxBackend", FakePK)
        return made

    @pytest.fixture()
    def fw_fake(self, monkeypatch):
        from fluidvoice.backends import faster_whisper_backend as fw

        class FakeFW:
            name = "faster-whisper"

            def __init__(self, c):
                pass

        monkeypatch.setattr(fw, "FasterWhisperBackend", FakeFW)

    def test_explicit_and_alias_construct_it(self, monkeypatch, pk_fakes):
        b = backends.load_backend(cfg(backend="parakeet"))
        assert b.name == "parakeet" and pk_fakes == ["parakeet"]
        b = backends.load_backend(cfg(backend="parakeet-onnx"))
        assert b.name == "parakeet"
        assert pk_fakes == ["parakeet", "parakeet"]

    def test_auto_never_picks_parakeet_even_with_ort(self, monkeypatch, fw_fake):
        monkeypatch.setattr(backends, "_import_ok", lambda m: True)
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: False)
        monkeypatch.setattr(backends, "cuda_available", lambda: False)
        b = backends.load_backend(cfg())
        assert b.name == "faster-whisper"  # branch 3, whisper family

    def test_parakeet_model_names_pass_through(self):
        from fluidvoice import model_catalog
        for name in model_catalog.PARAKEET_CATALOG:
            assert backends.resolve_model_name(name) == name

    def test_backend_status_not_installed(self, monkeypatch):
        monkeypatch.setattr(backends, "_import_ok", lambda m: False)
        assert backends.backend_status()["parakeet"] == \
            "not installed (pip install onnxruntime)"

    def test_backend_status_available(self, monkeypatch):
        from types import SimpleNamespace

        from fluidvoice import model_catalog
        monkeypatch.setattr(backends, "_import_ok", lambda m: m != "torch")
        stub = SimpleNamespace(
            __version__="1.29.0",
            get_available_providers=lambda: ["CPUExecutionProvider"])
        monkeypatch.setitem(sys.modules, "onnxruntime", stub)
        monkeypatch.setattr(model_catalog, "parakeet_downloaded",
                            lambda n: n == "parakeet-tdt-0.6b-v2")
        s = backends.backend_status()["parakeet"]
        assert s == "available (CPU · v2 yes, v3 no)"


class TestFasterWhisperWarmup:
    """warmup() = load + one throwaway inference, so the first real
    dictation doesn't pay CUDA kernel setup. Hermetic: _model is injected,
    no faster-whisper import or model download."""

    @staticmethod
    def _backend(model):

        from fluidvoice.backends import faster_whisper_backend as fw
        be = object.__new__(fw.FasterWhisperBackend)
        be.model_name, be.language = "small", "en"
        be.device, be.compute = "cpu", "int8"
        be._WhisperModel = None
        be._model = model
        return be

    @staticmethod
    def _fake_model(fail=False):
        from types import SimpleNamespace

        class Inner:
            def __init__(self):
                self.paths = []

            def transcribe(self, path, language=None, **kw):
                if fail:
                    raise RuntimeError("probe boom")
                self.paths.append(path)
                seg = SimpleNamespace(start=0.0, end=1.0, text=" hi",
                                      avg_logprob=-0.1)
                info = SimpleNamespace(language="en", duration=1.0)
                return iter([seg]), info

        return Inner()

    def test_warmup_runs_throwaway_inference(self):
        model = self._fake_model()
        be = self._backend(model)
        be.warmup()
        assert len(model.paths) == 1
        wav = model.paths[0]
        assert str(wav).endswith(".wav") and not Path(wav).exists()  # cleaned up

    def test_warmup_swallows_inference_errors(self):
        be = self._backend(self._fake_model(fail=True))
        be.warmup()  # must not raise - load errors still would
