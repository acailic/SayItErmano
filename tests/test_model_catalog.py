"""GGUF + Parakeet catalogs: curated models + managed-cache probes."""
from __future__ import annotations

import re

from fluidvoice import model_catalog, paths

BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"


def test_gguf_catalog_completeness():
    assert set(model_catalog.GGUF_CATALOG) == {
        "ggml-base.bin", "ggml-base.en.bin",
        "ggml-small.bin", "ggml-small.en.bin",
        "ggml-medium.bin", "ggml-medium.en.bin",
        "ggml-large-v3.bin",
    }
    for name, info in model_catalog.GGUF_CATALOG.items():
        assert set(info) == {"size", "langs", "note", "url"}, name
        assert info["url"] == BASE + name


def test_gguf_path_is_managed_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = model_catalog.gguf_path("ggml-base.bin")
    assert p == paths.models_dir() / "whisper.cpp" / "ggml-base.bin"


def test_gguf_downloaded_true_false(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert model_catalog.gguf_downloaded("ggml-base.bin") is False
    model_catalog.gguf_dir().mkdir(parents=True)
    model_catalog.gguf_path("ggml-base.bin").write_bytes(b"x")
    assert model_catalog.gguf_downloaded("ggml-base.bin") is True
    # unknown name is never "downloaded" (and never writes a path)
    assert model_catalog.gguf_downloaded("ggml-bogus.bin") is False


class TestParakeetCatalog:
    """Curated sherpa-onnx Parakeet TDT exports: checksummed sources."""

    def test_both_models_present(self):
        assert set(model_catalog.PARAKEET_CATALOG) == {
            "parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3"}
        assert model_catalog.PARAKEET_DEFAULT_MODEL in model_catalog.PARAKEET_CATALOG

    def test_urls_and_checksums(self):
        hex64 = re.compile(r"^[0-9a-f]{64}$")
        for name, info in model_catalog.PARAKEET_CATALOG.items():
            assert info["url"].startswith(model_catalog.PARAKEET_TARBALL_BASE), name
            assert hex64.match(info["tarball_sha256"]), name
            assert set(info["files"]) == {"encoder.int8.onnx", "decoder.int8.onnx",
                                           "joiner.int8.onnx", "tokens.txt"}
            for sha in info["files"].values():
                assert hex64.match(sha), name
            assert set(info) == {"size", "langs", "note", "url",
                                 "tarball_sha256", "files", "features"}

    def test_feature_spec_pinned(self):
        want = {"sample_rate": 16000, "n_mels": 128, "n_fft": 512,
                "win": 400, "hop": 160, "fmin": 0.0, "fmax": 8000.0}
        for info in model_catalog.PARAKEET_CATALOG.values():
            assert info["features"] == want

    def test_downloaded_probe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        name = "parakeet-tdt-0.6b-v2"
        d = model_catalog.parakeet_model_dir(name)
        assert model_catalog.parakeet_downloaded(name) is False
        assert model_catalog.parakeet_downloaded("nope") is False
        d.mkdir(parents=True)
        for f in model_catalog.PARAKEET_CATALOG[name]["files"]:
            (d / f).write_bytes(b"x")
        assert model_catalog.parakeet_downloaded(name) is True
        (d / "joiner.int8.onnx").unlink()  # one missing -> not downloaded
        assert model_catalog.parakeet_downloaded(name) is False

    def test_model_dir_is_managed_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert model_catalog.parakeet_model_dir("parakeet-tdt-0.6b-v3") == \
            paths.models_dir() / "parakeet" / "parakeet-tdt-0.6b-v3"


class TestNoteText:
    """Catalog notes translate at display time (runtime language switch)."""

    def test_every_catalog_note_is_translatable(self):
        # under a non-English language every catalog note must come back
        # translated — a note missing from the literal table falls back
        # to English and fails this guard
        import fluidvoice.i18n as i18n

        before = i18n.set_language("sr-Latn")
        try:
            for catalog in (model_catalog.MODEL_CATALOG,
                            model_catalog.GGUF_CATALOG,
                            model_catalog.CHAT_GGUF_CATALOG,
                            model_catalog.PARAKEET_CATALOG):
                for name, info in catalog.items():
                    assert model_catalog.note_text(info["note"]) != \
                        info["note"], name
        finally:
            i18n.set_language(before)

    def test_translates_and_switches_at_runtime(self):
        import fluidvoice.i18n as i18n

        before = i18n.set_language("sr-Latn")
        try:
            assert model_catalog.note_text("best accuracy") == \
                "najveća tačnost"
            i18n.set_language("sr")  # no restart: next call re-translates
            assert model_catalog.note_text("best accuracy") == \
                "највећа тачност"
        finally:
            i18n.set_language(before)

    def test_unknown_note_falls_back_to_english(self):
        assert model_catalog.note_text("a future catalog note") == \
            "a future catalog note"
