"""`ensure_wav` format handling (pure unit: ffmpeg/PyAV monkeypatched)."""
from __future__ import annotations

import shutil
import subprocess
import wave

import pytest

from fluidvoice.audio_utils import SUPPORTED_AUDIO_EXTS, AudioFormatError, ensure_wav
from tests.test_daemon import make_wav


class FakeProc:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def _fake_run(calls, returncode=0, stderr=""):
    def fake(cmd, **kw):
        calls.append(cmd)
        return FakeProc(returncode, stderr)
    return fake


class TestEnsureWav:
    def test_wav_passthrough_no_subprocess(self, tmp_path, monkeypatch):
        wav = make_wav(tmp_path / "in.wav")
        calls = []
        monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run",
                            _fake_run(calls))
        out = ensure_wav(wav)
        assert out == wav and calls == []

    def test_pyav_decodable_passthrough(self, tmp_path, monkeypatch):
        opus = tmp_path / "in.opus"
        opus.write_bytes(b"fake-opus-bytes")
        monkeypatch.setattr("fluidvoice.audio_utils._pyav_decodable",
                            lambda p: True)
        calls = []
        monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run",
                            _fake_run(calls))
        out = ensure_wav(opus)
        assert out == opus and calls == []

    def test_ffmpeg_conversion_command(self, tmp_path, monkeypatch):
        src = tmp_path / "in.amr"
        src.write_bytes(b"not-decodable")
        monkeypatch.setattr("fluidvoice.audio_utils._pyav_decodable",
                            lambda p: False)
        monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/ffmpeg")
        calls = []
        monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run",
                            _fake_run(calls))
        out = ensure_wav(src)
        cmd = calls[0]
        assert cmd[0] == "/usr/bin/ffmpeg"
        for flag in ("-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y"):
            assert flag in cmd
        assert str(src) in cmd
        assert out != src and out.name == "in.16k.wav"
        assert out.suffix == ".wav" and out.parent != tmp_path  # temp dir
        shutil.rmtree(out.parent)

    def test_ffmpeg_failure_raises(self, tmp_path, monkeypatch):
        src = tmp_path / "in.xyz"
        src.write_bytes(b"x")
        monkeypatch.setattr("fluidvoice.audio_utils._pyav_decodable",
                            lambda p: False)
        monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/ffmpeg")
        monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run",
                            _fake_run([], returncode=1, stderr="boom"))
        with pytest.raises(AudioFormatError) as ei:
            ensure_wav(src)
        assert "boom" in str(ei.value)

    def test_missing_ffmpeg_error_names_ffmpeg(self, tmp_path, monkeypatch):
        src = tmp_path / "in.abc"
        src.write_bytes(b"x")
        monkeypatch.setattr("fluidvoice.audio_utils._pyav_decodable",
                            lambda p: False)
        monkeypatch.setattr(shutil, "which", lambda n: None)
        with pytest.raises(AudioFormatError) as ei:
            ensure_wav(src)
        assert "ffmpeg" in str(ei.value)

    def test_force_wav_reencodes(self, tmp_path, monkeypatch):
        wav = make_wav(tmp_path / "in.wav")
        monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/ffmpeg")
        calls = []
        monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run",
                            _fake_run(calls))
        out = ensure_wav(wav, force=True)
        assert out != wav and out.name == "in.16k.wav"
        assert calls and str(wav) in calls[0]
        shutil.rmtree(out.parent)

    def test_force_wav_without_ffmpeg_passthrough(self, tmp_path, monkeypatch):
        wav = make_wav(tmp_path / "in.wav")
        monkeypatch.setattr(shutil, "which", lambda n: None)
        out = ensure_wav(wav, force=True)
        assert out == wav

    def test_supported_exts_are_the_verified_set(self):
        assert SUPPORTED_AUDIO_EXTS == frozenset({
            ".wav", ".flac", ".mp3", ".opus", ".oga", ".ogg",
            ".m4a", ".aac", ".wma", ".aiff", ".aif", ".webm"})

    @pytest.mark.skipif(not shutil.which("ffmpeg"),
                        reason="ffmpeg not installed")
    def test_real_ffmpeg_flac_roundtrip(self, tmp_path):
        """flac -> forced 16k mono WAV via the real binary."""
        wav = make_wav(tmp_path / "src.wav")
        flac = tmp_path / "src.flac"
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(wav),
                        "-y", str(flac)], check=True, capture_output=True)
        out = ensure_wav(flac, force=True)
        assert out != flac
        try:
            with wave.open(str(out), "rb") as wf:
                assert wf.getframerate() == 16000
                assert wf.getnchannels() == 1
        finally:
            shutil.rmtree(out.parent, ignore_errors=True)
