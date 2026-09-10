from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

from fluidvoice import history
from fluidvoice.audio_utils import duration_seconds, is_silent, wav_stats


def write_wav(path: Path, seconds: float, *, loud: bool, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        for i in range(int(rate * seconds)):
            v = int(9000 * math.sin(2 * math.pi * 300 * i / rate)) if loud else 0
            wf.writeframes(struct.pack("<h", v))
    return path


class TestAudioUtils:
    def test_loud_audio_not_silent(self, tmp_path):
        wav = write_wav(tmp_path / "loud.wav", 1.0, loud=True)
        assert not is_silent(str(wav))
        stats = wav_stats(str(wav))
        assert stats.rms > 0.01 and stats.peak > 0.1

    def test_digital_silence_detected(self, tmp_path):
        wav = write_wav(tmp_path / "silent.wav", 2.0, loud=False)
        assert is_silent(str(wav))

    def test_duration(self, tmp_path):
        wav = write_wav(tmp_path / "d.wav", 1.5, loud=True)
        assert abs(duration_seconds(str(wav)) - 1.5) < 0.01

    def test_empty_file_stats(self, tmp_path):
        wav = write_wav(tmp_path / "e.wav", 0.0, loud=False)
        stats = wav_stats(str(wav))
        assert stats.peak == 0.0 and stats.rms == 0.0


class TestHistory:
    def test_append_and_tail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history.paths, "history_file",
                            lambda: tmp_path / "h.jsonl")
        history.append({"ts": 1, "text": "first"})
        history.append({"ts": 2, "text": "second"})
        entries = history.tail(10)
        assert [e["text"] for e in entries] == ["first", "second"]

    def test_tail_n(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history.paths, "history_file",
                            lambda: tmp_path / "h.jsonl")
        for i in range(5):
            history.append({"ts": i, "text": f"t{i}"})
        assert [e["text"] for e in history.tail(2)] == ["t3", "t4"]

    def test_corrupt_lines_skipped(self, tmp_path, monkeypatch):
        f = tmp_path / "h.jsonl"
        f.write_text('{"ts": 1, "text": "ok"}\nnot json\n')
        monkeypatch.setattr(history.paths, "history_file", lambda: f)
        assert [e["text"] for e in history.tail()] == ["ok"]

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history.paths, "history_file",
                            lambda: tmp_path / "nope.jsonl")
        assert history.tail() == []

    def test_audio_saved_when_requested(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history.paths, "history_file",
                            lambda: tmp_path / "h.jsonl")
        monkeypatch.setattr(history.paths, "audio_dir", lambda: tmp_path / "audio")
        src = write_wav(tmp_path / "src.wav", 0.5, loud=True)
        history.append({"ts": 1, "text": "with audio"}, audio_src=src,
                       keep_audio=True, budget_gb=1.0)
        saved = list((tmp_path / "audio").glob("*.wav"))
        assert len(saved) == 1

    def test_audio_budget_prunes_oldest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(history.paths, "history_file",
                            lambda: tmp_path / "h.jsonl")
        monkeypatch.setattr(history.paths, "audio_dir", lambda: tmp_path / "audio")
        adir = tmp_path / "audio"
        adir.mkdir()
        import os
        import time as _time
        old = adir / "old.wav"
        new = adir / "new.wav"
        old.write_bytes(b"x" * 1000)
        new.write_bytes(b"y" * 1000)
        os.utime(old, (_time.time() - 100, _time.time() - 100))
        history._enforce_budget(adir, budget_gb=1000 / 1024 ** 3)  # ~1KB budget
        assert not old.exists()
        assert new.exists()


