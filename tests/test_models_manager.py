"""Settings depth pack: per-model language (model.languages) + model
pruning (Phase 3/4 plan).

Pure-python tests (resolver, config validation, TOML round-trip, the
daemon's model-delete action) always run; the Settings → Models row tests
skip headless (class-level guards, same conditions as tests/test_gtkui.py).
"""
from __future__ import annotations

import copy
import os
import wave

import pytest

from fluidvoice import backends
from fluidvoice.config import DEFAULTS, apply_settings, coerce_setting


class StubBackend:
    name = "stub"

    def __init__(self, model_name="small"):
        self.model_name = model_name


class StubRecorder:
    def start(self, path):
        pass

    def stop(self):
        return None

    def cancel(self):
        pass

    def elapsed(self):
        return 0.0


def cfg(**over):
    c = copy.deepcopy(DEFAULTS)
    for sec, keys in over.items():
        c[sec].update(keys)
    return c


# ---------------------------------------------------------------------------
# effective_language resolution
# ---------------------------------------------------------------------------

class TestEffectiveLanguage:
    def test_no_override_follows_general(self):
        assert backends.effective_language(cfg()) == "auto"
        assert backends.effective_language(
            cfg(general={"language": "de"})) == "de"

    def test_override_wins_for_config_key(self):
        c = cfg(general={"language": "en"}, model={"languages": {
            "small": "de"}})
        assert backends.effective_language(c) == "de"

    def test_auto_override_beats_general(self):
        c = cfg(general={"language": "en"}, model={"languages": {
            "small": "auto"}})
        assert backends.effective_language(c) == "auto"

    def test_empty_override_inherits(self):
        c = cfg(general={"language": "en"}, model={"languages": {
            "small": ""}})
        assert backends.effective_language(c) == "en"

    def test_live_backend_key_wins_over_config_key(self):
        c = cfg(model={"name": "small", "languages": {
            "small": "de", "large-v3": "fr"}})

        class B:
            model_name = "large-v3"

        assert backends.effective_language(c, B()) == "fr"

    def test_whispercpp_path_key_is_basename(self):
        c = cfg(model={"backend": "whisper.cpp",
                       "whispercpp_model": "/opt/models/ggml-base.bin",
                       "languages": {"ggml-base.bin": "de"}})
        assert backends.config_model_key(c) == "ggml-base.bin"
        assert backends.effective_language(c) == "de"

    def test_config_model_key_parakeet_default(self):
        c = cfg(model={"backend": "parakeet"})
        assert backends.config_model_key(c) == "parakeet-tdt-0.6b-v2"
        c = cfg(model={"backend": "parakeet", "name": "parakeet-tdt-0.6b-v3"})
        assert backends.config_model_key(c) == "parakeet-tdt-0.6b-v3"

    def test_backend_model_key_variants(self):
        assert backends.backend_model_key(None) is None

        class Fw:
            model_name = "small"

        class Wc:
            model = "/cache/models/whisper.cpp/ggml-base.bin"

        assert backends.backend_model_key(Fw()) == "small"
        assert backends.backend_model_key(Wc()) == "ggml-base.bin"

    def test_auto_name_resolves(self, monkeypatch):
        monkeypatch.setattr(backends, "cuda_available", lambda: False)
        assert backends.config_model_key(cfg()) == "base"


class TestPipelineLanguagePlumbing:
    def test_pipeline_passes_effective_language_to_backend(self, tmp_path):
        from fluidvoice import daemon as dm
        c = cfg(general={"language": "en"},
                model={"languages": {"stub-ish": "de"}})
        calls: list = []

        class StubBackend:
            name = "stub"
            model_name = "stub-ish"

            def transcribe(self, wav, language=None):
                calls.append(language)
                return {"text": "hi", "language": language, "duration": 1.0}

        pipe = dm.DictationPipeline(
            c, StubBackend(),
            inserter=lambda t, c2: "typed",
            history_writer=lambda e, w: None)
        pipe._transcribe(tmp_path / "x.wav")
        assert calls == ["de"]


