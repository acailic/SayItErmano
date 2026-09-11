"""Segmented streaming preview (requests/streaming-finalization.md phase 1).

Covers: fixed-window tiling + finalize semantics, the constant per-tick
decode bound (N windows -> N decode calls, each one window of audio - not
O(take) re-decodes), the trailing-silence VAD trigger table, and the
backend-generalized transcriber factory.
"""
from __future__ import annotations

import math
import struct
import threading
import time
from pathlib import Path

from fluidvoice.preview import (
    SegmentedPreviewEngine,
    join_tail,
    preview_transcriber,
    trailing_silence_s,
)

RATE = 16000
BPS = RATE * 2  # s16 mono bytes per second


def pcm(seconds: float, freq: int = 440, amp: int = 8000) -> bytes:
    return b"".join(
        struct.pack("<h", int(amp * math.sin(2 * math.pi * freq * i / RATE)))
        for i in range(int(seconds * RATE)))


def silence(seconds: float) -> bytes:
    return b"\x00" * int(seconds * RATE) * 2


def fricative(seconds: float) -> bytes:
    """Low-energy high-ZCR wave: unvoiced consonant ("sss") - not silence."""
    return pcm(seconds, freq=6000, amp=120)


class RecordingEngine(SegmentedPreviewEngine):
    """Test seam: record every decode span (start_s, end_s)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.positions: list[tuple[float, float]] = []

    def _decode(self, start_s, end_s, ctx):
        self.positions.append((round(start_s, 3), round(end_s, 3)))
        return super()._decode(start_s, end_s, ctx)


def drive(engine: SegmentedPreviewEngine, raw: Path, total_s: float,
          step_s: float) -> None:
    """Deterministically replay the recording loop: grow the file, tick."""
    data = b""
    t = step_s
    while t <= total_s + 1e-9:
        data += pcm(step_s) if t > 0 else b""
        raw.write_bytes(data)
        engine._tick(data, len(data) / BPS)
        t += step_s


class TestSegmentation:
    def test_commit_windows_tile_stream_exactly(self, tmp_path):
        raw = tmp_path / "take.raw"
        raw.write_bytes(pcm(0.2))
        eng = RecordingEngine(raw, lambda w, c: "word", lambda t: None,
                              interval=1.2, min_audio=0.5, segment_s=2.0)
        drive(eng, raw, total_s=10.0, step_s=0.5)
        commits = [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0)]
        for span in commits:
            assert span in eng.positions, f"missing commit decode {span}"
        # every commit span decoded exactly once
        for span in commits:
            assert eng.positions.count(span) == 1
        assert len(eng.committed) == 5
        assert eng.stats["commits"] == 5
        assert eng.stats["covered_s"] == 10.0

    def test_constant_decode_cost_not_quadratic(self, tmp_path):
        raw = tmp_path / "long.raw"
        raw.write_bytes(pcm(0.2))
        calls = []

        def counter(wav, ctx):
            calls.append(len(wav))
            return "w"

        eng = SegmentedPreviewEngine(raw, counter, lambda t: None,
                                     interval=1.0, min_audio=0.5, segment_s=2.0)
        ticks = 40
        drive(eng, raw, total_s=float(ticks), step_s=1.0)
        # one window (<= 2 s of audio) per tick, never the whole take again
        assert len(calls) <= ticks + 1
        assert sum(calls) <= (ticks + 1) * BPS * 2
        # a legacy whole-buffer engine would have decoded ticks*(ticks+1)/2
        # audio-seconds by now; assert we are far below that
        assert sum(calls) / BPS < ticks * ticks / 4

    def test_committed_text_monotone_and_stable(self, tmp_path):
        raw = tmp_path / "m.raw"
        raw.write_bytes(pcm(0.2))
        snapshots = []  # (committed tuple as of tick, displayed text)

        def wordy(wav, ctx):
            # window-length-derived text: tail slices differ from commits,
            # like a real decoder re-rendering the live region
            return f"w{int(len(wav) / (BPS * 2))}"

        class Snapshots(SegmentedPreviewEngine):
            def _tick(self, raw, audio_s):
                super()._tick(raw, audio_s)
                snapshots.append((tuple(self.committed), self.last_text))

        eng = Snapshots(raw, wordy, lambda t: None,
                        interval=1.0, min_audio=0.5, segment_s=2.0)
        drive(eng, raw, total_s=8.0, step_s=0.5)
        assert len(eng.committed) == 4  # windows 0, 2, 4, 6 committed once
        # finalized text is stable: once a segment is committed, the display
        # always starts with the full committed prefix from then on
        seen_committed = ()
        for committed, text in snapshots:
            if committed != seen_committed:
                seen_committed = committed
            else:
                prefix = " ".join(t for t in committed if t)
                assert not prefix or text.startswith(prefix)
        assert eng.last_text.startswith(
            " ".join(t for t in eng.committed if t))

    def test_tail_decode_is_newest_slice(self, tmp_path):
        raw = tmp_path / "t.raw"
        raw.write_bytes(pcm(0.2))
        eng = RecordingEngine(raw, lambda w, c: "x", lambda t: None,
                              interval=1.2, min_audio=0.5, segment_s=2.0)
        # single tick at t=1.4: no commit window yet (needs t>=2), tail only
        data = pcm(1.4)
        raw.write_bytes(data)
        eng._tick(data, 1.4)
        assert eng.positions == [(0.0, 1.4)]
        # t=3.0: commit window 0 due first; tail refresh comes on a later tick
        data += pcm(1.6)
        raw.write_bytes(data)
        eng._tick(data, 3.0)
        assert eng.positions[-1] == (0.0, 2.0)
        eng._tick(data, 3.0)  # same stream, no new commit -> tail
        assert eng.positions[-1] == (1.0, 3.0)

    def test_errors_are_swallowed(self, tmp_path):
        raw = tmp_path / "e.raw"
        raw.write_bytes(pcm(0.3))

        def broken(wav, ctx):
            raise RuntimeError("cuda busy")

        eng = SegmentedPreviewEngine(raw, broken, lambda t: None,
                                     interval=0.1, min_audio=0.1)
        eng._tick(pcm(3.0), 3.0)  # must not raise
        eng.stop()


class TestTailDedupe:
    def test_drops_reemitted_overlap(self):
        assert join_tail("hello world", "world again") == "again"

    def test_no_false_drop(self):
        assert join_tail("hello world", "again please") == "again please"

    def test_empty_sides(self):
        assert join_tail("", "tail") == "tail"
        assert join_tail("committed", "") == ""


class TestVad:
    def make_engine(self, raw: Path, texts, fired) -> SegmentedPreviewEngine:
        def fake(wav, ctx):
            seconds = len(wav) / BPS
            return texts(seconds)

        return SegmentedPreviewEngine(
            raw, fake, lambda t: None, interval=0.4, min_audio=0.5,
            segment_s=2.0, vad_silence_s=2.0,
            on_silence=lambda: fired.append(time.monotonic()))

    def drive_take(self, eng, raw, speech_s, silence_s, step=0.4):
        data = pcm(speech_s)
        raw.write_bytes(data)
        t = speech_s
        while t <= speech_s + silence_s + 1e-9:
            eng._tick(data, len(data) / BPS)
            t += step
            if t <= speech_s + silence_s:
                data += silence(step)
                raw.write_bytes(data)
        return data

    def test_fires_after_trailing_silence_with_speech(self, tmp_path):
        raw = tmp_path / "v.raw"
        raw.write_bytes(pcm(0.2))
        fired = []
        eng = self.make_engine(raw, lambda s: "hello world" if s > 1.5 else "",
                               fired)
        self.drive_take(eng, raw, speech_s=3.0, silence_s=2.6)
        assert len(fired) == 1
        assert any(eng.committed)  # only because real speech was committed

    def test_no_fire_on_all_silence_take(self, tmp_path):
        raw = tmp_path / "s.raw"
        raw.write_bytes(silence(0.2))
        fired = []
        eng = self.make_engine(raw, lambda s: "", fired)
        data = silence(8.0)
        raw.write_bytes(data)
        for t in (i * 0.5 + 0.5 for i in range(16)):
            eng._tick(data, t)
        assert fired == []
        assert eng.committed == ["", "", "", ""]  # decodes happened, all empty

    def test_short_silence_does_not_fire(self, tmp_path):
        raw = tmp_path / "sh.raw"
        raw.write_bytes(pcm(0.2))
        fired = []
        eng = self.make_engine(raw, lambda s: "hi" if s > 1.5 else "", fired)
        self.drive_take(eng, raw, speech_s=3.0, silence_s=1.2)
        assert fired == []

    def test_fires_at_most_once(self, tmp_path):
        raw = tmp_path / "once.raw"
        raw.write_bytes(pcm(0.2))
        fired = []
        eng = self.make_engine(raw, lambda s: "hi" if s > 1.5 else "", fired)
        data = self.drive_take(eng, raw, speech_s=3.0, silence_s=3.0)
        for _ in range(5):  # keep sitting in silence
            eng._tick(data, len(data) / BPS)
        assert len(fired) == 1

    def test_trailing_silence_unit(self):
        assert abs(trailing_silence_s(silence(1.0) + pcm(1.0)) - 0.0) < 0.02
        assert abs(trailing_silence_s(pcm(1.0) + silence(1.0)) - 1.0) < 0.02
        assert abs(trailing_silence_s(pcm(1.0) + silence(0.5)) - 0.5) < 0.02
        assert trailing_silence_s(b"") == 0.0
        # low-energy fricative tail is speech, not silence
        assert trailing_silence_s(pcm(1.0) + fricative(0.6)) < 0.02


class TestSendCountdown:
    """B7 spoken-send quiet countdown: phrase + >=0.5 s quiet arms the
    daemon callback (once); resumed speech cancels and re-arms; emission is
    suppressed while armed; the plain VAD path is untouched."""

    def make_engine(self, raw, texts, armed, resumed, *, phrase="send it",
                    shown=None, vad_fired=None):
        def fake(wav, ctx):
            return texts(len(wav) / BPS)

        kwargs = dict(interval=0.4, min_audio=0.5, segment_s=2.0,
                      vad_silence_s=2.0,
                      on_silence=(lambda: vad_fired.append(1))
                      if vad_fired is not None else None,
                      send_phrase=phrase, send_countdown_s=1.2,
                      on_send_countdown=lambda: armed.append(1),
                      on_send_resume=lambda: resumed.append(1))
        return SegmentedPreviewEngine(raw, fake, shown or (lambda t: None),
                                      **kwargs)

    def drive_quiet(self, eng, raw, speech_s=3.0, quiet_s=1.4, step=0.4):
        data = pcm(speech_s)
        raw.write_bytes(data)
        t = speech_s
        while t <= speech_s + quiet_s + 1e-9:
            eng._tick(data, len(data) / BPS)
            t += step
            if t <= speech_s + quiet_s:
                data += silence(step)
                raw.write_bytes(data)
        return data

    def test_arms_on_phrase_plus_quiet(self, tmp_path):
        raw = tmp_path / "c1.raw"
        raw.write_bytes(pcm(0.2))
        armed, resumed = [], []
        eng = self.make_engine(
            raw, lambda s: "please send it" if s > 1.5 else "", armed, resumed)
        self.drive_quiet(eng, raw)
        assert len(armed) == 1 and resumed == []
        assert eng._send_armed

    def test_no_arm_without_the_phrase(self, tmp_path):
        raw = tmp_path / "c2.raw"
        raw.write_bytes(pcm(0.2))
        armed = []
        eng = self.make_engine(
            raw, lambda s: "keep talking" if s > 1.5 else "", armed, [])
        self.drive_quiet(eng, raw)
        assert armed == []

    def test_no_arm_on_all_silence_take(self, tmp_path):
        raw = tmp_path / "c3.raw"
        raw.write_bytes(silence(0.2))
        armed = []
        eng = self.make_engine(raw, lambda s: "", armed, [])
        data = silence(6.0)
        raw.write_bytes(data)
        for t in (i * 0.5 + 0.5 for i in range(12)):
            eng._tick(data, t)
        assert armed == []

    def test_literal_escape_does_not_arm(self, tmp_path):
        raw = tmp_path / "c4.raw"
        raw.write_bytes(pcm(0.2))
        armed = []
        eng = self.make_engine(
            raw, lambda s: "write literal send it" if s > 1.5 else "",
            armed, [])
        self.drive_quiet(eng, raw)
        assert armed == []

    def test_speech_resume_cancels_and_rearms(self, tmp_path):
        raw = tmp_path / "c5.raw"
        raw.write_bytes(pcm(0.2))
        armed, resumed = [], []
        eng = self.make_engine(
            raw, lambda s: "one send it" if s > 1.5 else "", armed, resumed)
        data = self.drive_quiet(eng, raw)          # arm
        assert len(armed) == 1
        data += pcm(0.8)                           # speak again
        raw.write_bytes(data)
        eng._tick(data, len(data) / BPS)
        assert len(resumed) == 1 and not eng._send_armed
        data += silence(1.0)                       # quiet again -> re-arm
        raw.write_bytes(data)
        eng._tick(data, len(data) / BPS)
        eng._tick(data, len(data) / BPS)
        assert len(armed) == 2

    def test_emission_suppressed_while_armed(self, tmp_path):
        raw = tmp_path / "c6.raw"
        raw.write_bytes(pcm(0.2))
        shown: list[str] = []
        armed: list[int] = []
        eng = self.make_engine(
            raw, lambda s: "first words send it" if s > 1.5 else "",
            armed, [], shown=shown.append)
        self.drive_quiet(eng, raw)                 # arm; later emissions skip
        count_at_arm = len(shown)
        assert armed and count_at_arm >= 1         # text flowed before arming
        # more ticks while armed -> no further pill updates
        data = raw.read_bytes()
        eng._tick(data, len(data) / BPS)
        eng._tick(data, len(data) / BPS)
        assert len(shown) == count_at_arm

    def test_vad_still_fires_when_phrase_absent(self, tmp_path):
        raw = tmp_path / "c7.raw"
        raw.write_bytes(pcm(0.2))
        armed, vad_fired = [], []
        eng = self.make_engine(
            raw, lambda s: "no phrase here" if s > 1.5 else "", armed, [],
            vad_fired=vad_fired)
        self.drive_quiet(eng, raw, quiet_s=3.0)    # past the VAD threshold
        assert armed == [] and len(vad_fired) == 1

    def test_armed_countdown_suppresses_vad(self, tmp_path):
        # phrase present: the countdown is the finisher, the plain VAD
        # must not fire mid-countdown even past its own threshold
        raw = tmp_path / "c8.raw"
        raw.write_bytes(pcm(0.2))
        armed, vad_fired = [], []
        eng = self.make_engine(
            raw, lambda s: "words send it" if s > 1.5 else "", armed, [],
            vad_fired=vad_fired)
        self.drive_quiet(eng, raw, quiet_s=3.2)    # well past VAD 2.0 s
        assert len(armed) == 1
        assert vad_fired == []                     # suppressed while armed
        assert eng._send_armed                     # daemon timer takes over

    def test_arms_when_tail_decode_drops_the_phrase(self, tmp_path):
        # live-smoke regression: once speech stops, the tail window slides
        # into silence and its decode DROPS the final words - the arm must
        # fire from the phrase that WAS the end of the text, before the
        # degraded re-decode can overwrite it
        raw = tmp_path / "c9.raw"
        raw.write_bytes(pcm(0.2))
        armed = []

        def fake(wav, ctx):
            # decodes made after the recording went quiet (file past the
            # speech) degrade and lose the final words
            return ("finish with send it"
                    if raw.stat().st_size < int(7.4 * BPS)
                    else "finish with")

        eng = SegmentedPreviewEngine(
            raw, fake, lambda t: None, interval=0.4, min_audio=0.5,
            segment_s=2.0, vad_silence_s=2.0,
            send_phrase="send it", send_countdown_s=1.2,
            on_send_countdown=lambda: armed.append(1),
            on_send_resume=lambda: None)
        self.drive_quiet(eng, raw, speech_s=3.0, quiet_s=1.6)
        assert len(armed) == 1
        assert "finish with" == eng.last_text \
            or eng.last_text.endswith("send it")

    def test_stale_phrase_does_not_arm(self, tmp_path):
        # speech AFTER the phrase invalidates it: the stamp clears on the
        # next active-speech tick, so going quiet later does not finish
        raw = tmp_path / "c10.raw"
        raw.write_bytes(pcm(0.2))
        armed = []
        eng = self.make_engine(
            raw, lambda s: "later words", armed, [])
        # simulate: the phrase was the end of the text at 4.0 s...
        eng.committed = ["please", "send it"]
        eng.last_text = "please send it"
        eng._phrase_seen_s = 4.0
        # ...the user kept talking (fresh speech in the last 1.1 s)...
        data = pcm(8.0)
        raw.write_bytes(data)
        eng._tick(data, 8.0)
        assert eng._phrase_seen_s is None and armed == []
        # ...and going quiet afterwards must not arm
        data += silence(1.2)
        raw.write_bytes(data)
        eng._tick(data, 9.2)
        eng._tick(data, 9.2)
        assert armed == []


class TestThreadedEngine:
    def test_end_to_end_growing_file(self, tmp_path):
        raw = tmp_path / "live.raw"
        raw.write_bytes(pcm(0.1))
        shown = []
        eng = SegmentedPreviewEngine(raw, lambda w, c: "word", shown.append,
                                     interval=0.08, min_audio=0.3,
                                     segment_s=2.0)
        eng.start()
        deadline = time.monotonic() + 6
        data = b""
        while len(shown) < 2 and time.monotonic() < deadline:
            data += pcm(0.3)
            raw.write_bytes(data)
            time.sleep(0.05)
        eng.stop(timeout=2)
        assert len(shown) >= 2
        assert eng.stats["decodes"] >= 2


class TestPreviewTranscriberFactory:
    def test_faster_whisper_gets_initial_prompt(self):
        kwargs = {}

        class Seg:
            text = "hi there"

        class Model:
            def transcribe(self, *a, **k):
                kwargs.update(k)
                return [Seg()], None

        backend = type("B", (), {"name": "faster-whisper", "_model": Model()})()
        made = preview_transcriber({}, backend, "en")
        assert made is not None
        fn, bname = made
        assert bname == "faster-whisper"
        from fluidvoice.audio_utils import raw_to_wav_bytes
        text, conf = fn(raw_to_wav_bytes(pcm(0.2)), "context words")
        assert text == "hi there"
        assert conf is None  # the Seg fake carries no avg_logprob
        assert kwargs["initial_prompt"] == "context words"
        assert kwargs["condition_on_previous_text"] is False

    def test_unready_backends_return_none(self):
        fw_loading = type("B", (), {"name": "faster-whisper", "_model": None})()
        assert preview_transcriber({}, fw_loading, "en") is None
        parakeet_cold = type("B", (), {"name": "parakeet", "_decoder": None})()
        assert preview_transcriber({}, parakeet_cold, "en") is None
        assert preview_transcriber({}, None, "en") is None

    def test_parakeet_ready_callable(self):
        import numpy as np

        class Feat:
            def __call__(self, samples):
                return np.zeros((10, 80), np.float32)

        class Dec:
            def run(self, feats):
                return [1, 2]

        backend = type("B", (), {
            "name": "parakeet", "_decoder": Dec(), "_featurizer": Feat(),
            "_normalize_type": "", "_id2tok": {1: "zdravo", 2: "svet"}})()
        fn, _ = preview_transcriber({}, backend, None)
        assert callable(fn)
        from fluidvoice.audio_utils import raw_to_wav_bytes
        # ctx is ignored (TDT has no prompt concept) but must not blow up;
        # detokenize joins TDT tokens without spaces (spacing rides the tokens)
        assert fn(raw_to_wav_bytes(pcm(0.2)), "ignored ctx") == "zdravosvet"

    def test_whisper_cpp_needs_binary_and_model(self):
        ready = type("B", (), {"name": "whisper.cpp", "binary": "/bin/true",
                               "model": "/tmp/m.bin"})()
        fn, bname = preview_transcriber({}, ready, "en")
        assert bname == "whisper.cpp" and callable(fn)
        not_ready = type("B", (), {"name": "whisper.cpp", "binary": None,
                                   "model": None})()
        assert preview_transcriber({}, not_ready, "en") is None


class TestFirstWordCapture:
    """Rider (reviews sweep): upstream v1.6.6 dropped opening words (#751);
    v1.6.7 pinned trigger-to-audio under 100 ms. Pin Recorder.start()'s
    probe contract: return as soon as PCM flows (never a fixed sleep), and
    never trim the head of the stream."""

    def _patch_recorder(self, monkeypatch, write_pcm: bool):
        from fluidvoice import recorder as rec

        class FastProc:
            def __init__(self, args):
                if write_pcm:
                    Path(args[-1]).write_bytes(pcm(1.0))
                self.stderr = None

            def poll(self):
                return None

            def send_signal(self, s):
                pass

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(rec.shutil, "which", lambda n: "/usr/bin/pw-record")
        monkeypatch.setattr(rec.subprocess, "Popen", lambda a, **k: FastProc(a))
        monkeypatch.setattr(
            rec.threading, "Thread",
            lambda target=None, args=(), daemon=None, name=None:
            type("T", (), {"start": lambda s: None})())

    def test_start_returns_promptly_when_pcm_flows(self, tmp_path, monkeypatch):
        self._patch_recorder(monkeypatch, write_pcm=True)
        from fluidvoice import recorder as rec
        r = rec.Recorder()
        t0 = time.monotonic()
        r.start(tmp_path / "utt.wav")
        elapsed = time.monotonic() - t0
        # probe breaks at the first 2048-byte partial write, far below the
        # 0.35 s probe ceiling - a refactor back to a fixed sleep fails this
        assert elapsed < 0.25
        # the whole stream from process start is preserved (no head trim)
        assert r.raw_path.stat().st_size >= 32000
        r.stop()

    def test_start_bounded_wait_when_no_pcm(self, tmp_path, monkeypatch):
        self._patch_recorder(monkeypatch, write_pcm=False)
        from fluidvoice import recorder as rec
        r = rec.Recorder()
        t0 = time.monotonic()
        r.start(tmp_path / "silent.wav")  # live-but-silent: no crash, bounded
        elapsed = time.monotonic() - t0
        assert rec.PROBE_SECONDS - 0.05 <= elapsed <= rec.PROBE_SECONDS + 0.3
        r.stop()


class TestDoctorPreviewLines:
    def test_defaults_show_segmented_engine(self):
        import copy

        from fluidvoice import doctor
        from fluidvoice.config import DEFAULTS
        lines = doctor._preview_lines(copy.deepcopy(DEFAULTS))
        assert any("segmented" in l and "backend 'auto'" in l for l in lines)
        assert any("2 s window, 50% hop" in l for l in lines)
        assert any("vad auto-stop: 2 s trailing silence" in l for l in lines)

    def test_disabled_and_off_variants(self):
        import copy

        from fluidvoice import doctor
        from fluidvoice.config import DEFAULTS
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["preview_enabled"] = False
        assert doctor._preview_lines(cfg) == [
            "  disabled (recording.preview_enabled = false)"]
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["preview_segmented"] = False
        assert any("legacy whole-buffer" in l
                   for l in doctor._preview_lines(cfg))
        cfg["recording"]["preview_vad_silence_s"] = 0.0
        assert any("vad auto-stop: off" in l for l in doctor._preview_lines(cfg))


class TestDaemonWiring:
    """_start_preview picks the segmented engine; _vad_auto_stop reuses the
    max-duration stop path under the lock."""

    @staticmethod
    def make_backend(text="final text"):
        class Model:
            def transcribe(self, *a, **k):
                return [type("S", (), {"text": text})()], None

        def transcribe(self, wav_path, language=None):
            return {"text": text, "language": "en", "duration": 1.0}

        return type("B", (), {"name": "faster-whisper", "_model": Model(),
                              "transcribe": transcribe})()

    def make_daemon(self, tmp_path, monkeypatch, recorder):
        import copy

        from fluidvoice import daemon as dm
        from fluidvoice.config import DEFAULTS
        cfg = copy.deepcopy(DEFAULTS)
        cfg["recording"]["preview_mode"] = "notify"
        cfg["recording"]["preview_interval"] = 0.05
        cfg["recording"]["preview_min_audio"] = 0.3
        cfg["recording"]["preview_vad_silence_s"] = 0.0
        monkeypatch.setattr(dm.ui, "notify", lambda *a, **k: None)
        monkeypatch.setattr(dm.ui, "play_sound", lambda *a, **k: None)
        monkeypatch.setattr(dm.history_mod.paths, "history_file",
                            lambda: tmp_path / "h.jsonl")
        monkeypatch.setattr(dm.insertion, "insert_text",
                            lambda text, cfg, on_notice=None: "typed")
        monkeypatch.setattr(dm.insertion, "active_window_class",
                            lambda: "TestApp")
        backend = self.make_backend()
        d = dm.Daemon(cfg, recorder=recorder,
                      backend_factory=lambda c: backend,
                      use_hotkey=False, use_sounds=False)
        d.backend = backend
        return d

    def test_start_preview_uses_segmented_engine(self, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(
            "fluidvoice.preview.NotifyPreview.show",
            lambda self, text: shown.append(text))
        monkeypatch.setattr("fluidvoice.preview.NotifyPreview.close",
                            lambda self: None)

        class Rec:
            pass

        d = self.make_daemon(tmp_path, monkeypatch, Rec())
        raw = tmp_path / "utt.raw"
        raw.write_bytes(pcm(1.0))
        d._capture.start_preview(raw)
        eng, disp = d._capture.preview
        assert isinstance(eng, SegmentedPreviewEngine)
        assert eng.vad_silence_s == 0.0  # threaded off in this test
        deadline = time.monotonic() + 3
        while not shown and time.monotonic() < deadline:
            raw.write_bytes(pcm(1.0))  # keep "recording"
            time.sleep(0.02)
        eng.stop(timeout=2)
        assert shown  # partial text reached the display

    def test_vad_auto_stop_stops_the_take(self, tmp_path, monkeypatch):
        import wave as wave_mod


        class Rec:
            def __init__(self):
                self.stopped = 0
                self.path = None
                self.raw_path = None

            def stop(self):
                self.stopped += 1
                p = tmp_path / "utt.wav"
                with wave_mod.open(str(p), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(pcm(1.0))
                self.path = p
                return p

            def cancel(self):
                self.stopped += 1

        rec = Rec()
        d = self.make_daemon(tmp_path, monkeypatch, rec)
        d.recording = True
        d._capture.watchdog = threading.Timer(999.0, d._capture.auto_stop)
        d._capture.vad_auto_stop()
        assert rec.stopped == 1
        assert d.recording is False
        d._capture.watchdog and d._capture.watchdog.cancel()

    def test_vad_stop_finishes_via_full_take_decode(self, tmp_path,
                                                    monkeypatch):
        """Brief item 4 pin: the VAD stop finishes the take through the
        legacy full-take decode - one backend.transcribe over the COMPLETE
        wav, never a window mosaic. A future refactor that swaps stop-time
        transcription to concatenated preview windows fails here."""
        import wave as wave_mod

        from fluidvoice import daemon as dm

        take_s = 3.0  # > the 2 s preview window: a mosaic would truncate

        class RecordingBackend:
            name = "faster-whisper"

            def __init__(self):
                self.calls = []  # (wav path, seconds decoded at call time)

            def transcribe(self, wav_path, language=None):
                with wave_mod.open(str(wav_path), "rb") as wf:
                    self.calls.append(
                        (Path(wav_path), wf.getnframes() / wf.getframerate()))
                return {"text": "final text", "language": "en",
                        "duration": take_s}

        class Rec:
            def __init__(self):
                self.stopped = 0
                self.raw_path = None

            def stop(self):
                self.stopped += 1
                p = tmp_path / "utt.wav"
                with wave_mod.open(str(p), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(pcm(take_s))  # the whole take
                return p

            def cancel(self):
                self.stopped += 1

        captured = []
        backend = RecordingBackend()
        rec = Rec()
        d = self.make_daemon(tmp_path, monkeypatch, rec)
        # Capture at the pipeline's injectable inserter seam: the
        # DictationPipeline default arg binds insertion.insert_text at
        # import time, so patching the module attribute never reaches it
        # (the sibling's module-attr stub covers only direct daemon calls).
        def pipeline_factory(cfg, be):
            return dm.DictationPipeline(
                cfg, be, inserter=lambda text, c: captured.append(text)
                or "typed")
        d._pipeline_factory = pipeline_factory
        d.backend = backend
        d.recording = True
        d._capture.watchdog = threading.Timer(999.0, d._capture.auto_stop)

        d._capture.vad_auto_stop()

        assert rec.stopped == 1
        assert d.recording is False
        assert d._capture.watchdog is None  # _stop_recording_locked cancelled it
        assert d._process_thread is not None
        d._process_thread.join(timeout=5)
        assert not d._process_thread.is_alive()
        # the typed text is the backend's single full-decode result, not a
        # preview mosaic
        assert captured == ["final text"]
        assert len(backend.calls) == 1
        decoded_s = backend.calls[0][1]
        assert decoded_s >= take_s - 0.05  # whole take, no 2 s truncation


class TestProvisionalTail:
    """CHI'23 stability finding: committed text renders full ink, the
    volatile live tail is marked provisional (stable_chars offset) and
    every tail rewrite is counted."""

    def make_engine(self, raw, out):
        def fake(wav, ctx):
            return "tail words"

        def on_text(shown, stable=0, _o=out):
            _o.append((shown, stable))

        return SegmentedPreviewEngine(
            raw, fake, on_text, interval=0.4, min_audio=0.5,
            segment_s=2.0, vad_silence_s=0.0)

    def test_emit_passes_stable_offset(self, tmp_path):
        raw = tmp_path / "pt.raw"
        raw.write_bytes(pcm(0.2))
        shown = []
        eng = self.make_engine(raw, shown)
        eng.committed = ["stable text here"]
        eng.last_text = ""
        eng._emit("stable text here", "fresh tail")
        assert shown[-1][0].endswith("fresh tail")
        assert shown[-1][1] == len("stable text here")  # stable offset

    def test_tail_rewrites_counted(self, tmp_path):
        raw = tmp_path / "pt2.raw"
        raw.write_bytes(pcm(0.2))
        shown = []
        eng = self.make_engine(raw, shown)
        eng.committed = ["fixed"]
        eng.last_text = ""
        eng._emit("fixed", "aaa")
        eng._emit("fixed aaa", "bbb")     # tail rewritten
        eng._emit("fixed aaa", "bbb")     # same tail: no count
        eng._emit("fixed aaa bbb", "")    # commit: no tail, no count
        assert eng.stats.get("tail_rewrites") == 1


class TestConfidenceGate:
    """Low-confidence window text is suppressed from the pill (2026-09-11
    learning session: BT-mic garbage windows render as wrong words; the
    bars stay as the activity signal). Transcribers may return
    (text, mean_avg_logprob); the engine gates below the final-decode
    LOW threshold."""

    def make_engine(self, tmp_path, transcriber, **kw):
        shown = []
        raw = tmp_path / "cg.raw"
        raw.write_bytes(pcm(0.2))
        kw.setdefault("interval", 1.0)
        kw.setdefault("min_audio", 0.5)
        kw.setdefault("segment_s", 2.0)
        eng = SegmentedPreviewEngine(raw, transcriber,
                                     lambda t, s=0: shown.append(t), **kw)
        return eng, shown

    def drive(self, eng, raw, total_s, step_s):
        data = b""
        t = step_s
        while t <= total_s + 1e-9:
            data += pcm(step_s)
            raw.write_bytes(data)
            eng._tick(data, len(data) / BPS)
            t += step_s

    def test_low_conf_window_suppressed(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("garbled words", -1.2))
        self.drive(eng, tmp_path / "cg.raw", total_s=6.0, step_s=1.0)
        assert not any(eng.committed)
        assert eng.stats["lowconf"] >= 3   # 3 commits + gated tails
        assert not [t for t in shown if "garbled" in t]

    def test_confident_window_committed(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("clear words", -0.2))
        self.drive(eng, tmp_path / "cg.raw", total_s=6.0, step_s=1.0)
        assert "clear words" in " ".join(eng.committed)
        assert eng.stats["lowconf"] == 0

    def test_unknown_confidence_never_gated(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("maybe words", None))
        self.drive(eng, tmp_path / "cg.raw", total_s=6.0, step_s=1.0)
        assert "maybe words" in " ".join(eng.committed)
        assert eng.stats["lowconf"] == 0

    def test_plain_str_contract_still_works(self, tmp_path):
        eng, shown = self.make_engine(tmp_path, lambda w, c: "legacy")
        self.drive(eng, tmp_path / "cg.raw", total_s=6.0, step_s=1.0)
        assert "legacy" in " ".join(eng.committed)

    def test_gate_disabled_commits_anyway(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("garbled words", -1.2), conf_gate=False)
        self.drive(eng, tmp_path / "cg.raw", total_s=6.0, step_s=1.0)
        assert "garbled words" in " ".join(eng.committed)

    def test_low_conf_tail_suppressed(self, tmp_path):
        # first commit confident, every tail decode shaky: the pill shows
        # the committed words and never the shaky provisional tail
        replies = iter([("solid words", -0.2), ("junk tail", -1.3),
                        ("junk tail", -1.3), ("junk tail", -1.3),
                        ("junk tail", -1.3)])
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: next(replies, ("junk tail", -1.3)))
        self.drive(eng, tmp_path / "cg.raw", total_s=5.0, step_s=1.0)
        assert "solid words" in " ".join(shown)
        assert not [t for t in shown if "junk" in t]


class TestSustainedSilence:
    """Per-take self-silencing: when the first committed windows average
    below -0.55 (sustained - one shaky window never silences), the pill
    stops showing words for the rest of the take (bars + ellipsis carry
    it) - measured on the 2026-09-11 BT takes: garble averages ~ -0.78,
    clean speech ~ -0.35."""

    def make_engine(self, tmp_path, transcriber, **kw):
        shown = []
        raw = tmp_path / "ss.raw"
        raw.write_bytes(pcm(0.2))
        kw.setdefault("interval", 1.0)
        kw.setdefault("min_audio", 0.5)
        kw.setdefault("segment_s", 2.0)
        eng = SegmentedPreviewEngine(raw, transcriber,
                                     lambda t, s=0: shown.append(t), **kw)
        return eng, shown

    def drive(self, eng, raw, total_s, step_s):
        data = b""
        t = step_s
        while t <= total_s + 1e-9:
            data += pcm(step_s)
            raw.write_bytes(data)
            eng._tick(data, len(data) / BPS)
            t += step_s

    def test_sustained_low_conf_silences_take(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("words", -0.8))
        self.drive(eng, tmp_path / "ss.raw", total_s=8.0, step_s=1.0)
        assert eng.stats["silenced"] == 1
        # the display ends on the ellipsis; later windows add no words
        assert shown and shown[-1] == "…"

    def test_clean_windows_never_silence(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("clear words", -0.3))
        self.drive(eng, tmp_path / "ss.raw", total_s=6.0, step_s=1.0)
        assert "silenced" not in eng.stats or eng.stats["silenced"] == 0
        assert "clear words" in " ".join(shown)

    def test_one_shaky_window_does_not_silence(self, tmp_path):
        replies = iter([("x", -0.9), ("clear", -0.2), ("clear", -0.2),
                        ("clear", -0.2), ("clear", -0.2)])
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: next(replies, ("clear", -0.2)))
        self.drive(eng, tmp_path / "ss.raw", total_s=6.0, step_s=1.0)
        assert not eng.stats.get("silenced")
        assert "clear" in " ".join(shown)

    def test_gate_off_disables_silencing(self, tmp_path):
        eng, shown = self.make_engine(
            tmp_path, lambda w, c: ("words", -0.9), conf_gate=False)
        self.drive(eng, tmp_path / "ss.raw", total_s=6.0, step_s=1.0)
        assert not eng.stats.get("silenced")
        assert "words" in " ".join(shown)