class TestHistoryCapAndTail:
    def _many(self, path, n):
        with open(path, "a", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps({"ts": i, "text": f"e{i}"}) + "\n")

    def test_entry_cap_trims_old(self, tmp_path, monkeypatch):
        from fluidvoice import history
        f = tmp_path / "h.jsonl"
        monkeypatch.setattr(history.paths, "history_file", lambda: f)
        monkeypatch.setattr(history, "MAX_ENTRIES", 50)
        monkeypatch.setattr(history, "_TAIL_WINDOW", 256)  # let the cap logic run
        for i in range(80):
            history.append({"ts": i, "text": f"e{i}"})
        lines = f.read_text().splitlines()
        assert len(lines) == 50
        assert json.loads(lines[0])["text"] == "e30"  # newest kept

    def test_tail_reads_only_end_of_big_file(self, tmp_path, monkeypatch):
        from fluidvoice import history
        f = tmp_path / "h.jsonl"
        monkeypatch.setattr(history.paths, "history_file", lambda: f)
        self._many(f, 2000)
        entries = history.tail(3)
        assert [e["text"] for e in entries] == ["e1997", "e1998", "e1999"]

    def test_tail_partial_first_line_dropped(self, tmp_path, monkeypatch):
        from fluidvoice import history
        f = tmp_path / "h.jsonl"
        f.write_text("half-line-without-newline\n" + "\n".join(
            json.dumps({"ts": i, "text": f"t{i}"}) for i in range(10)) + "\n")
        monkeypatch.setattr(history.paths, "history_file", lambda: f)
        # force the window path by making the file "big" relative to window
        monkeypatch.setattr(history, "_TAIL_WINDOW", 64)
        entries = history.tail(10)
        assert entries and all(e["text"].startswith("t") for e in entries)


class TestUpdateText:
    """Inline repair: rewrite one entry's text in the JSONL (research §4)."""

    def test_rewrites_matching_entry(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 111.0, "text": "before"})
        history.append({"ts": 222.0, "text": "other"})
        assert history.update_text(111.0, "after") is True
        entries = {e["ts"]: e["text"] for e in history.read_all()}
        assert entries[111.0] == "after"
        assert entries[222.0] == "other"

    def test_miss_returns_false_and_writes_nothing(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 1.0, "text": "keep"})
        assert history.update_text(999.0, "nope") is False
        assert [e["text"] for e in history.read_all()] == ["keep"]

    def test_audio_retention_untouched(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 5.0, "text": "with audio", "audio": "/x/y.wav"})
        history.update_text(5.0, "edited")
        assert history.read_all()[0]["audio"] == "/x/y.wav"

    def test_edit_records_edited_from(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 1.0, "text": "old"})
        assert history.update_text(1.0, "new") is True
        entry = history.read_all()[0]
        assert entry["text"] == "new"
        assert entry["edited_from"] == "old"

    def test_same_text_update_sets_no_edited_from(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 1.0, "text": "same"})
        assert history.update_text(1.0, "same") is True
        assert "edited_from" not in history.read_all()[0]

    def test_second_edit_keeps_original_edited_from(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 1.0, "text": "first"})
        history.update_text(1.0, "second")
        history.update_text(1.0, "third")
        entry = history.read_all()[0]
        assert entry["text"] == "third"
        assert entry["edited_from"] == "first"

    def test_entries_without_edited_from_survive_update_of_other(
            self, tmp_path, monkeypatch):
        # pre-feature line (no edited_from key) round-trips alongside a
        # newly-edited entry
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        hpath.write_text('{"ts": 1.0, "text": "pre-feature"}\n'
                         '{"ts": 2.0, "text": "target"}\n',
                         encoding="utf-8")
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        assert history.update_text(2.0, "targeted") is True
        entries = history.read_all()
        assert entries[0] == {"ts": 1.0, "text": "pre-feature"}
        assert entries[1]["edited_from"] == "target"
        assert [e["text"] for e in history.tail()] == [
            "pre-feature", "targeted"]

    def test_edited_from_survives_rewrite_and_export(self, tmp_path,
                                                     monkeypatch):
        # edited_from round-trips through _rewrite (delete of another
        # entry) and export_zip - the dictionary learner's durable signal
        import zipfile

        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        history.append({"ts": 1.0, "text": "open the miro board app"})
        history.update_text(1.0, "open the Miro board app")
        history.append({"ts": 2.0, "text": "unrelated"})
        assert history.delete(2.0) == 1  # _rewrite path
        entries = history.read_all()
        assert entries == [{"ts": 1.0, "text": "open the Miro board app",
                            "edited_from": "open the miro board app"}]
        zpath = tmp_path / "h.zip"
        assert history.export_zip(zpath) == 1
        with zipfile.ZipFile(zpath) as zf:
            line = zf.read("history.jsonl").decode("utf-8").strip()
        import json as _json
        assert _json.loads(line)["edited_from"] == "open the miro board app"