class TestWhisperCppLanguageArg:
    @pytest.fixture(autouse=True)
    def _env(self, tmp_path, monkeypatch):
        import fluidvoice.backends.whisper_cpp as wc
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        self.tmp = tmp_path
        script = tmp_path / "whisper-cli"
        self.argv_file = tmp_path / "argv.txt"
        script.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{self.argv_file}'\n"
            "echo 'stub output'\n")
        script.chmod(0o755)
        monkeypatch.setattr(wc, "_whispercpp_binary", lambda: str(script))
        from fluidvoice import model_catalog
        model_catalog.gguf_dir().mkdir(parents=True)
        model_catalog.gguf_path("ggml-base.bin").write_bytes(b"x")
        self.wc = wc

    def _wav(self):
        p = self.tmp / "a.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0" * 3200)
        return p

    def _transcribe(self, c):
        be = self.wc.WhisperCppBackend(c)
        return be.transcribe(self._wav(),
                             language=backends.effective_language(c, be))

    def test_override_adds_l_flag(self):
        c = cfg(model={"backend": "whisper.cpp",
                       "whispercpp_model": "ggml-base.bin",
                       "languages": {"ggml-base.bin": "de"}})
        result = self._transcribe(c)
        argv = self.argv_file.read_text().splitlines()
        assert "-l" in argv and argv[argv.index("-l") + 1] == "de"
        assert result["language"] == "de"

    def test_auto_omits_l_flag(self):
        c = cfg(model={"backend": "whisper.cpp",
                       "whispercpp_model": "ggml-base.bin",
                       "languages": {"ggml-base.bin": "auto"}})
        result = self._transcribe(c)
        assert "-l" not in self.argv_file.read_text().splitlines()
        assert result["language"] is None


class TestCliTranscribeLanguage:
    def test_cli_routes_through_effective_language(self, tmp_path, monkeypatch):
        from fluidvoice import audio_utils, cli
        calls: list = []

        class StubBackend:
            name = "stub"
            model_name = "stub-ish"

            def transcribe(self, wav, language=None):
                calls.append(language)
                return {"text": "hi", "language": language, "duration": 1.0}

        monkeypatch.setattr(backends, "load_backend", lambda c: StubBackend())
        cfile = tmp_path / "c.toml"
        cfile.write_text('[general]\nlanguage = "en"\n'
                         '[model]\n'
                         'languages = { "stub-ish" = "de" }\n')
        wav = tmp_path / "a.wav"
        # real WAV bytes: the probe reads it, the single-shot path passes
        # the file through untouched (no conversion patch needed)
        wav.write_bytes(audio_utils.raw_to_wav_bytes(b"\0" * 3200))
        rc = cli.main(["transcribe", str(wav), "--config", str(cfile),
                       "--no-process"])
        assert rc == 0
        assert calls == ["de"]


# ---------------------------------------------------------------------------
# model.languages config validation + persistence
# ---------------------------------------------------------------------------

