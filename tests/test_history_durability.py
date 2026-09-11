"""History durability under crash and disk failure (Q6).

The existing history tests cover concurrent access, rollback, and
corrupt-row tolerance with in-process fakes. These add the missing
physical-failure seams:

* a REAL child process SIGKILLs itself mid-append-loop (crash injection
  with a deterministic row count — no timing guesses); every durably
  appended row survives, the loader never sees a partial row, and the
  store stays writable afterwards;
* disk-full (ENOSPC) on the append path raises, leaves the file
  byte-identical, and a retry succeeds;
* ENOSPC on the atomic replace behind update_text preserves the ORIGINAL
  file (atomicity under failure), and a retry lands the edit with the
  edited_from audit intact;
* a directory-fsync failure AFTER the atomic replace surfaces the error
  while leaving the new content intact (the rename is already durable;
  nothing is corrupted or half-rolled-back).

Invariant throughout: no unrelated history entry disappears and no
partial row ever becomes visible to readers.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fluidvoice import history as history_mod
from fluidvoice import paths

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def isolated_history(tmp_path, monkeypatch):
    # the REAL layout: <XDG_DATA_HOME>/sayit-ermano/history.jsonl, so a
    # child process resolves the same file through its own env
    data_home = tmp_path / "xdata"
    hpath = data_home / "sayit-ermano" / "history.jsonl"
    monkeypatch.setattr(paths, "history_file", lambda: hpath)
    monkeypatch.setattr(paths, "audio_dir",
                        lambda: data_home / "sayit-ermano" / "audio")
    return hpath


@pytest.fixture()
def child_env(isolated_history):
    return {**os.environ,
            "XDG_DATA_HOME": str(isolated_history.parent.parent),
            "PYTHONPATH": str(REPO)}


def rows(hpath: Path) -> list[dict]:
    if not hpath.exists():
        return []
    return [json.loads(line) for line in hpath.read_text().splitlines()
            if line.strip()]


class TestCrashInjection:
    def test_child_sigkill_mid_appends_preserves_committed_rows(
            self, tmp_path, isolated_history, child_env):
        """A real process crash between appends: the three fsynced rows
        survive verbatim, nothing partial is visible, and the store
        accepts new writes immediately."""
        child = tmp_path / "crashy_appender.py"
        child.write_text(textwrap.dedent("""
            import os, signal, sys
            from fluidvoice import history
            for i in range(1, 6):
                if i == 4:
                    os.kill(os.getpid(), signal.SIGKILL)  # crash: after 3
                history.append({"ts": float(i), "text": f"row {i}"})
        """))
        proc = subprocess.run(
            [sys.executable, str(child)], env=child_env, cwd=str(REPO),
            capture_output=True, text=True, timeout=60)
        assert proc.returncode == -signal.SIGKILL, (
            f"child did not die by SIGKILL: rc={proc.returncode} "
            f"{proc.stderr[-400:]}")
        committed = rows(isolated_history)
        assert [e["text"] for e in committed] == ["row 1", "row 2", "row 3"]
        assert all(e["ts"] in (1.0, 2.0, 3.0) for e in committed)
        # every visible line is complete JSON — a torn write never
        # surfaces as a row (and the reader skips nothing valid)
        assert history_mod.read_all() == committed
        # the store stays writable after the crash
        history_mod.append({"ts": 9.0, "text": "after crash"})
        assert [e["text"] for e in rows(isolated_history)] == \
            ["row 1", "row 2", "row 3", "after crash"]


class TestDiskFull:
    def test_append_enospc_raises_file_unchanged_retry_succeeds(
            self, isolated_history, monkeypatch):
        history_mod.append({"ts": 1.0, "text": "first"})
        before = isolated_history.read_bytes()
        real = history_mod._append_line
        state = {"failed": False}

        def enospc_once(hpath, line):
            if not state["failed"]:
                state["failed"] = True
                raise OSError(28, "No space left on device")
            return real(hpath, line)

        monkeypatch.setattr(history_mod, "_append_line", enospc_once)
        with pytest.raises(OSError, match="No space left"):
            history_mod.append({"ts": 2.0, "text": "second"})
        assert isolated_history.read_bytes() == before  # untouched
        history_mod.append({"ts": 2.0, "text": "second"})  # retry lands
        assert [e["text"] for e in rows(isolated_history)] == \
            ["first", "second"]

    def test_update_text_enospc_preserves_original_and_retry_edits(
            self, isolated_history, monkeypatch):
        history_mod.append({"ts": 1.0, "text": "as heard"})
        history_mod.append({"ts": 2.0, "text": "unrelated row"})
        before = isolated_history.read_bytes()
        real = history_mod._atomic_write
        state = {"failed": False}

        def enospc_once(hpath, lines):
            if not state["failed"]:
                state["failed"] = True
                raise OSError(28, "No space left on device")
            return real(hpath, lines)

        monkeypatch.setattr(history_mod, "_atomic_write", enospc_once)
        with pytest.raises(OSError, match="No space left"):
            history_mod.update_text(1.0, "edited")
        # atomicity under failure: the ORIGINAL rows are byte-identical
        # (no half-applied rewrite), and the unrelated row is still there
        assert isolated_history.read_bytes() == before
        assert history_mod.update_text(1.0, "edited") is True
        entries = {e["ts"]: e for e in rows(isolated_history)}
        assert entries[2.0]["text"] == "unrelated row"  # untouched
        assert entries[1.0]["text"] == "edited"
        assert entries[1.0]["edited_from"] == "as heard"  # audit preserved

    def test_dir_fsync_failure_after_replace_surfaces_without_corruption(
            self, isolated_history, monkeypatch):
        history_mod.append({"ts": 1.0, "text": "as heard"})
        real = history_mod._fsync_dir
        state = {"failed": False}

        def raise_once(directory):
            if not state["failed"]:
                state["failed"] = True
                raise OSError(5, "Input/output error")
            return real(directory)

        monkeypatch.setattr(history_mod, "_fsync_dir", raise_once)
        # the rename already made the new content durable; the fsync
        # failure must SURFACE (the caller learns durability of the
        # rename itself is not yet guaranteed) without corrupting or
        # rolling anything back
        with pytest.raises(OSError, match="Input/output error"):
            history_mod.update_text(1.0, "edited")
        assert rows(isolated_history) and \
            rows(isolated_history)[0]["text"] == "edited"
        # reader sees a fully consistent file either way
        assert all("text" in e for e in history_mod.read_all())
