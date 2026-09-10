"""Chunked file transcription (P3): slicing, merge/dedup, convert-once,
limits, golden output shapes (stub backend — no model, no ffmpeg)."""
from __future__ import annotations

import copy
import wave
from pathlib import Path

import pytest

from fluidvoice import chunking
from fluidvoice.backends.base import Transcript, TranscriptSegment
from fluidvoice.config import DEFAULTS


def make_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    """Fast synthetic WAV: silence is fine — the file path has no gates."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\0" * int(rate * seconds) * 2)
    return path


class SeqBackend:
    """Stub returning one prepared result per transcribe() call, in order,
    recording the wavs it saw (chunk order = call order)."""

    name = "stub"
    capabilities = None

    def __init__(self, results: list):
        self.results = [copy.deepcopy(r) for r in results]
        self.calls: list[Path] = []

    def transcribe(self, wav, language=None):
        self.calls.append(Path(wav))
        return copy.deepcopy(self.results[len(self.calls) - 1])


def seg(start, end, text, **kw):
    return TranscriptSegment(start=start, end=end, text=text, **kw)


class TestSliceWav:
    def test_three_chunks_starts_and_ends(self, tmp_path):
        wav = make_wav(tmp_path / "src.wav", 1500.0)  # 25 min
        chunks = chunking.slice_wav(wav, tmp_path / "out")
        step = chunking.CHUNK_SECONDS - chunking.CHUNK_OVERLAP_SECONDS
        assert len(chunks) == 3
        assert [c.start for c in chunks] == [0.0, step, 2 * step]
        assert chunks[-1].end == pytest.approx(1500.0, abs=0.01)
        for c in chunks:  # each slice is itself a readable WAV
            with wave.open(str(c.path), "rb") as wf:
                assert wf.getframerate() == 16000
                expect = c.duration if c is not chunks[-1] \
                    else chunks[-1].duration
                assert wf.getnframes() / 16000.0 == \
                    pytest.approx(expect, abs=0.01)

    def test_short_tail_folded_into_previous(self, tmp_path):
        # duration = 2*step + 0.3: the 0.3 s remainder is covered already
        step = chunking.CHUNK_SECONDS - chunking.CHUNK_OVERLAP_SECONDS
        wav = make_wav(tmp_path / "src.wav", 2 * step + 0.3)
        chunks = chunking.slice_wav(wav, tmp_path / "out")
        assert len(chunks) == 2
        assert chunks[-1].end == pytest.approx(2 * step + 0.3, abs=0.01)

    def test_tail_under_overlap_covered_by_previous(self, tmp_path):
        # duration = 2*step + 0.7: no third chunk — chunk 2 already ends
        # at the source end (every emitted chunk is >= overlap long)
        step = chunking.CHUNK_SECONDS - chunking.CHUNK_OVERLAP_SECONDS
        wav = make_wav(tmp_path / "src.wav", 2 * step + 0.7)
        chunks = chunking.slice_wav(wav, tmp_path / "out")
        assert len(chunks) == 2
        assert chunks[-1].end == pytest.approx(2 * step + 0.7, abs=0.01)
        assert all(c.duration >= chunking.CHUNK_OVERLAP_SECONDS
                   for c in chunks)


class TestMerge:
    def _run(self, texts_a, texts_b, total=1200.0):
        # A's last segment sits at ~599 s — inside the overlap window;
        # B's segments are chunk-local (598.5 + local start is global)
        chunks = [chunking.Chunk(Path("/a"), 0.0, 600.0),
                  chunking.Chunk(Path("/b"), 598.5, 1200.0)]
        results = [
            Transcript(text=" ".join(texts_a), language="en", duration=600.0,
                       segments=tuple(
                           seg(598.8 if i == len(texts_a) - 1 else 10.0 * i,
                              606.0 if i == len(texts_a) - 1 else 8.0 + 10 * i,
                              t)
                           for i, t in enumerate(texts_a))),
            Transcript(text=" ".join(texts_b), language="en", duration=601.5,
                       segments=tuple(
                           seg(0.3 + 10 * i, 8.0 + 10 * i, t)
                           for i, t in enumerate(texts_b))),
        ]
        return chunking.merge_transcripts(chunks, results, total)

    def test_duplicate_in_overlap_dropped(self):
        # "boundary sentence" decoded in both chunks' overlap window
        merged = self._run(["early words", "boundary sentence"],
                           ["boundary sentence", "later words"])
        texts = [s.text for s in merged.segments]
        assert texts.count("boundary sentence") == 1
        assert "early words" in texts and "later words" in texts
        assert merged.text == "early words boundary sentence later words"

    def test_duplicate_shifted_but_within_tolerance(self):
        # same utterance, decoders disagree by < 2*overlap on the onset
        chunks = [chunking.Chunk(Path("/a"), 0.0, 600.0),
                  chunking.Chunk(Path("/b"), 598.5, 1200.0)]
        results = [
            Transcript(text="x", segments=(seg(599.0, 605.0, "same words"),)),
            Transcript(text="x", segments=(seg(1.9, 7.0, "same words"),)),
        ]
        merged = chunking.merge_transcripts(chunks, results, 1200.0)
        assert [s.text for s in merged.segments] == ["same words"]

    def test_overlapping_but_different_text_kept(self):
        # conservative rule: near in time but NOT equal text -> keep both
        merged = self._run(["cut off wo"], ["cut off wording continues"])
        texts = [s.text for s in merged.segments]
        assert "cut off wo" in texts and "cut off wording continues" in texts

    def test_global_timestamps_monotonic(self):
        merged = self._run(["a", "b"], ["c", "d"])
        starts = [s.start for s in merged.segments]
        assert starts == sorted(starts)
        assert merged.segments[0].start >= 0.0

    def test_no_segments_falls_back_to_chunk_texts(self):
        chunks = [chunking.Chunk(Path("/a"), 0.0, 600.0),
                  chunking.Chunk(Path("/b"), 598.5, 1200.0)]
        results = [Transcript(text="first part ", language=None),
                   Transcript(text=" second part", language=None)]
        merged = chunking.merge_transcripts(chunks, results, 1200.0)
        assert merged.segments == ()
        assert merged.text == "first part second part"
        assert merged.duration == 1200.0  # exact global, not a sum


class TestTranscribeLong:
    def test_short_file_single_shot_unchanged(self, tmp_path, monkeypatch):
        wav = make_wav(tmp_path / "short.wav", 5.0)
        calls = {"ensure": 0}

        def counting_ensure(path, dest_dir=None, force=False):
            calls["ensure"] += 1
            return chunking.ensure_wav.__wrapped__(path, dest_dir, force) \
                if hasattr(chunking.ensure_wav, "__wrapped__") else path

        monkeypatch.setattr(chunking, "ensure_wav", counting_ensure)
        backend = SeqBackend([{"text": "hi", "language": "en",
                               "duration": 5.0, "segments": []}])
        result = chunking.transcribe_long(backend, wav, "en")
        assert result.text == "hi"
        assert len(backend.calls) == 1 and backend.calls[0] == wav

    def test_long_file_two_chunks_merged(self, tmp_path):
        wav = make_wav(tmp_path / "long.wav", 900.0)  # 15 min
        dup = "spoken across the seam"
        backend = SeqBackend([
            Transcript(text=f"first half {dup}", language="en",
                       duration=600.0,
                       segments=(seg(10.0, 20.0, "first half"),
                                 seg(598.8, 600.0, dup))),
            Transcript(text=f"{dup} second half", language="en",
                       duration=301.5,
                       segments=(seg(0.3, 2.0, dup),
                                 seg(30.0, 40.0, "second half"))),
        ])
        result = chunking.transcribe_long(backend, wav, "en")
        assert len(backend.calls) == 2
        # chunk 2 starts at 598.5: its 30 s segment lands at 628.5 global
        texts = [s.text for s in result.segments]
        assert texts == ["first half", dup, "second half"]
        assert result.segments[2].start == pytest.approx(598.5 + 30.0,
                                                         abs=0.01)
        assert result.duration == pytest.approx(900.0, abs=0.01)
        assert result.text == "first half spoken across the seam second half"

    def test_chunks_are_sequential_files(self, tmp_path):
        wav = make_wav(tmp_path / "long.wav", 601.0)
        backend = SeqBackend([{"text": "a", "segments": []},
                              {"text": "b", "segments": []}])
        chunking.transcribe_long(backend, wav)
        names = [p.name for p in backend.calls]
        assert names == ["chunk-000.wav", "chunk-001.wav"]

    def test_oversize_rejected_before_any_work(self, tmp_path):
        wav = make_wav(tmp_path / "huge.wav", 10.0)
        backend = SeqBackend([])
        with pytest.raises(chunking.AudioTooLargeError) as ei:
            chunking.transcribe_long(backend, wav, limit_s=5.0)
        assert "too long" in str(ei.value)  # structured, names the bound
        assert backend.calls == []       # no decode ever ran
        assert not list(tmp_path.glob("fluidvoice-chunks-*"))  # no temp

    def test_tempdirs_cleaned_on_success_and_error(self, tmp_path):
        import tempfile
        before = set(Path(tempfile.gettempdir()).glob("fluidvoice-chunks-*"))
        wav = make_wav(tmp_path / "long.wav", 900.0)
        backend = SeqBackend([{"text": "a", "segments": []},
                              {"text": "b", "segments": []}])
        chunking.transcribe_long(backend, wav)
        failing = SeqBackend([])
        failing.transcribe = lambda w, language=None: (_ for _ in ()).throw(
            RuntimeError("decode blew up"))
        with pytest.raises(RuntimeError):
            chunking.transcribe_long(failing, wav)
        after = set(Path(tempfile.gettempdir()).glob("fluidvoice-chunks-*"))
        assert after == before  # nothing leaked on either exit


class TestConvertOnce:
    def test_one_conversion_max_for_long_input(self, tmp_path, monkeypatch):
        wav = make_wav(tmp_path / "long.wav", 900.0)
        conversions = {"n": 0}
        real = chunking.ensure_wav

        def counting(path, dest_dir=None, force=False):
            conversions["n"] += 1
            return real(path, dest_dir=dest_dir, force=force)

        monkeypatch.setattr(chunking, "ensure_wav", counting)
        backend = SeqBackend([{"text": "a", "segments": []},
                              {"text": "b", "segments": []}])
        chunking.transcribe_long(backend, wav)
        # a .wav passes through untouched: 1 ensure call, 0 ffmpeg runs,
        # and every chunk shares the same (unconverted) source
        assert conversions["n"] == 1

    def test_non_riff_passthrough_forced_once(self, tmp_path, monkeypatch):
        flac = tmp_path / "rec.flac"
        flac.write_bytes(b"not a wav")  # wave.open must fail on it
        converted = make_wav(tmp_path / "rec.16k.wav", 900.0)
        calls = {"force": 0}

        def fake_ensure(path, dest_dir=None, force=False):
            if force or Path(path).suffix != ".wav":
                calls["force"] += 1
                return converted
            return Path(path)

        monkeypatch.setattr(chunking, "ensure_wav", fake_ensure)
        backend = SeqBackend([{"text": "a", "segments": []},
                              {"text": "b", "segments": []}])
        chunking.transcribe_long(backend, flac)
        assert calls["force"] == 1  # passthrough rejected, forced exactly once


class TestProbe:
    def test_wave_duration_wav(self, tmp_path):
        assert chunking._probe_duration(
            make_wav(tmp_path / "x.wav", 12.0)) == pytest.approx(12.0)

    def test_probe_returns_none_for_garbage(self, tmp_path):
        p = tmp_path / "junk.bin"
        p.write_bytes(b"\x00" * 64)
        assert chunking._probe_duration(p) is None


# -- edge-shape guards -------------------------------------------------------

DAEMON_KEYS = {"ok", "path", "text", "language", "duration_s"}


def test_daemon_response_shape_golden(tmp_path, monkeypatch):
    """The control-edge response keeps the exact pre-P3 key set on the
    chunked path (golden compat)."""
    from fluidvoice import daemon as dm
    from tests.test_socket_api import StubRecorder
    from tests.test_socket_api import make_wav as api_wav

    cfg = copy.deepcopy(DEFAULTS)
    rec_path = api_wav(tmp_path / "utt.wav")

    results = [{"text": "part one seam", "language": "en",
                "duration": 600.0,
                "segments": [{"start": 1.0, "end": 2.0, "text": "part one"},
                             {"start": 598.9, "end": 600.0, "text": "seam"}]},
               {"text": "seam part two", "language": "en",
                "duration": 301.5,
                "segments": [{"start": 0.2, "end": 1.5, "text": "seam"},
                             {"start": 5.0, "end": 6.0, "text": "part two"}]}]

    class ChunkyStub:
        name = "stub"
        surfaces_detected_language = False

        def __init__(self):
            self.calls = []

        def transcribe(self, wav, language=None):
            self.calls.append(Path(wav))
            return copy.deepcopy(results[len(self.calls) - 1])

    stub = ChunkyStub()
    d = dm.Daemon(cfg, recorder=StubRecorder(rec_path),
                  backend_factory=lambda c: stub,
                  use_hotkey=False, use_sounds=False)
    d.backend = stub
    monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
    monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
    monkeypatch.setattr(dm.history_mod.paths, "history_file",
                        lambda: tmp_path / "history.jsonl")

    long_wav = make_wav(tmp_path / "meeting.wav", 900.0)
    r = d.handle_request({"action": "transcribe", "path": str(long_wav)})
    assert set(r) == DAEMON_KEYS
    assert r["ok"] is True and r["path"] == str(long_wav)
    assert r["text"] == "part one seam part two"  # one "seam", ordered
    assert r["language"] == "en"
    assert r["duration_s"] == pytest.approx(900.0, abs=0.01)
    assert len(stub.calls) == 2
    # the busy gate is released when the chunked job finishes
    assert d.busy is False