class TestModelLanguagesConfig:
    def test_default_and_template(self):
        assert DEFAULTS["model"]["languages"] == {}
        from fluidvoice.config import TEMPLATE
        assert "languages" in TEMPLATE  # cheap doc-guard

    def test_accepts_valid(self):
        ok, out = coerce_setting("model", "languages",
                                 {"small": "de", "ggml-base.bin": "en",
                                  "parakeet-tdt-0.6b-v3": "auto"})
        assert ok is True and out == {"small": "de", "ggml-base.bin": "en",
                                      "parakeet-tdt-0.6b-v3": "auto"}

    def test_strips_values(self):
        ok, out = coerce_setting("model", "languages", {"small": " de "})
        assert ok is True and out == {"small": "de"}

    @pytest.mark.parametrize("bad", [
        "de",                       # not a dict
        {"small": 5},               # non-str value
        {"small": "German"},        # not a language code
        {"": "de"},                 # empty key
        {"x" * 65: "de"},           # over-long key
        {f"m{i}": "de" for i in range(31)},  # 31 entries (max 30)
    ])
    def test_rejects_bad(self, bad):
        ok, out = coerce_setting("model", "languages", bad)
        assert ok is False and out == bad

    def test_accepts_thirty_entries(self):
        ok, out = coerce_setting("model", "languages",
                                 {f"m{i}": "de" for i in range(30)})
        assert ok is True and len(out) == 30

    def test_apply_settings_applies_and_reports(self):
        c = copy.deepcopy(DEFAULTS)
        changed, rejected = apply_settings(
            c, {"model": {"languages": {"small": "de"}}})
        assert rejected == [] and c["model"]["languages"] == {"small": "de"}
        assert "model.languages" in changed
        # NOT an engine/restart key: applies live, read per-dictation
        from fluidvoice.config import ENGINE_KEYS, RESTART_REQUIRED
        assert "model.languages" not in ENGINE_KEYS
        assert "model.languages" not in RESTART_REQUIRED

    def test_toml_roundtrip_with_dotted_keys(self, tmp_path, monkeypatch):
        """ggml-base.bin contains dots: an unquoted key would round-trip
        as a nested table. The quoted-dict fix must survive save->load."""
        from fluidvoice import paths as p
        from fluidvoice.config import load_config, save_config
        target = tmp_path / "c.toml"
        monkeypatch.setattr(p, "config_file", lambda: target)
        c = copy.deepcopy(DEFAULTS)
        c["model"]["languages"] = {"small": "de", "ggml-base.bin": "en"}
        save_config(c)
        text = target.read_text()
        assert '"ggml-base.bin"' in text  # quoted, not a nested table
        loaded = load_config(target)
        assert loaded["model"]["languages"] == {"small": "de",
                                                "ggml-base.bin": "en"}


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

class TestDoctorLanguageLines:
    def test_lines_report_resolution(self):
        from fluidvoice.doctor import _language_lines
        c = cfg(general={"language": "en"},
                model={"languages": {"small": "de"}})
        lines = _language_lines(c)
        assert any("general: en" in ln for ln in lines)
        assert any("small=de" in ln and "model.languages" in ln
                   for ln in lines)
        assert any("active model" in ln and "-> de" in ln for ln in lines)

    def test_parakeet_v2_english_note(self):
        from fluidvoice.doctor import _language_lines
        c = cfg(model={"backend": "parakeet", "name": "parakeet-tdt-0.6b-v2"})
        lines = _language_lines(c)
        assert any("English-only" in ln for ln in lines)

    def test_empty_overrides_line(self):
        from fluidvoice.doctor import _language_lines
        assert any("per-model overrides: none" in ln
                   for ln in _language_lines(cfg()))


# ---------------------------------------------------------------------------
# Phase 4: cached-model enumeration + the model-delete socket action
# ---------------------------------------------------------------------------

