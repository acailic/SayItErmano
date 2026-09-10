"""Remote OpenAI-compatible STT backend (model.remote_url) — config
surface, wire format against a threaded stdlib fake server, selection
seam, retry/error table, secrets hygiene, doctor, and the daemon
local-model interplay. Hermetic: no real network beyond 127.0.0.1."""
from __future__ import annotations

import copy
import json
import math
import struct
import time
from pathlib import Path

import pytest

from fluidvoice import backends
from fluidvoice.audio_utils import raw_to_wav_bytes
from fluidvoice.config import (
    DEFAULTS,
    apply_settings,
    coerce_setting,
    load_config,
    mask_secrets,
    save_config,
)
from tests.fake_remote_stt_server import FakeRemoteSttServer

SECRET = "sk-test-DO-NOT-PRINT-9f1c"


def make_wav(path: Path) -> Path:
    """1 s of 440 Hz sine as a real 16 kHz mono s16 WAV (RIFF magic)."""
    rate = 16000
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(
            2 * math.pi * 440 * i / rate)))
        for i in range(rate))
    with open(path, "wb") as fh:
        fh.write(raw_to_wav_bytes(frames, rate))
    return path


def remote_cfg(url: str, **model_over) -> dict:
    c = copy.deepcopy(DEFAULTS)
    c["model"]["remote_url"] = url
    c["model"].update(model_over)
    return c


# ---------------------------------------------------------------------------
# Phase 1 — config surface
# ---------------------------------------------------------------------------

class TestRemoteConfig:
    def test_defaults(self):
        m = DEFAULTS["model"]
        assert m["remote_url"] == ""
        assert m["remote_model"] == "whisper-large-v3"
        assert m["remote_api_key"] == ""
        assert m["remote_timeout_s"] == 30

    def test_coerce_url_accepts(self):
        ok, v = coerce_setting("model", "remote_url", "http://h:1")
        assert ok and v == "http://h:1"
        ok, v = coerce_setting("model", "remote_url",
                               "https://x/y/v1/audio/transcriptions")
        assert ok
        ok, v = coerce_setting("model", "remote_url", "  ")
        assert ok and v == ""  # empty = off
        ok, v = coerce_setting("model", "remote_url", " http://h:1 ")
        assert ok and v == "http://h:1"  # stripped

    def test_coerce_url_rejects(self):
        for bad in ("ftp://x", "not a url", "http://",
                    "http://a b/", "x" * 2049):
            assert not coerce_setting("model", "remote_url", bad)[0], bad

    def test_coerce_timeout_range(self):
        assert coerce_setting("model", "remote_timeout_s", 30)[0]
        for bad in (4, 601, "fast"):
            assert not coerce_setting("model", "remote_timeout_s", bad)[0], bad

    def test_coerce_api_key_any_string(self):
        assert coerce_setting("model", "remote_api_key", "")[0]
        assert coerce_setting("model", "remote_api_key", SECRET)[0]
        assert not coerce_setting("model", "remote_api_key", 42)[0]
        assert not coerce_setting("model", "remote_api_key", "k" * 4097)[0]

    def test_backend_enum_accepts_remote(self):
        assert coerce_setting("model", "backend", "remote")[0]
        assert not coerce_setting("model", "backend", "remote-x")[0]

    def test_apply_settings_roundtrip(self):
        cfg = copy.deepcopy(DEFAULTS)
        changed, rejected = apply_settings(cfg, {"model": {
            "remote_url": "http://lan:8000",
            "remote_model": "whisper-large-v2",
            "remote_api_key": SECRET,
            "remote_timeout_s": 45,
        }})
        assert not rejected
        assert {"model.remote_url", "model.remote_model",
                "model.remote_api_key",
                "model.remote_timeout_s"} <= set(changed)
        assert cfg["model"]["remote_url"] == "http://lan:8000"
        assert cfg["model"]["remote_model"] == "whisper-large-v2"
        assert cfg["model"]["remote_timeout_s"] == 45.0

    def test_apply_settings_rejects_garbage(self):
        cfg = copy.deepcopy(DEFAULTS)
        _, rejected = apply_settings(cfg, {"model": {
            "remote_url": "ftp://nope",
            "remote_timeout_s": 1,
            "remote_model": "x" * 257,
        }})
        assert {"model.remote_url", "model.remote_timeout_s",
                "model.remote_model"} <= set(rejected)

    def test_save_keeps_key_and_uses_empty_url_as_off(self, tmp_path):
        path = tmp_path / "config.toml"
        cfg = copy.deepcopy(DEFAULTS)
        cfg["model"].update(remote_url="http://lan:8000",
                            remote_api_key=SECRET)
        save_config(cfg, path)  # file now has both
        # a later save that POSTS remote_url = "" (UI cleared it) but omits
        # the key entirely must persist OFF while keeping the key
        cfg2 = copy.deepcopy(DEFAULTS)
        cfg2["model"]["remote_url"] = ""
        save_config(cfg2, path)
        on_disk = path.read_text()
        assert "remote_url" not in on_disk  # off, not carried over
        # the key is CARRIED OVER from the file (same rule as ai.api_key)
        assert f'remote_api_key = "{SECRET}"' in on_disk
        assert load_config(path)["model"]["remote_url"] == ""

    def test_mask_secrets_hides_remote_key(self):
        safe = mask_secrets(remote_cfg("http://x", remote_api_key=SECRET))
        assert safe["model"]["remote_api_key"] is True
        assert SECRET not in json.dumps(safe)

    def test_engine_keys_cover_remote(self):
        for k in ("model.remote_url", "model.remote_model",
                  "model.remote_api_key", "model.remote_timeout_s"):
            from fluidvoice.config import ENGINE_KEYS
            assert k in ENGINE_KEYS


