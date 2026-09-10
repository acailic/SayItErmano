"""`fluidvoice transcribe` CLI: formats, --json, --out (stubbed backend)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fluidvoice import backends, cli
from fluidvoice.cli import LARGE_INPUT_BYTES
from fluidvoice.config import DEFAULTS
from fluidvoice.processing import post_process
from tests.test_daemon import StubBackend, make_wav


class TranscribeStub(StubBackend):
    """StubBackend plus segments - mirrors the new backend contract."""

    def __init__(self, result=None, **kw):
        super().__init__(**kw)
        self.full = result if result is not None else {
            "text": self.text, "language": "en", "duration": 1.5,
            "segments": [{"start": 0.0, "end": 1.5, "text": self.text}]}

    def transcribe(self, wav, language=None):
        self.calls.append((str(wav), language))
        return copy.deepcopy(self.full)


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    """Stub backend + default config; returns the stub to customize."""
    stub = TranscribeStub()
    monkeypatch.setattr(backends, "load_backend", lambda cfg: stub)
    monkeypatch.setattr(cli, "load_config", lambda p=None: copy.deepcopy(DEFAULTS))
    return stub


class TestPlain:
    def test_no_process_raw_text(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "utt.wav")
        rc = cli.main(["transcribe", str(wav), "--no-process"])
        out = capsys.readouterr()
        assert rc == 0 and out.out.strip() == "hello world"

    def test_default_post_processing_applied(self, patched, tmp_path, capsys):
        patched.full = {"text": "um hello literal comma world", "language": "en",
                        "duration": 1.0, "segments": []}
        wav = make_wav(tmp_path / "utt.wav")
        rc = cli.main(["transcribe", str(wav)])
        out = capsys.readouterr()
        expected = post_process("um hello literal comma world",
                                copy.deepcopy(DEFAULTS))
        assert rc == 0 and out.out.strip() == expected


class TestJson:
    def test_payload_keys(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "utt.wav")
        rc = cli.main(["transcribe", str(wav), "--no-process", "--json"])
        out = capsys.readouterr()
        assert rc == 0
        payload = json.loads(out.out)
        assert payload == {"text": "hello world", "language": "en",
                           "duration_s": 1.5,
                           "segments": [{"start": 0.0, "end": 1.5,
                                         "text": "hello world"}]}

    def test_stub_without_segments_key(self, monkeypatch, tmp_path, capsys):
        stub = StubBackend("legacy backend")
        import fluidvoice.backends as bm
        monkeypatch.setattr(bm, "load_backend", lambda cfg: stub)
        monkeypatch.setattr(cli, "load_config", lambda p=None: copy.deepcopy(DEFAULTS))
        wav = make_wav(tmp_path / "utt.wav")
        rc = cli.main(["transcribe", str(wav), "--no-process", "--json"])
        out = capsys.readouterr()
        assert rc == 0
        payload = json.loads(out.out)
        assert payload["text"] == "legacy backend"
        assert payload["segments"] == []

    def test_transcribed_exactly_once(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "big.wav")
        rc = cli.main(["transcribe", str(wav), "--no-process", "--json"])
        capsys.readouterr()
        assert rc == 0 and len(patched.calls) == 1


class TestOut:
    def test_out_plain(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "utt.wav")
        dest = tmp_path / "res.txt"
        rc = cli.main(["transcribe", str(wav), "--no-process", "--out", str(dest)])
        out = capsys.readouterr()
        assert rc == 0 and out.out == ""
        assert dest.read_text() == "hello world\n"
        assert "wrote" in out.err and str(dest) in out.err

    def test_out_json(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "utt.wav")
        dest = tmp_path / "res.json"
        rc = cli.main(["transcribe", str(wav), "--no-process", "--json",
                       "--out", str(dest)])
        out = capsys.readouterr()
        assert rc == 0
        payload = json.loads(dest.read_text())
        assert payload["text"] == "hello world"
        assert out.out == "" and "wrote" in out.err  # confirmation on stderr only

    def test_out_creates_missing_dirs(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "utt.wav")
        dest = tmp_path / "a" / "b" / "c" / "res.txt"
        rc = cli.main(["transcribe", str(wav), "--no-process", "--out", str(dest)])
        capsys.readouterr()
        assert rc == 0 and dest.exists()


class TestWarningsAndErrors:
    def test_large_input_warns_but_transcribes(self, patched, tmp_path, capsys):
        wav = make_wav(tmp_path / "huge.wav")
        with open(wav, "ab") as fh:  # sparse-ish tail past the 25 MB gate
            fh.truncate(LARGE_INPUT_BYTES + 1)
        rc = cli.main(["transcribe", str(wav), "--no-process"])
        out = capsys.readouterr()
        assert rc == 0 and "hello world" in out.out
        assert "warning" in out.err and "ffmpeg" in out.err
        assert len(patched.calls) == 1

    def test_missing_file_rc1(self, patched, tmp_path, capsys):
        rc = cli.main(["transcribe", str(tmp_path / "nope.wav")])
        out = capsys.readouterr()
        assert rc == 1 and "not found" in out.err

    def test_unlisted_extension_tried_anyway(self, patched, tmp_path, capsys,
                                             monkeypatch):
        # x.amr: not in SUPPORTED_AUDIO_EXTS; probe decides passthrough/ffmpeg.
        src = tmp_path / "x.amr"
        src.write_bytes(b"\x00" * 300)
        monkeypatch.setattr("fluidvoice.audio_utils._pyav_decodable",
                            lambda p: True)
        rc = cli.main(["transcribe", str(src), "--no-process"])
        out = capsys.readouterr()
        assert rc == 0 and "not a verified format" in out.err
        assert len(patched.calls) == 1

    def test_conversion_tempdir_cleaned(self, monkeypatch, tmp_path, capsys):

        made = {"n": 0}

        def fake_mkdtemp(prefix=""):
            made["n"] += 1
            d = tmp_path / f"tmp{made['n']}"
            d.mkdir()
            return str(d)

        stub = TranscribeStub()
        monkeypatch.setattr(backends, "load_backend", lambda cfg: stub)
        monkeypatch.setattr(cli, "load_config",
                            lambda p=None: copy.deepcopy(DEFAULTS))
        monkeypatch.setattr("fluidvoice.audio_utils._pyav_decodable",
                            lambda p: False)
        monkeypatch.setattr("fluidvoice.audio_utils.shutil.which",
                            lambda n: "/usr/bin/ffmpeg")
        monkeypatch.setattr("fluidvoice.audio_utils.tempfile.mkdtemp",
                            fake_mkdtemp)

        def fake_run(cmd, **kw):
            out = Path(cmd[-1])
            out.write_bytes(b"")
            return type("P", (), {"returncode": 0, "stderr": ""})()

        monkeypatch.setattr("fluidvoice.audio_utils.subprocess.run", fake_run)
        src = tmp_path / "x.opus"
        src.write_bytes(b"\x00" * 300)
        rc = cli.main(["transcribe", str(src), "--no-process"])
        capsys.readouterr()
        assert rc == 0
        assert not (tmp_path / "tmp1").exists()  # converted dir swept
        assert src.exists()  # original untouched