@pytest.fixture()
def cache(tmp_path, monkeypatch):
    """Pristine per-test cache root (test_model_download.py fixture)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    return tmp_path


class TestCachedModels:
    def test_empty_cache(self, cache):
        from fluidvoice import model_catalog
        assert model_catalog.cached_models() == []

    def test_fw_repo_dir_with_blobs(self, cache):
        from fluidvoice import model_catalog
        d = model_catalog.cache_entry_path("faster-whisper", "small")
        (d / "blobs").mkdir(parents=True)
        (d / "blobs" / "b1").write_bytes(b"x" * 100)
        (d / "blobs" / "b2").write_bytes(b"y" * 50)
        entries = model_catalog.cached_models()
        assert [(e["kind"], e["name"], e["bytes"]) for e in entries] == \
            [("faster-whisper", "small", 150)]
        assert entries[0]["path"] == d

    def test_fw_unknown_repo_keeps_repo_id_name(self, cache):
        from fluidvoice import model_catalog
        d = model_catalog.cache_entry_path("faster-whisper",
                                           "someone/other-model")
        d.mkdir(parents=True)
        (d / "blob").write_bytes(b"z")
        (e,) = model_catalog.cached_models()
        assert e["name"] == "someone/other-model" and e["bytes"] == 1

    def test_ggml_files_skip_part_files(self, cache):
        from fluidvoice import model_catalog
        model_catalog.gguf_dir().mkdir(parents=True)
        model_catalog.gguf_path("ggml-base.bin").write_bytes(b"a" * 10)
        model_catalog.gguf_path("ggml-tiny.bin").with_suffix(".bin.part") \
            .write_bytes(b"b" * 99)
        (e,) = model_catalog.cached_models()
        assert (e["kind"], e["name"], e["bytes"]) == \
            ("whisper.cpp", "ggml-base.bin", 10)

    def test_parakeet_dirs_skip_staging(self, cache):
        from fluidvoice import model_catalog
        d = model_catalog.parakeet_model_dir("parakeet-tdt-0.6b-v3")
        d.mkdir(parents=True)
        (d / "tokens.txt").write_bytes(b"t" * 5)
        (model_catalog.parakeet_dir() / ".tmp-staging").mkdir()
        (model_catalog.parakeet_dir() / ".tmp-staging" / "x").write_bytes(
            b"q" * 500)
        (e,) = model_catalog.cached_models()
        assert (e["kind"], e["name"], e["bytes"]) == \
            ("parakeet", "parakeet-tdt-0.6b-v3", 5)

    def test_cache_entry_path_variants(self, cache):
        from fluidvoice import model_catalog
        p = model_catalog.cache_entry_path("faster-whisper", "small")
        assert p.name == "models--Systran--faster-whisper-small"
        # repo-id form resolves to the same dir
        assert model_catalog.cache_entry_path(
            "faster-whisper", "Systran/faster-whisper-small") == p
        assert model_catalog.cache_entry_path(
            "whisper.cpp", "ggml-base.bin").name == "ggml-base.bin"
        assert model_catalog.cache_entry_path(
            "parakeet", "parakeet-tdt-0.6b-v2").name == "parakeet-tdt-0.6b-v2"
        with pytest.raises(ValueError, match="unknown model kind"):
            model_catalog.cache_entry_path("bogus", "x")


class TestDaemonDeleteModel:
    _UNSET = object()

    def _daemon(self, c=None, backend=_UNSET):
        from fluidvoice import daemon as dm
        d = dm.Daemon(cfg=c or copy.deepcopy(DEFAULTS),
                      recorder=StubRecorder(),
                      backend_factory=lambda c2: StubBackend(),
                      use_hotkey=False, use_sounds=False)
        d.backend = StubBackend() if backend is self._UNSET else backend
        return d

    def _req(self, d, kind, name):
        return d.handle_request({"action": "model-delete",
                                 "kind": kind, "name": name})

    def test_deletes_decoy_dir_over_socket(self, cache):
        from fluidvoice import model_catalog
        decoy = model_catalog.cache_entry_path("faster-whisper", "tiny")
        (decoy / "blobs").mkdir(parents=True)
        (decoy / "blobs" / "b").write_bytes(b"x" * 42)
        sibling = model_catalog.cache_entry_path("faster-whisper", "base")
        (sibling / "blobs").mkdir(parents=True)
        (sibling / "blobs" / "b").write_bytes(b"y" * 7)
        d = self._daemon()  # stub backend active model: "small"
        resp = self._req(d, "faster-whisper", "tiny")
        assert resp["ok"] is True and resp["bytes"] == 42
        assert not decoy.exists()
        # every sibling is byte-identical after the delete
        from fluidvoice import paths
        fw_dir = paths.models_dir() / "faster-whisper"
        assert list(fw_dir.iterdir()) == [sibling]
        assert (sibling / "blobs" / "b").read_bytes() == b"y" * 7

    def test_refuses_active_model_backend_key(self, cache):
        from fluidvoice import model_catalog
        target = model_catalog.cache_entry_path("faster-whisper", "small")
        target.mkdir(parents=True)
        (target / "b").write_bytes(b"x")
        d = self._daemon()  # live backend model_name = "small"
        resp = self._req(d, "faster-whisper", "small")
        assert resp["ok"] is False and "active model" in resp["error"]
        assert target.exists()  # untouched

    def test_refuses_active_model_gguf_config(self, cache):
        from fluidvoice import model_catalog
        model_catalog.gguf_dir().mkdir(parents=True)
        model_catalog.gguf_path("ggml-base.bin").write_bytes(b"x")
        c = cfg(model={"backend": "whisper.cpp",
                      "whispercpp_model": "ggml-base.bin"})
        d = self._daemon(c=c, backend=None)  # config-derived identity
        resp = self._req(d, "whisper.cpp", "ggml-base.bin")
        assert resp["ok"] is False and "active model" in resp["error"]
        assert model_catalog.gguf_path("ggml-base.bin").exists()

    def test_refuses_active_model_parakeet_config(self, cache):
        from fluidvoice import model_catalog
        model_catalog.parakeet_model_dir("parakeet-tdt-0.6b-v2").mkdir(
            parents=True)
        c = cfg(model={"backend": "parakeet",
                      "name": "parakeet-tdt-0.6b-v2"})
        d = self._daemon(c=c, backend=None)
        resp = self._req(d, "parakeet", "parakeet-tdt-0.6b-v2")
        assert resp["ok"] is False and "active model" in resp["error"]
        assert model_catalog.parakeet_model_dir(
            "parakeet-tdt-0.6b-v2").exists()

    def test_refuses_unknown_kind(self, cache):
        d = self._daemon()
        resp = self._req(d, "bogus-kind", "whatever")
        assert resp["ok"] is False and "unknown model kind" in resp["error"]

    def test_refuses_empty_name(self, cache):
        d = self._daemon()
        resp = self._req(d, "faster-whisper", "  ")
        assert resp["ok"] is False and "missing model name" in resp["error"]

    def test_missing_target_reports_cleanly(self, cache):
        d = self._daemon()
        resp = self._req(d, "faster-whisper", "tiny")
        assert resp["ok"] is False and "not in the models cache" in resp["error"]

    def test_refuses_symlink_escaping_root(self, cache, tmp_path):
        from fluidvoice import model_catalog
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"secret")
        model_catalog.gguf_dir().mkdir(parents=True)
        link = model_catalog.gguf_path("ggml-base.bin")
        link.symlink_to(outside)
        d = self._daemon()
        resp = self._req(d, "whisper.cpp", "ggml-base.bin")
        assert resp["ok"] is False and "outside the models cache" in resp["error"]
        assert outside.read_bytes() == b"secret"  # untouched

    def test_refuses_while_warmup_running(self, cache):
        from fluidvoice import model_catalog
        decoy = model_catalog.cache_entry_path("faster-whisper", "tiny")
        decoy.mkdir(parents=True)
        d = self._daemon()
        d.warmup = {"running": True, "error": None, "model": "x"}
        resp = self._req(d, "faster-whisper", "tiny")
        assert resp["ok"] is False and "in progress" in resp["error"]
        assert decoy.exists()  # untouched

    def test_status_reports_active_model_key(self, cache):
        d = self._daemon()
        st = d.handle_request({"action": "status"})
        assert st["active_model_key"] == "small"  # live backend identity


# ---------------------------------------------------------------------------
# doctor: models-cache lines
# ---------------------------------------------------------------------------

class TestDoctorModelsCacheLines:
    def test_lines_list_entries_and_total(self, cache):
        from fluidvoice import model_catalog
        from fluidvoice.doctor import _models_cache_lines
        d = model_catalog.cache_entry_path("faster-whisper", "small")
        d.mkdir(parents=True)
        (d / "b").write_bytes(b"x" * 3000)
        lines = _models_cache_lines(cfg())
        assert lines[0].startswith("models cache:")
        assert any("faster-whisper small" in ln and "3 KB" in ln
                   for ln in lines)
        assert any("total: 1 model" in ln for ln in lines)
        assert any("huggingface/hub" in ln for ln in lines)

    def test_active_marker(self, cache):
        from fluidvoice import model_catalog
        from fluidvoice.doctor import _models_cache_lines
        d = model_catalog.cache_entry_path("faster-whisper", "base")
        d.mkdir(parents=True)
        (d / "b").write_bytes(b"x")
        c = cfg(model={"name": "base"})
        lines = _models_cache_lines(c)
        assert any("base" in ln and "ACTIVE" in ln for ln in lines)


# ---------------------------------------------------------------------------
# GTK: Settings → Models rows (skips headless, like tests/test_gtkui.py)
# ---------------------------------------------------------------------------

def _gtk_ok() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except (ImportError, ValueError):
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


pytestmark = []  # module always collectible; GTK guarded per class

if _gtk_ok():
    from gi.repository import Adw, GLib, Gtk

    from fluidvoice.gtkui.client import Client

    Adw.init()
else:
    # Headless (CI, first dispatch 2026-09-12): the GTK test classes below
    # reference Client INSIDE their class bodies, which evaluate during
    # COLLECTION — without this fallback a bare checkout crashes with
    # NameError before the skipif ever runs. object() never executes: the
    # tests skip via _gtk_ok() in every headless environment.
    Client = object


def _pump(loop, ms=120):
    GLib.timeout_add(ms, loop.quit)
    loop.run()


@pytest.mark.needs_display  # GUI lane (Q1): provisioned tier, never the unit gate
@pytest.mark.skipif(not _gtk_ok(), reason="GTK4/Adw or display unavailable")
class TestModelLanguageRows:
    class _Client(Client):
        def __init__(self, overrides=None):
            super().__init__()
            self.saved: list[dict] = []
            self.overrides = overrides or {}
            self.profile_store: dict[str, str] = {}
            self.profile_calls: list[tuple] = []
            self.deleted_models: list[tuple] = []

        def get_config(self):
            c = copy.deepcopy(DEFAULTS)
            for sec, keys in self.overrides.items():
                c[sec].update(keys)
            return c, True

        def set_config(self, body):
            self.saved.append(body)
            changed = [f"{s}.{k}" for s, keys in body.items() for k in keys]
            return {"ok": True, "changed": changed, "rejected": [],
                    "restart_required": [], "errors": [], "note": ""}

        def prompt_profiles(self):
            return {}

        def prompt_profile_save(self, name, prompt):
            return {"ok": True, "error": None, "profiles": {}}

        def prompt_profile_rename(self, old, new):
            return {"ok": True, "error": None, "profiles": {}}

        def prompt_profile_delete(self, name):
            return {"ok": True, "error": None, "profiles": {}}

        def model_delete(self, kind, name):
            self.deleted_models.append((kind, name))
            return {"ok": True, "path": f"/cache/{kind}/{name}",
                    "bytes": 1234}

    @pytest.fixture()
    def loop(self):
        return GLib.MainLoop()

    @pytest.fixture()
    def dl_state(self, monkeypatch):
        """Control which models appear downloaded."""
        from fluidvoice.gtkui import settings_window as sw  # noqa: F401
        from fluidvoice.gtkui.settings_pages import models as spm
        state = {"fw": set(), "gguf": set(), "pk": set()}
        monkeypatch.setattr(spm.model_catalog, "model_downloaded",
                            lambda n: n in state["fw"])
        monkeypatch.setattr(spm.model_catalog, "gguf_downloaded",
                            lambda n: n in state["gguf"])
        monkeypatch.setattr(spm.model_catalog, "parakeet_downloaded",
                            lambda n: n in state["pk"])
        return state

    def _window(self, loop, c):
        from fluidvoice.gtkui.settings_window import SettingsWindow
        w = SettingsWindow(client=c)
        w.present()
        _pump(loop)
        return w

    def test_rows_render_for_downloaded_only(self, loop, dl_state):
        dl_state["fw"] = {"small"}
        dl_state["gguf"] = {"ggml-base.bin"}
        w = self._window(loop, self._Client())
        assert set(w._model_lang_rows) == {"small", "ggml-base.bin"}
        w.close()

    def test_parakeet_v2_skipped(self, loop, dl_state):
        dl_state["pk"] = {"parakeet-tdt-0.6b-v2", "parakeet-tdt-0.6b-v3"}
        w = self._window(loop, self._Client())
        assert set(w._model_lang_rows) == {"parakeet-tdt-0.6b-v3"}
        w.close()

    def test_none_downloaded_hides_group(self, loop, dl_state):
        w = self._window(loop, self._Client())
        assert w._model_lang_rows == {}
        assert w.lang_overrides_group.get_visible() is False
        w.close()

    def test_selecting_code_and_save_posts_languages(self, loop, dl_state):
        dl_state["fw"] = {"small"}
        c = self._Client()
        w = self._window(loop, c)
        values = w._model_lang_values_map["small"]
        row = w._model_lang_rows["small"]
        row.set_selected([v for _l, v in values].index("de"))
        assert w._dirty is True
        w.save()
        assert c.saved[-1]["model"]["languages"] == {"small": "de"}
        w.close()

    def test_saved_selection_restored_and_unknown_code_appended(self, loop,
                                                                dl_state):
        dl_state["fw"] = {"small"}
        c = self._Client({"model": {"languages": {"small": "sl"}}})
        w = self._window(loop, c)
        values = w._model_lang_values_map["small"]
        row = w._model_lang_rows["small"]
        assert [v for _l, v in values][row.get_selected()] == "sl"
        assert w._collect()["model"]["languages"] == {"small": "sl"}
        w.close()

    def test_inherit_drops_key_but_keeps_unshown(self, loop, dl_state):
        dl_state["fw"] = {"small"}
        c = self._Client({"model": {"languages": {"small": "de",
                                                  "tiny": "fr"}}})
        w = self._window(loop, c)
        assert w._collect()["model"]["languages"] == {"small": "de",
                                                      "tiny": "fr"}
        # switch small to inherit -> its key drops, tiny (no row) survives
        w._model_lang_rows["small"].set_selected(0)
        assert w._collect()["model"]["languages"] == {"tiny": "fr"}
        w.close()

    def test_refresh_diff_survives_selections(self, loop, dl_state):
        dl_state["fw"] = {"small"}
        c = self._Client()
        w = self._window(loop, c)
        values = w._model_lang_values_map["small"]
        w._model_lang_rows["small"].set_selected(
            [v for _l, v in values].index("de"))
        dl_state["fw"] = {"small", "base"}
        w._refresh_models()
        assert set(w._model_lang_rows) == {"small", "base"}
        values2 = w._model_lang_values_map["small"]
        assert [v for _l, v in values2][
            w._model_lang_rows["small"].get_selected()] == "de"
        w.close()

    def test_load_reset_resyncs_selections(self, loop, dl_state):
        dl_state["fw"] = {"small"}
        c = self._Client({"model": {"languages": {"small": "de"}}})
        w = self._window(loop, c)
        w._model_lang_rows["small"].set_selected(0)  # user edits to inherit
        w._load()  # Discard-style reload
        values = w._model_lang_values_map["small"]
        assert [v for _l, v in values][
            w._model_lang_rows["small"].get_selected()] == "de"
        assert w._dirty is False
        w.close()


@pytest.mark.needs_display  # GUI lane (Q1): provisioned tier, never the unit gate
@pytest.mark.skipif(not _gtk_ok(), reason="GTK4/Adw or display unavailable")
class TestDiskUsageRows:
    ENTRIES = [
        {"kind": "faster-whisper", "name": "small", "path": "/m/small",
         "bytes": 1_200_000},
        {"kind": "whisper.cpp", "name": "ggml-base.bin",
         "path": "/m/ggml-base.bin", "bytes": 5000},
    ]

    @pytest.fixture()
    def loop(self):
        return GLib.MainLoop()

    @pytest.fixture()
    def client(self):
        return TestModelLanguageRows._Client()

    def _walk(self, widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from self._walk(child)
            child = child.get_next_sibling()

    def _window(self, loop, c, entries=None, monkeypatch=None):
        from fluidvoice.gtkui import settings_window as sw  # noqa: F401
        from fluidvoice.gtkui.settings_pages import models as spm
        from fluidvoice.gtkui.settings_window import SettingsWindow
        mp = monkeypatch
        fake = lambda: list(entries if entries is not None else self.ENTRIES)
        if mp is not None:
            mp.setattr(spm.model_catalog, "cached_models", fake)
        w = SettingsWindow(client=c)
        w.present()
        _pump(loop)
        return w

    def test_rows_render_with_sizes_and_total(self, loop, client, monkeypatch):
        w = self._window(loop, client, monkeypatch=monkeypatch)
        titles = [r.get_title() for r in w._disk_rows]
        assert titles == ["small", "ggml-base.bin"]
        assert "1 MB" in w._disk_rows[0].get_subtitle()
        assert "5 KB" in w._disk_rows[1].get_subtitle()
        assert "2 models" in w.disk_total_row.get_subtitle()
        assert "1 MB" in w.disk_total_row.get_subtitle()
        w.close()

    def test_active_model_delete_insensitive_with_tooltip(
            self, loop, client, monkeypatch):
        c = client
        c.overrides = {"model": {"name": "small"}}
        w = self._window(loop, c, monkeypatch=monkeypatch)
        row = next(r for r in w._disk_rows if r.get_title() == "small")
        btn = next(x for x in self._walk(row) if isinstance(x, Gtk.Button))
        assert btn.get_sensitive() is False
        assert btn.get_tooltip_text() == "This is the active model"
        w.close()

    def test_delete_posts_only_after_confirmation(
            self, loop, client, monkeypatch):
        w = self._window(loop, client, monkeypatch=monkeypatch)
        assert client.deleted_models == []
        w._on_delete_model_response(None, "cancel", self.ENTRIES[1])
        assert client.deleted_models == []
        w._on_delete_model_response(None, "delete", self.ENTRIES[1])
        assert pump_until_local(loop, lambda: client.deleted_models)
        assert client.deleted_models == [("whisper.cpp", "ggml-base.bin")]
        w.close()

    def test_delete_daemon_down_toasts_hint(self, loop, client, monkeypatch):
        from fluidvoice.gtkui.client import ClientError
        c = client

        def down(kind, name):
            raise ClientError("connection refused")

        c.model_delete = down
        w = self._window(loop, client, monkeypatch=monkeypatch)
        toasts: list[str] = []
        monkeypatch.setattr(w, "toast",
                            lambda text, timeout=5: toasts.append(text))
        w._on_delete_model_response(None, "delete", self.ENTRIES[1])
        assert pump_until_local(
            loop, lambda: any("daemon not running" in t for t in toasts))
        w.close()


def pump_until_local(loop, cond, timeout_s=2.0):
    import time
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        _pump(loop, ms=20)
    return cond()