# ---------------------------------------------------------------------------
# Phase 2 — backend roundtrip + multipart shape + errors + selection
# ---------------------------------------------------------------------------

class TestRemoteBackendRoundtrip:
    def test_load_backend_selects_remote(self):
        with FakeRemoteSttServer() as srv:
            be = backends.load_backend(remote_cfg(srv.url))
            assert be.name == "remote"
            assert be.model_name == "whisper-large-v3"
            assert be.endpoint == srv.url + "/v1/audio/transcriptions"

    def test_transcribe_roundtrip(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer() as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            out = be.transcribe(wav)
            assert out["text"] == "hello remote world"
            assert out["language"] is None  # auto -> omitted -> None
            assert out["segments"] == []
            assert srv.count == 1

    def test_verbose_json_segments_concatenate(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer(mode="verbose_json") as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            out = be.transcribe(wav)
            assert out["text"] == "a b"
            assert isinstance(out["segments"], list) and out["segments"]

    def test_endpoint_normalization(self):
        be = backends.RemoteSttBackend(
            remote_cfg("https://gpu:8443/v1/audio/transcriptions"))
        assert be.endpoint == "https://gpu:8443/v1/audio/transcriptions"


class TestMultipartShape:
    def test_file_model_fields(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer() as srv:
            be = backends.RemoteSttBackend(
                remote_cfg(srv.url, remote_model="my-model"))
            be.transcribe(wav)
            r = srv.requests[0]
            assert r["content_type"].startswith("multipart/form-data")
            assert "file" in r["fields"] and "model" in r["fields"]
            assert r["file_len"] > 0
            assert r["file_head"] == b"RIFF"
            assert r["filename"] == "audio.wav"
            assert r["model"] == "my-model"
            assert "language" not in r["fields"]  # auto default

    def test_language_field_direct_and_via_overrides(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer() as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            be.transcribe(wav, language="de")
            assert srv.requests[0]["language"] == "de"
            # per-model override keyed by the remote model name
            cfg = remote_cfg(srv.url)
            cfg["model"]["languages"] = {"whisper-large-v3": "de"}
            be2 = backends.RemoteSttBackend(cfg)
            assert be2.language == "de"
            be2.transcribe(wav)
            assert srv.requests[1]["language"] == "de"

    def test_bearer_only_with_key(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer() as srv:
            backends.RemoteSttBackend(remote_cfg(srv.url)).transcribe(wav)
            assert srv.requests[0]["authorization"] is None
            backends.RemoteSttBackend(
                remote_cfg(srv.url, remote_api_key=SECRET)).transcribe(wav)
            assert srv.requests[1]["authorization"] == f"Bearer {SECRET}"


class TestRemoteErrorTable:
    def test_connection_refused(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        srv = FakeRemoteSttServer().start()
        port = srv.port
        srv.stop()
        be = backends.RemoteSttBackend(
            remote_cfg(f"http://127.0.0.1:{port}", remote_timeout_s=5))
        from fluidvoice.backends.remote_stt import RemoteSttError
        with pytest.raises(RemoteSttError) as ei:
            be.transcribe(wav)
        assert "127.0.0.1" in str(ei.value)  # host named, never the key

    def test_timeout(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer(mode="slow") as srv:
            be = backends.RemoteSttBackend(
                remote_cfg(srv.url, remote_timeout_s=0.3))
            from fluidvoice.backends.remote_stt import RemoteSttError
            t0 = time.monotonic()
            with pytest.raises(RemoteSttError):
                be.transcribe(wav)
            assert time.monotonic() - t0 < 4  # no hang

    def test_500_then_success_retries_once(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer(mode="http500") as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            with pytest.raises(Exception):
                be.transcribe(wav)
            assert srv.count == 2  # exactly 2 attempts
            srv.set_mode("default")
            assert be.transcribe(wav)["text"] == "hello remote world"
            assert srv.count == 3

    def test_401_no_retry(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer(mode="http401") as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            with pytest.raises(Exception) as ei:
                be.transcribe(wav)
            assert srv.count == 1  # config errors don't retry
            assert "401" in str(ei.value)

    def test_bad_json_and_missing_text(self, tmp_path):
        from fluidvoice.backends.remote_stt import RemoteSttError
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer(mode="bad_json") as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            with pytest.raises(RemoteSttError):
                be.transcribe(wav)
            assert srv.count == 1
        with FakeRemoteSttServer(mode="missing_text") as srv:
            be = backends.RemoteSttBackend(remote_cfg(srv.url))
            with pytest.raises(RemoteSttError, match="no 'text'"):
                be.transcribe(wav)

    def test_error_messages_never_leak_the_key(self, tmp_path):
        wav = make_wav(tmp_path / "a.wav")
        with FakeRemoteSttServer(mode="http401") as srv:
            be = backends.RemoteSttBackend(
                remote_cfg(srv.url, remote_api_key=SECRET))
            with pytest.raises(Exception) as ei:
                be.transcribe(wav)
            assert SECRET not in str(ei.value)


class TestRemoteSelection:
    @pytest.fixture()
    def fakes(self, monkeypatch):
        """Same pattern as tests/test_backends_selection.py: prove no local
        class constructs while remote_url is set."""
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

    def test_remote_url_wins_over_auto_and_explicit_local(self, monkeypatch,
                                                          fakes):
        monkeypatch.setattr(backends, "_import_ok", lambda m: True)
        monkeypatch.setattr(backends, "preload_cuda_libs", lambda: True)
        for backend in ("auto", "faster-whisper"):
            c = remote_cfg("http://127.0.0.1:1", backend=backend)
            assert backends.load_backend(c).name == "remote"
        assert fakes == []  # no local class ever constructed

    def test_explicit_remote_without_url_raises(self):
        with pytest.raises(RuntimeError, match="model.remote_url"):
            backends.load_backend(remote_cfg("", backend="remote"))

    def test_no_request_when_unconfigured(self, monkeypatch):
        with FakeRemoteSttServer() as srv:
            from fluidvoice.backends import faster_whisper_backend as fw

            class FakeFW:
                name = "faster-whisper"

                def __init__(self, c):
                    pass

            monkeypatch.setattr(fw, "FasterWhisperBackend", FakeFW)
            monkeypatch.setattr(backends, "_import_ok", lambda m: True)
            c = copy.deepcopy(DEFAULTS)
            c["model"]["backend"] = "faster-whisper"
            assert backends.load_backend(c).name == "faster-whisper"  # local
            assert backends.resolved_backend_name(c) != "remote"
            assert srv.count == 0  # nothing was sent anywhere

    def test_preview_transcriber_is_none_for_remote(self):
        from fluidvoice import preview
        be = backends.RemoteSttBackend(remote_cfg("http://127.0.0.1:1"))
        assert preview.preview_transcriber(
            copy.deepcopy(DEFAULTS), be, "en") is None

    def test_model_key_and_resolved_name(self):
        c = remote_cfg("http://lan:8000", remote_model="para-8b")
        assert backends.config_model_key(c) == "para-8b"
        assert backends.resolved_backend_name(c) == "remote"
        assert backends.LANGUAGE_GUARD["remote"]
        # explicit remote without url still resolves (constructor explains)
        c2 = remote_cfg("", backend="remote")
        assert backends.resolved_backend_name(c2) == "remote"
        # unconfigured: unchanged behavior
        assert backends.config_model_key(
            copy.deepcopy(DEFAULTS)) in (None, "small", "base")


# ---------------------------------------------------------------------------
# Phase 3 — doctor lines + daemon local-model interplay
# ---------------------------------------------------------------------------

class TestDoctorLines:
    def test_unconfigured_single_line_no_probe(self):
        from fluidvoice import doctor
        lines = doctor._remote_stt_lines(copy.deepcopy(DEFAULTS))
        assert lines == ["  not configured (model.remote_url) - local "
                         "models only"]

    def test_configured_reachable(self):
        from fluidvoice import doctor
        with FakeRemoteSttServer() as srv:
            lines = doctor._remote_stt_lines(
                remote_cfg(srv.url, remote_api_key=SECRET,
                           remote_model="whisper-large-v3"))
        text = "\n".join(lines)
        assert f"endpoint: {srv.url}" in text
        assert "model: whisper-large-v3" in text
        assert "key: set" in text
        assert "reachable" in text
        assert "/v1/audio/transcriptions" in text
        assert SECRET not in text  # never the key

    def test_configured_unreachable(self):
        from fluidvoice import doctor
        srv = FakeRemoteSttServer().start()
        url = srv.url
        srv.stop()
        lines = doctor._remote_stt_lines(remote_cfg(url))
        assert any("unreachable" in ln for ln in lines)

    def test_doctor_never_prints_the_key(self):
        from fluidvoice import doctor
        with FakeRemoteSttServer() as srv:
            lines = doctor._remote_stt_lines(
                remote_cfg(srv.url, remote_api_key=SECRET))
        for line in lines:
            assert SECRET not in line


class TestDaemonWarmupClearsRemote:
    def test_warmup_local_model_clears_remote_url(self, monkeypatch,
                                                  tmp_path):
        import fluidvoice.daemon as dm
        from tests.test_daemon import StubRecorder

        class FakeLocal:
            name = "faster-whisper"

            def __init__(self, c):
                assert not c["model"].get("remote_url"), \
                    "local backend must never see a remote_url cfg"

            def warmup(self):
                pass

        monkeypatch.setattr(dm.backends, "load_backend",
                            lambda c: FakeLocal(c))
        import fluidvoice.config as config_mod
        saved: list = []
        monkeypatch.setattr(config_mod, "save_config",
                            lambda c, p=None: saved.append(c))
        cfg = remote_cfg("http://127.0.0.1:1")
        d = dm.Daemon(cfg, recorder=StubRecorder(),
                      backend_factory=lambda c: FakeLocal(c),
                      use_hotkey=False, use_sounds=False)
        d._engines.warmup_model("small")
        assert d.cfg["model"]["remote_url"] == ""  # cleared in place
        assert d.backend.name == "faster-whisper"  # local hot-swapped in
        assert saved and saved[0]["model"]["remote_url"] == ""  # persisted


class TestStandaloneServer:
    def test_error_mode_banner_does_not_crash(self):
        """D4 (audit): the standalone main() banner crashed on non-JSON
        modes (http500/http401) - the unit tests only used the in-process
        server. Run the real CLI and expect a listening line + a refusal."""
        import subprocess
        import sys
        import urllib.error
        import urllib.request
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent /
                                 "fake_remote_stt_server.py"),
             "0", "--mode", "http500"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            line = proc.stdout.readline()
            assert "listening on" in line and "http500" in line
            assert "transcribes to" not in line   # old crash point
            url = line.split("listening on ")[1].strip().split(" ")[0]
            try:
                urllib.request.urlopen(
                    urllib.request.Request(url + "/v1/audio/transcriptions",
                                           data=b"x"), timeout=3)
            except urllib.error.HTTPError as e:
                assert e.code == 500
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            proc.stdout.close()  # -W error gate: no unclosed-pipe ResourceWarning
