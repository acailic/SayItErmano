"""P0.3 transactional history store: locking, durability, crash residue.

The reliability program requires these proofs, without lost entries:
- append-versus-edit (an edit must not lose a concurrent append)
- append-versus-trim (cap enforcement concurrent with appends)
- two-process mutation (two subprocesses hammering append/delete)
- crash residue (leftover .tmp sidecar/lock files tolerated + cleaned)
- audio rollback (append fails after audio copy -> copied audio removed)
- corrupt rows (torn/invalid JSONL lines must not crash or lose other rows)

All state lives under tmp_path (never the real user history dir); the
two-process test points the CHILDREN's XDG_DATA_HOME at tmp_path too.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import struct
import subprocess
import sys
import threading
import time
import wave
import zipfile
from pathlib import Path

import pytest

from fluidvoice import history

REPO_ROOT = Path(__file__).resolve().parent.parent


def write_wav(path: Path, seconds: float = 0.1) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        for i in range(1600):
            wf.writeframes(struct.pack("<h", int(9000 * math.sin(i / 5))))
    return path


@pytest.fixture
def hist(tmp_path, monkeypatch) -> Path:
    """Default store -> tmp history file + tmp audio dir."""
    hpath = tmp_path / "history.jsonl"
    monkeypatch.setattr(history.paths, "history_file", lambda: hpath)
    monkeypatch.setattr(history.paths, "audio_dir",
                        lambda: tmp_path / "audio")
    return hpath


def _read_lines(hpath: Path) -> list[dict]:
    """Strict read (unlike history's tolerant readers): every line must be
    valid JSON - a torn write under concurrency is exactly what these
    tests exist to catch. Splits on "\n" ONLY, like the production
    readers (N8): str.splitlines() would also split on U+2028/U+2029/
    U+0085, hiding exactly the data-loss class these tests must catch
    (a row whose text contains one of those characters)."""
    lines = hpath.read_text(encoding="utf-8").split("\n")
    return [json.loads(line) for line in lines if line]


class TestStoreInterface:
    def test_module_functions_delegate_to_default_store(self):
        assert isinstance(history._store, history.HistoryStore)

    def test_explicit_path_store_independent_of_paths(self, tmp_path):
        store = history.HistoryStore(tmp_path / "solo.jsonl")
        store.append({"ts": 1.0, "text": "solo"})
        assert [e["text"] for e in store.read_all()] == ["solo"]
        assert history.HistoryStore(lambda: tmp_path / "solo.jsonl").tail()

    def test_exclusive_lock_serializes_transactions(self, hist):
        """The sidecar flock is real: an externally held LOCK_EX blocks the
        store's append until released (same discipline the cross-process
        test relies on)."""
        history.append({"ts": 1.0, "text": "seed"})
        lock_fd = os.open(str(hist) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            done: list[bool] = []

            def worker():
                history.append({"ts": 2.0, "text": "after"})
                done.append(True)

            t = threading.Thread(target=worker)
            t.start()
            time.sleep(0.3)
            assert not done, "append ran while we held LOCK_EX"
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            t.join(timeout=5)
            assert done and not t.is_alive()
            assert [e["text"] for e in _read_lines(hist)] == \
                ["seed", "after"]
        finally:
            os.close(lock_fd)


class TestAppendVersusEdit:
    """The P0 defect: a GTK edit (read-modify-replace) could overwrite and
    lose a concurrent daemon append. Under LOCK_EX it cannot."""

    def test_edits_never_lose_concurrent_appends(self, hist):
        history.append({"ts": 0.5, "text": "original"})
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def appender():
            barrier.wait()
            try:
                for i in range(120):
                    history.append({"ts": float(i + 1), "text": f"a{i}"})
            except Exception as e:  # pragma: no cover - failure evidence
                errors.append(e)

        def editor():
            barrier.wait()
            try:
                for j in range(40):
                    assert history.update_text(0.5, f"edit{j}") is True
            except Exception as e:  # pragma: no cover - failure evidence
                errors.append(e)

        threads = [threading.Thread(target=appender),
                   threading.Thread(target=editor)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        assert not any(t.is_alive() for t in threads)

        entries = _read_lines(hist)  # strict: no torn lines either
        assert len(entries) == 121  # 1 seed + 120 appends, zero lost
        assert {e["text"] for e in entries if e["text"].startswith("a")} == \
            {f"a{i}" for i in range(120)}
        edited = next(e for e in entries if e["ts"] == 0.5)
        assert edited["text"] == "edit39"
        assert edited["edited_from"] == "original"  # first edit wins

    def test_deletes_never_lose_concurrent_appends(self, hist):
        history.append({"ts": 0.5, "text": "sacrifice"})
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def appender():
            barrier.wait()
            try:
                for i in range(80):
                    history.append({"ts": float(i + 1), "text": f"d{i}"})
            except Exception as e:  # pragma: no cover - failure evidence
                errors.append(e)

        def deleter():
            barrier.wait()
            try:
                for _ in range(20):
                    assert history.delete(0.5) in (0, 1)
            except Exception as e:  # pragma: no cover - failure evidence
                errors.append(e)

        threads = [threading.Thread(target=appender),
                   threading.Thread(target=deleter)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        entries = _read_lines(hist)
        # 80 appends survive; the sacrificial row is gone (exactly one
        # delete won the race, the rest were no-ops)
        assert len(entries) == 80
        assert {e["text"] for e in entries} == \
            {f"d{i}" for i in range(80)}
        assert all(e["ts"] != 0.5 for e in entries)


class TestAppendVersusTrim:
    """Cap enforcement concurrent with appends: trimming must never lose
    entries beyond the cap and never corrupt the file."""

    def test_concurrent_cap_trim_keeps_exactly_cap_entries(self, hist,
                                                           monkeypatch):
        monkeypatch.setattr(history, "MAX_ENTRIES", 20)
        monkeypatch.setattr(history, "_TAIL_WINDOW", 64)
        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def appender(slot: int):
            barrier.wait()
            try:
                for i in range(75):
                    history.append({"ts": float(slot * 1000 + i),
                                    "text": f"t{slot}-{i}"})
            except Exception as e:  # pragma: no cover - failure evidence
                errors.append(e)

        threads = [threading.Thread(target=appender, args=(s,))
                   for s in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors
        assert not any(t.is_alive() for t in threads)

        entries = _read_lines(hist)  # strict: every surviving line parses
        total_appended = 4 * 75
        assert len(entries) == min(total_appended, history.MAX_ENTRIES)
        texts = [e["text"] for e in entries]
        assert len(set(texts)) == len(texts)  # no duplicated rows
        assert set(texts) <= {f"t{s}-{i}" for s in range(4)
                              for i in range(75)}


class TestTwoProcessMutation:
    """Two real subprocesses hammering append/delete through the sidecar
    flock. The children get their own XDG_DATA_HOME (tmp_path), so this
    never touches the real user history dir."""

    CHILD = (
        "import sys\n"
        "from fluidvoice import history\n"
        "tag = sys.argv[1]\n"
        "if tag == 'B':\n"
        "    history.delete(0.25)\n"
        "for i in range(150):\n"
        "    history.append({'ts': (1 if tag == 'A' else 10000) + i,"
        " 'text': f'{tag}{i}'})\n"
    )

    def test_two_processes_append_and_delete_without_loss(self, tmp_path):
        data_home = tmp_path / "xdg-data"
        data_home.mkdir()
        hpath = data_home / "sayit-ermano" / "history.jsonl"
        hpath.parent.mkdir(parents=True)
        hpath.write_text(json.dumps({"ts": 0.25, "text": "sacrifice"})
                         + "\n", encoding="utf-8")
        env = dict(os.environ, XDG_DATA_HOME=str(data_home))
        procs = [subprocess.Popen(
            [sys.executable, "-c", self.CHILD, tag],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for tag in ("A", "B")]
        for p in procs:
            out, err = p.communicate(timeout=60)
            assert p.returncode == 0, err.decode()
        entries = _read_lines(hpath)
        # 150 + 150 appends all present, sacrificial row deleted, nothing
        # else lost or duplicated
        assert len(entries) == 300
        assert {e["text"] for e in entries} == \
            {f"{t}{i}" for t in "AB" for i in range(150)}
        assert all(e["ts"] != 0.25 for e in entries)
        ts_list = [e["ts"] for e in entries]
        assert len(set(ts_list)) == 300


class TestCrashResidue:
    """Leftover .tmp sidecars from interrupted transactions (and the
    ever-present lock file) must be tolerated and cleaned safely."""

    def test_stale_tmp_files_cleaned_on_next_transaction(self, hist,
                                                         tmp_path):
        history.append({"ts": 1.0, "text": "one"})
        history.append({"ts": 2.0, "text": "two"})
        stale_hist = hist.parent / f"{hist.name}.crashed3f2a.tmp"
        stale_hist.write_text("garbage", encoding="utf-8")
        adir = tmp_path / "audio"
        adir.mkdir()
        stale_audio = adir / "half-copied.wav.tmp"
        stale_audio.write_bytes(b"RIFF...")
        # tolerated: reads work with residue present
        assert [e["text"] for e in history.read_all()] == ["one", "two"]
        # cleaned by the next exclusive transaction
        assert history.delete(1.0) == 1
        assert not stale_hist.exists()
        assert not stale_audio.exists()
        assert [e["text"] for e in _read_lines(hist)] == ["two"]

    def test_backups_and_lock_file_never_cleaned(self, hist):
        history.append({"ts": 1.0, "text": "one"})
        bak = hist.parent / (hist.name + ".bak-20260101-000000")
        bak.write_text("backup", encoding="utf-8")
        lock = hist.parent / (hist.name + ".lock")
        assert lock.exists()  # sidecar created by the transaction above
        history.append({"ts": 2.0, "text": "two"})
        assert bak.exists() and lock.exists()
        assert len(_read_lines(hist)) == 2

    def test_append_cleans_audio_tmp_residue(self, hist, tmp_path):
        adir = tmp_path / "audio"
        adir.mkdir()
        stale = adir / "torn.wav.tmp"
        stale.write_bytes(b"x")
        history.append({"ts": 1.0, "text": "x"})
        assert not stale.exists()


class TestAudioRollback:
    def test_failed_append_rolls_back_copied_audio(self, hist, tmp_path,
                                                   monkeypatch):
        src = write_wav(tmp_path / "src.wav")
        adir = tmp_path / "audio"

        def boom(hpath, line):
            raise OSError("disk fell over mid-append")

        monkeypatch.setattr(history, "_append_line", boom)
        with pytest.raises(OSError):
            history.append({"ts": 1.0, "text": "doomed"}, audio_src=src,
                           keep_audio=True)
        # the copied audio is gone - no file, no temp, no row
        assert list(adir.glob("*.wav")) == []
        assert list(adir.glob("*.wav.tmp")) == []
        assert history.read_all() == []

    def test_trim_failure_after_landed_row_keeps_its_audio(
            self, hist, tmp_path, monkeypatch):
        """F3 supersedes the original P0.3 expectation: when the append
        LINE durably landed and only the later cap enforcement fails, the
        retained audio must NOT be rolled back - the row on disk
        references it, so deleting it would leave a dangling audio path.
        (Audio rolls back only while the row never landed - the test
        above.)"""
        src = write_wav(tmp_path / "src.wav")
        monkeypatch.setattr(history, "_TAIL_WINDOW", 1)
        monkeypatch.setattr(history, "_enforce_entry_cap_unlocked",
                            lambda hpath: (_ for _ in ()).throw(
                                OSError("trim failed")))
        with pytest.raises(OSError):
            history.append({"ts": 1.0, "text": "kept"}, audio_src=src,
                           keep_audio=True)
        entries = history.read_all()
        assert [e["text"] for e in entries] == ["kept"]
        assert Path(entries[0]["audio"]).exists()

    def test_collision_proof_audio_names(self, hist, tmp_path):
        src = write_wav(tmp_path / "src.wav")
        for i in range(3):
            history.append({"ts": float(i + 1), "text": f"n{i}"},
                           audio_src=src, keep_audio=True, budget_gb=1.0)
        saved = sorted((tmp_path / "audio").glob("*.wav"))
        assert len(saved) == 3  # same second, three distinct files
        assert len({p.name for p in saved}) == 3
        for i in range(3):
            p = history.audio_path_for(float(i + 1))
            assert p is not None and p.exists()

    def test_retained_audio_is_complete_under_its_final_name(self, hist,
                                                             tmp_path):
        """The audio lands via tmp+replace: no partial *.wav is ever
        visible at the name a row references (readers see all-or-nothing)."""
        src = write_wav(tmp_path / "src.wav", seconds=0.2)
        history.append({"ts": 1.0, "text": "kept"}, audio_src=src,
                       keep_audio=True)
        p = history.audio_path_for(1.0)
        assert p is not None
        with wave.open(str(p), "rb") as wf:  # parses as a full wav
            assert wf.getnframes() > 0


class TestOrphanAudio:
    def _seed_with_audio(self, hist, tmp_path):
        adir = tmp_path / "audio"
        adir.mkdir()
        rows = {}
        lines = []
        for i, name in enumerate(("a1", "a2")):
            wav = write_wav(adir / f"{name}.wav")
            ts = float(i + 1)
            rows[name] = ts
            lines.append(json.dumps({"ts": ts, "text": name,
                                     "audio": str(wav)}))
        hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
        orphan = write_wav(adir / "orphan.wav")
        return adir, rows, orphan

    def test_prune_removes_only_unreferenced_audio(self, hist, tmp_path):
        adir, rows, orphan = self._seed_with_audio(hist, tmp_path)
        assert history.prune_orphan_audio() == 1
        assert not orphan.exists()
        assert (adir / "a1.wav").exists() and (adir / "a2.wav").exists()

    def test_delete_drop_audio_prunes_that_audio_too(self, hist, tmp_path):
        adir, rows, orphan = self._seed_with_audio(hist, tmp_path)
        assert history.delete(rows["a1"], drop_audio=True) == 1
        assert not (adir / "a1.wav").exists()
        assert (adir / "a2.wav").exists()
        assert not orphan.exists()  # dropped in the same transaction

    def test_delete_keep_audio_leaves_audio_alone(self, hist, tmp_path):
        adir, rows, orphan = self._seed_with_audio(hist, tmp_path)
        assert history.delete(rows["a1"], drop_audio=False) == 1
        assert (adir / "a1.wav").exists()
        assert orphan.exists()  # nothing pruned: caller kept audio

    def test_clear_keep_audio_vs_drop_audio(self, hist, tmp_path):
        adir, rows, orphan = self._seed_with_audio(hist, tmp_path)
        stale = adir / "torn.wav.tmp"
        stale.write_bytes(b"x")
        assert history.clear(drop_audio=False) == 2
        assert (adir / "a1.wav").exists() and orphan.exists()
        assert history.clear(drop_audio=True) == 0  # already empty
        assert list(adir.glob("*.wav")) == []
        assert not stale.exists()  # residue cleaned in the same sweep


class TestCorruptRows:
    """Torn/invalid JSONL lines must not crash or lose other rows - the
    tolerant reader behavior, kept; rewrites then drop the bad lines."""

    def _seed_corrupt(self, hist):
        hist.write_bytes(
            b'{"ts": 1.0, "text": "good one"}\n'
            b'this is not json at all\n'
            b'{"ts": 2.0, "text": "torn line no newline"}'
            b'\xff\xfe{"ts": 3.0, "text": "invalid utf8 bytes"}\n'
            b'{"ts": 4.0, "text": "good two"}\n')

    def test_reads_skip_bad_lines_and_keep_good_ones(self, hist):
        self._seed_corrupt(hist)
        assert [e["text"] for e in history.read_all()] == \
            ["good one", "good two"]
        assert [e["text"] for e in history.tail()] == \
            ["good one", "good two"]
        assert [e["text"] for e in history.search("good")] == \
            ["good two", "good one"]  # newest first

    def test_edit_survives_corrupt_lines_without_losing_rows(self, hist):
        self._seed_corrupt(hist)
        assert history.update_text(1.0, "edited") is True
        entries = _read_lines(hist)
        assert [(e["ts"], e["text"]) for e in entries] == \
            [(1.0, "edited"), (4.0, "good two")]
        assert history.update_text(999.0, "nope") is False
        assert len(_read_lines(hist)) == 2  # nothing clobbered on a miss

    def test_delete_miss_is_safe_with_corrupt_lines(self, hist):
        self._seed_corrupt(hist)
        assert history.delete(777.0) == 0
        assert [e["text"] for e in history.read_all()] == \
            ["good one", "good two"]


class TestUnicodeLineSeparators:
    """F2 golden test: rows whose text contains U+2028 (LINE SEPARATOR),
    U+2029 (PARAGRAPH SEPARATOR) or U+0085 (NEL) - which
    json.dumps(ensure_ascii=False) writes RAW inside the JSON string -
    must survive append -> read_all -> rewrite -> read_all. The old
    splitlines() readers tore each such row in two unparseable halves
    (silently dropped, then permanently erased by the next rewrite)."""

    SPECIAL = ("line one\u2028line two",       # LINE SEPARATOR
               "para one\u2029para two",       # PARAGRAPH SEPARATOR
               "nel\u0085also nel",            # NEXT LINE (NEL)
               "backslash-n literal \\n stays",  # 2-char sequence \n
               "mix \u2028 \u2029 \u0085 \\n all")

    def test_round_trip_append_read_rewrite_read(self, hist):
        history.append({"ts": 0.0, "text": "plain seed"})
        for i, text in enumerate(self.SPECIAL, start=1):
            history.append({"ts": float(i), "text": text})
        history.append({"ts": 99.0, "text": "plain tail"})

        # every reader sees every row, exactly once, in order
        assert [e["text"] for e in history.read_all()] == \
            ["plain seed", *self.SPECIAL, "plain tail"]
        assert [e["text"] for e in history.tail(20)] == \
            ["plain seed", *self.SPECIAL, "plain tail"]
        assert [e["text"] for e in history.search("line") ] == \
            ["line one\u2028line two"]

        # the file on disk holds one physical line per row: the special
        # characters are inside JSON strings, never record separators
        disk = hist.read_text(encoding="utf-8").split("\n")
        assert len(disk) == len(self.SPECIAL) + 2 + 1  # + trailing ""

        # a rewrite (update of an unrelated row) must not erase them
        assert history.update_text(0.0, "PLAIN SEED") is True
        texts = [e["text"] for e in history.read_all()]
        assert texts == ["PLAIN SEED", *self.SPECIAL, "plain tail"]

        # delete/cap rewrites keep them too
        assert history.delete(99.0) == 1
        assert [e["text"] for e in history.read_all()] == \
            ["PLAIN SEED", *self.SPECIAL]

    def test_export_zip_carries_them_through(self, hist, tmp_path):
        for i, text in enumerate(self.SPECIAL, start=1):
            history.append({"ts": float(i), "text": text})
        zpath = tmp_path / "export.zip"
        assert history.export_zip(zpath) == len(self.SPECIAL)
        with zipfile.ZipFile(zpath) as zf:
            body = zf.read("history.jsonl").decode("utf-8")
        assert [json.loads(l) for l in body.split("\n") if l] == \
            [{"ts": float(i), "text": t}
             for i, t in enumerate(self.SPECIAL, start=1)]


class TestCapPathTolerance:
    """F3: one invalid UTF-8 byte anywhere in a >128 KiB history file must
    not make every append() raise (the strict read inside the cap path
    escaped as UnicodeDecodeError AFTER the row landed), and a cap-
    enforcement failure after a successful append line must not roll back
    audio the landed row references."""

    def test_append_survives_invalid_byte_in_oversized_file(self, hist):
        rows = "\n".join(json.dumps({"ts": float(i), "text": "x" * 300})
                         for i in range(600)) + "\n"
        data = rows.encode("utf-8")
        assert len(data) > history._TAIL_WINDOW  # >128 KiB: cap path engages
        hist.write_bytes(data.replace(b'"ts"', b'\xff"ts', 1))
        history.append({"ts": 999.0, "text": "new take"})  # must not raise
        texts = [e["text"] for e in history.read_all()]
        assert texts.count("new take") == 1
        assert texts.count("x" * 300) == 599  # only the torn row is gone


class TestObservability:
    """F11: the exceptional history paths are visible on stderr (house
    log idiom) - a silent unlocked fallback hid the fact that an
    exclusive transaction ran WITHOUT the lost-update guarantee."""

    def test_unlocked_fallback_is_logged(self, tmp_path, capsys,
                                         monkeypatch):
        store = history.HistoryStore(tmp_path / "h.jsonl")
        monkeypatch.setattr(store, "_lock_path",
                            lambda: Path("/nonexistent-dir-xyz/lock"))
        store.append({"ts": 1.0, "text": "unlocked"})
        assert [e["text"] for e in store.read_all()] == ["unlocked"]
        assert "proceeding unlocked" in capsys.readouterr().err

    def test_audio_rollback_is_logged(self, hist, tmp_path, monkeypatch,
                                      capsys):
        src = write_wav(tmp_path / "src.wav")

        def boom(hpath, line):
            raise OSError("disk full")

        monkeypatch.setattr(history, "_append_line", boom)
        with pytest.raises(OSError):
            history.append({"ts": 1.0, "text": "doomed"}, audio_src=src,
                           keep_audio=True)
        assert "rolling back" in capsys.readouterr().err


class TestAtomicWriteDurability:
    def test_failed_replace_cleans_tmp_and_keeps_original(self, hist,
                                                          monkeypatch):
        hist.write_text('{"ts": 1.0, "text": "original"}\n',
                        encoding="utf-8")
        before = hist.read_bytes()

        def boom(src, dst):
            raise OSError("replace failed")

        monkeypatch.setattr(history.os, "replace", boom)
        with pytest.raises(OSError):
            history._atomic_write(hist, ['{"ts": 2.0}'])
        assert hist.read_bytes() == before
        assert list(hist.parent.glob("*.tmp")) == []

    def test_successful_write_leaves_no_tmp_residue(self, hist):
        history._atomic_write(hist, ['{"ts": 1.0, "text": "clean"}'])
        assert list(hist.parent.glob("*.tmp")) == []
        assert json.loads(hist.read_text(encoding="utf-8"))["ts"] == 1.0

    def test_empty_write_is_an_empty_file_not_garbage(self, hist):
        history._atomic_write(hist, [])
        assert hist.read_bytes() == b""
