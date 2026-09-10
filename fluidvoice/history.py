"""Transcription history (JSONL) with entry cap, audio budget and a tail read
that never loads the whole file.

P0.3 reliability: history operations are transactional. A sidecar `flock`
(`<history>.lock` in the same directory) serializes access across threads
and processes - LOCK_SH for reads, LOCK_EX spanning each complete append
or read-modify-replace transaction, so a GTK edit can never overwrite and
lose a daemon append. Rewrites go through unique same-directory temp
files, fsync, atomic replace and a directory fsync, so a crash can never
leave a half-written history file (at worst a stale `*.tmp` sidecar,
which the next exclusive transaction removes). Retained audio gets
collision-proof names, is rolled back when the history append fails, and
orphaned audio is pruned safely under the same lock. The row schema and
the timestamp-keyed module-level functions are unchanged in this release.
"""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from . import paths

MAX_ENTRIES = 5000
_TAIL_WINDOW = 128 * 1024  # bytes read from the end for tail()
_LOCK_SUFFIX = ".lock"     # sidecar flock; never the JSONL fd itself
_TMP_SUFFIX = ".tmp"       # unique same-dir transaction temp files


def _log(msg: str) -> None:
    """House log idiom (see pipeline.log): stderr, timestamped, quiet -
    only exceptional history paths ever log (F11)."""
    print(f"[sayit-ermano] {time.strftime('%H:%M:%S')} history: {msg}",
          file=sys.stderr, flush=True)


def _split_jsonl(text: str) -> list[str]:
    """Split decoded history text into JSONL lines on "\n" ONLY.

    str.splitlines() would also split on U+2028/U+2029/U+0085 - three
    characters json.dumps(ensure_ascii=False) leaves RAW inside JSON
    strings, so a row containing one would tear into two unparseable
    halves: dropped by every reader and erased by the next rewrite (the
    F2 data-loss class). The writers' record separator is exactly "\n"
    (\r never occurs: writers never emit it), so the readers split on
    exactly that. The trailing empty remainder after the final newline is
    not a record and is dropped.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


# -- durability primitives ------------------------------------------------------

def _fsync_dir(directory: Path) -> None:
    """fsync a directory fd so a rename inside it survives a crash."""
    try:
        dfd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def _atomic_write(hpath: Path, lines: list[str]) -> None:
    """Replace the JSONL with `lines` atomically AND durably.

    Unique same-directory temp file (`mkstemp` - a crashed transaction's
    leftover can never collide with ours), write, flush, fsync, atomic
    `os.replace`, then fsync the directory so the rename itself is
    durable. On failure the temp file is removed again. Callers hold
    LOCK_EX.
    """
    data = "\n".join(lines) + ("\n" if lines else "")
    fd, tmp_name = tempfile.mkstemp(dir=hpath.parent,
                                    prefix=hpath.name + ".",
                                    suffix=_TMP_SUFFIX)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, hpath)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _fsync_dir(hpath.parent)


def _append_line(hpath: Path, line: str) -> None:
    """Append one JSONL line durably (flush + fsync). Callers hold LOCK_EX."""
    with open(hpath, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _clean_tmp_in(directory: Path | None, prefix: str = "") -> None:
    """Unlink leftover transaction temp files (crash residue) in one
    directory: names starting with `prefix` and ending in `.tmp`.

    Safe only under LOCK_EX: while the exclusive lock is held no other
    transaction can be mid-write, so any match is stale. Lock files
    (`.lock`) and `*.bak-*` backups never match.
    """
    if directory is None:
        return
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if name.startswith(prefix) and name.endswith(_TMP_SUFFIX):
            with contextlib.suppress(OSError):
                (directory / name).unlink()


def _retain_audio(audio_src: Path, adir: Path) -> Path | None:
    """Copy retained audio under a collision-proof name, atomically.

    Same discipline as _atomic_write: copy to a unique `*.wav.tmp` in the
    destination dir, fsync, atomic replace, fsync the dir - a reader never
    sees a partial `*.wav` under its final name. The name is timestamp +
    random suffix, so concurrent retains can never collide or overwrite
    each other. Returns None (leaving no residue) on OSError.
    """
    adir.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:12]}.wav"
    final = adir / name
    tmp = adir / (name + _TMP_SUFFIX)
    try:
        shutil.copy2(audio_src, tmp)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return None
    _fsync_dir(adir)
    return final


def _enforce_budget(adir: Path, budget_gb: float) -> None:
    """Delete oldest `*.wav` until the total is back under budget.

    Tolerates files vanishing mid-scan (best-effort maintenance path)."""
    try:
        items = []
        for p in adir.glob("*.wav"):
            try:
                items.append((p.stat().st_mtime, p.stat().st_size, p))
            except OSError:
                continue  # vanished mid-scan: nothing to bill it for
    except OSError:
        return
    items.sort(key=lambda t: t[0], reverse=True)  # newest first
    budget = budget_gb * 1024 ** 3
    total = sum(size for _, size, _ in items)
    for _, size, p in reversed(items):  # oldest first
        if total <= budget:
            break
        total -= size
        p.unlink(missing_ok=True)


def _enforce_entry_cap_unlocked(hpath: Path) -> None:
    """Trim to MAX_ENTRIES (newest kept) once the file outgrows the tail
    window. Callers hold LOCK_EX."""
    try:
        if hpath.stat().st_size < _TAIL_WINDOW:
            return
        # tolerant decode like every other reader (errors="replace"),
        # and the guard covers ValueError too (UnicodeDecodeError is a
        # ValueError): ONE torn byte anywhere in a >128 KiB file must not
        # turn every future append into a failure - and must not trigger
        # the audio rollback for a row that already landed (F3).
        data = hpath.read_bytes().decode("utf-8", errors="replace")
        lines = _split_jsonl(data)
        if len(lines) > MAX_ENTRIES:
            _atomic_write(hpath, lines[-MAX_ENTRIES:])
    except (OSError, ValueError):
        pass


# -- the store -------------------------------------------------------------------

class HistoryStore:
    """Transactional JSONL history store (P0.3).

    One deep module owns every history concern: cross-process/thread
    locking, durable atomic rewrites, the entry cap, audio retention,
    rollback and orphan pruning. The module-level functions below
    delegate to a default instance, so existing callers (daemon, cli,
    gtkui, command, pipeline, doctor) need no changes. Paths resolve
    through `fluidvoice.paths` at operation time (the test seam); an
    explicit path or callable pins a store to one file.
    """

    def __init__(self, history_file: Path | Callable[[], Path] | None = None):
        self._history_file = history_file

    # -- path resolution (dynamic: honors paths.py and test monkeypatches) --

    def _hpath(self) -> Path:
        hf = self._history_file
        if hf is None:
            return paths.history_file()
        return hf() if callable(hf) else Path(hf)

    def _lock_path(self) -> Path:
        # Sidecar of the JSONL file NAME, not a fd of the JSONL itself:
        # lock identity stays stable while the history file is atomically
        # swapped underneath, and two different history files in one
        # directory never contend with each other.
        hpath = self._hpath()
        return hpath.parent / (hpath.name + _LOCK_SUFFIX)

    # -- locking ----------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self, exclusive: bool) -> Iterator[None]:
        """Hold the sidecar flock for a whole transaction.

        LOCK_SH lets reads run concurrently; LOCK_EX spans complete
        append / read-modify-replace transactions. The kernel drops the
        lock when the holder dies, so a crash never wedges later
        transactions - it can only leave a `*.tmp` sidecar behind (removed
        under the next exclusive transaction). If the lock file cannot be
        opened at all (a read against a not-yet-existing directory), the
        read proceeds unlocked - the same tolerance the old code had for
        missing files.
        """
        if exclusive:
            # writes need the lock file's directory to exist
            self._hpath().parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as e:
            # the transaction proceeds WITHOUT the very lost-update
            # guarantee this module promises (EROFS/EACCES on the lock
            # sidecar) - that must be visible, not silent (F11)
            _log(f"lock unavailable ({e!r}) - proceeding unlocked")
            yield  # nothing to protect: proceed unlocked and tolerant
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _clean_stale_tmp(self, hpath: Path, adir: Path | None = None) -> None:
        """Remove crash residue: stale transaction temps beside the JSONL
        (`<name>.*.tmp`) and, when an audio dir is given, stale `*.tmp`
        retains in it. Callers hold LOCK_EX (see _clean_tmp_in)."""
        _clean_tmp_in(hpath.parent, hpath.name + ".")
        _clean_tmp_in(adir)

    # -- unlocked read helpers (callers hold the lock) -------------------------

    def _read_all_unlocked(self) -> list[dict]:
        hpath = self._hpath()
        try:
            data = hpath.read_bytes()
        except OSError:
            return []
        out = []
        # errors="replace" (like tail): torn bytes must not crash or hide
        # the other rows - the damaged line simply fails json parsing
        for line in _split_jsonl(data.decode("utf-8", errors="replace")):
            with contextlib.suppress(json.JSONDecodeError):
                out.append(json.loads(line))
        return out

    def _tail_unlocked(self, n: int) -> list[dict]:
        hpath = self._hpath()
        try:
            size = hpath.stat().st_size
        except OSError:
            return []
        with open(hpath, "rb") as fh:
            fh.seek(max(0, size - _TAIL_WINDOW))
            chunk = fh.read()
        lines = _split_jsonl(chunk.decode("utf-8", errors="replace"))
        if size > _TAIL_WINDOW and lines:
            lines = lines[1:]  # first line is likely partial
        out = []
        for line in lines[-n:]:
            with contextlib.suppress(json.JSONDecodeError):
                out.append(json.loads(line))
        return out

    def _prune_orphan_audio_unlocked(self) -> int:
        """Remove managed `*.wav` files no history row references.

        Only ever touches paths.audio_dir(), only under LOCK_EX (no
        concurrent append can be mid-copy). Callers hold the lock."""
        adir = paths.audio_dir()
        referenced: set[Path] = set()
        for entry in self._read_all_unlocked():
            audio = entry.get("audio")
            if audio:
                with contextlib.suppress(OSError, ValueError):
                    referenced.add(Path(audio).resolve())
        _clean_tmp_in(adir)  # stale retain temps are residue too
        try:
            wavs = list(adir.glob("*.wav"))
        except OSError:
            return 0
        removed = 0
        for f in wavs:
            try:
                if f.resolve() in referenced:
                    continue
                f.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed

    def _rewrite_unlocked(self, hpath: Path, keep: Callable[[dict], bool],
                          drop_audio: bool) -> int:
        """Keep entries matching `keep`; delete the rest (+ their audio).

        One LOCK_EX transaction: the re-read and the atomic replace cannot
        interleave with a concurrent append, so an edit never loses one.
        When drop_audio is set, audio no longer referenced by any kept row
        is pruned (the dropped rows' files - and any orphans a crashed
        append left behind); with drop_audio=False audio is never touched.
        """
        entries = self._read_all_unlocked()
        kept_lines = []
        removed = 0
        for entry in entries:
            if keep(entry):
                kept_lines.append(json.dumps(entry, ensure_ascii=False))
            else:
                removed += 1
        _atomic_write(hpath, kept_lines)
        if drop_audio:
            self._prune_orphan_audio_unlocked()
        return removed

    # -- public operations (each one lock acquisition, never nested) ------------

    def append(self, entry: dict, audio_src: Path | None = None,
               keep_audio: bool = False, budget_gb: float = 4.0) -> None:
        """Append one entry as a single LOCK_EX transaction.

        Retained audio is copied atomically to a collision-proof name
        BEFORE the JSONL append and rolled back if that append fails, so a
        failed append leaves no orphaned audio. The cap runs under the
        same lock, so trimming can never race an append into a loss.
        """
        hpath = self._hpath()
        with self._locked(exclusive=True):
            self._clean_stale_tmp(hpath, paths.audio_dir())
            copied: Path | None = None
            if audio_src and keep_audio:
                copied = _retain_audio(audio_src, paths.audio_dir())
                if copied is not None:
                    entry["audio"] = str(copied)
            wrote = False
            try:
                _append_line(hpath, json.dumps(entry, ensure_ascii=False))
                wrote = True  # the row durably references the audio now
                _enforce_entry_cap_unlocked(hpath)
            except BaseException:
                # roll back ONLY the audio THIS append copied, and only
                # while the row referencing it never landed: once the
                # JSONL line is on disk, deleting the audio would leave a
                # dangling audio path in history (F3 - e.g. a cap-enforce
                # failure after a successful append line)
                if copied is not None and not wrote:
                    _log(f"append failed before the row landed - rolling "
                         f"back retained audio {copied.name}")
                    with contextlib.suppress(OSError):
                        copied.unlink()
                raise
            if copied is not None:
                _enforce_budget(paths.audio_dir(), budget_gb)

    def tail(self, n: int = 20) -> list[dict]:
        """Last `n` entries, reading only the final 128 KB of the file."""
        with self._locked(exclusive=False):
            return self._tail_unlocked(n)

    def read_all(self) -> list[dict]:
        """Every parseable entry, oldest first (small: file is capped at
        MAX_ENTRIES). tail() reads only the last 128 KB, so anything that
        must see the whole file (search, stats, export, rewrite) comes
        here."""
        with self._locked(exclusive=False):
            return self._read_all_unlocked()

    def search(self, query: str = "", limit: int = 100) -> list[dict]:
        """Entries filtered by a substring over text/raw/app, newest first."""
        q = (query or "").strip().lower()
        with self._locked(exclusive=False):
            if not q:
                return self._tail_unlocked(limit)
            out = []
            for entry in reversed(self._read_all_unlocked()):
                if len(out) >= limit:
                    break
                hay = f"{entry.get('text', '')} {entry.get('raw', '')} " \
                      f"{entry.get('app') or ''}".lower()
                if q in hay:
                    out.append(entry)
            return out

    def audio_path_for(self, ts: float) -> Path | None:
        with self._locked(exclusive=False):
            for entry in self._read_all_unlocked():
                if abs(entry.get("ts", 0) - ts) < 1e-6:
                    p = entry.get("audio")
                    if p and Path(p).exists():
                        return Path(p)
        return None

    def rewrite(self, keep: Callable[[dict], bool], drop_audio: bool) -> int:
        """Keep entries matching `keep`; delete the rest (+ their audio).
        One LOCK_EX read-modify-replace transaction."""
        with self._locked(exclusive=True):
            hpath = self._hpath()
            self._clean_stale_tmp(hpath)
            return self._rewrite_unlocked(hpath, keep, drop_audio)

    def delete(self, ts: float, drop_audio: bool = True) -> int:
        """Remove the entry with this timestamp (and its audio)."""
        with self._locked(exclusive=True):
            hpath = self._hpath()
            self._clean_stale_tmp(hpath)
            return self._rewrite_unlocked(
                hpath, lambda e: abs(e.get("ts", 0) - ts) > 1e-6, drop_audio)

    def update_text(self, ts: float, text: str) -> bool:
        """Rewrite the text of the entry with this timestamp (inline repair,
        research §4: correction must be one step away). Returns whether a
        matching entry was found; audio retention is untouched. One LOCK_EX
        transaction: a concurrent append can no longer be lost to the edit.
        """
        with self._locked(exclusive=True):
            hpath = self._hpath()
            self._clean_stale_tmp(hpath)
            changed = False
            lines = []
            for entry in self._read_all_unlocked():
                if not changed and abs(entry.get("ts", 0) - ts) < 1e-6:
                    old = entry.get("text")
                    if old != text and "edited_from" not in entry:
                        # first edit wins: keep what ASR heard (the audit trail);
                        # the diff against the final text is the dictionary
                        # learner's signal (processing/dict_learn.py)
                        entry["edited_from"] = old
                    entry["text"] = text
                    changed = True
                lines.append(json.dumps(entry, ensure_ascii=False))
            if not changed:
                return False
            _atomic_write(hpath, lines)
            return True

    def clear(self, drop_audio: bool = True) -> int:
        """Remove every entry (and retained audio). Returns the count removed."""
        with self._locked(exclusive=True):
            hpath = self._hpath()
            adir = paths.audio_dir()
            self._clean_stale_tmp(hpath, adir)
            removed = self._rewrite_unlocked(hpath, lambda e: False,
                                             drop_audio=False)
            if drop_audio and adir.exists():
                for f in adir.glob("*.wav"):
                    f.unlink(missing_ok=True)
            return removed

    def prune_orphan_audio(self) -> int:
        """Remove managed `*.wav` files no history row references (one
        LOCK_EX transaction). Returns the number pruned."""
        with self._locked(exclusive=True):
            self._clean_stale_tmp(self._hpath(), paths.audio_dir())
            return self._prune_orphan_audio_unlocked()

    def export_zip(self, path: Path,
                   on_note: Callable[[str], None] | None = None) -> int:
        """Zip history + retained audio. Returns the number of entries
        exported. The entries read and audio gather share one LOCK_SH, so
        no rewrite can delete audio mid-export.

        Every entry lands in `history.jsonl`; audio is included only when
        it resolves inside paths.audio_dir() and exists. Skipped/refused
        audio is reported through `on_note`, never raised; OSError from
        the zip write itself propagates to the caller.
        """
        note = on_note or (lambda m: None)
        with self._locked(exclusive=False):
            entries = self._read_all_unlocked()
            adir = paths.audio_dir().resolve()
            seen: set[str] = set()
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                with zf.open("history.jsonl", "w") as fh, \
                        io.TextIOWrapper(fh, encoding="utf-8") as out:
                    for entry in entries:
                        out.write(json.dumps(entry, ensure_ascii=False)
                                  + "\n")
                for entry in entries:
                    audio = entry.get("audio")
                    if not audio:
                        continue
                    p = Path(audio)
                    try:
                        inside = p.resolve().is_relative_to(adir)
                    except OSError:  # unresolvable path: treat as outside
                        inside = False
                    if not inside:
                        note(f"refused audio outside audio dir: {audio}")
                        continue
                    if not p.is_file():
                        note(f"skipped missing audio: {audio}")
                        continue
                    arcname = f"audio/{p.name}"
                    if arcname in seen:
                        continue
                    seen.add(arcname)
                    zf.write(p, arcname=arcname)
            return len(entries)

    def scrub_test_entries(self, *, apply: bool = False
                           ) -> tuple[int, int, Path | None]:
        """Remove test-fingerprint rows from history.jsonl.

        Dry-run by default (zero writes). With apply=True and at least one
        match, writes a backup copy beside the file FIRST
        (history.jsonl.bak-<ts>), then atomically rewrites it with only
        the kept rows (order preserved, same JSONL serialization as the
        rewrite transaction). Returns (removed, total, backup_path);
        backup_path is None unless a backup was written. A missing file is
        (0, 0, None). Command rows carry no audio, so audio is never
        touched; the MAX_ENTRIES cap logic is not involved.
        """
        if not apply:  # dry run: read-only
            with self._locked(exclusive=False):
                if not self._hpath().exists():
                    return (0, 0, None)
                entries = self._read_all_unlocked()
                removed = sum(1 for e in entries if is_test_entry(e))
                return (removed, len(entries), None)
        with self._locked(exclusive=True):
            hpath = self._hpath()
            if not hpath.exists():
                return (0, 0, None)
            self._clean_stale_tmp(hpath)
            entries = self._read_all_unlocked()
            kept_lines: list[str] = []
            removed = 0
            for entry in entries:
                if is_test_entry(entry):
                    removed += 1
                else:
                    kept_lines.append(json.dumps(entry, ensure_ascii=False))
            if removed == 0:
                return (0, len(entries), None)
            backup = hpath.with_name(
                hpath.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(hpath, backup)
            _atomic_write(hpath, kept_lines)
            return (removed, len(entries), backup)


# -- compatibility wrappers -------------------------------------------------------
#
# The pre-P0.3 module-level API, unchanged in shape: every function
# delegates to one default HistoryStore that resolves its paths through
# fluidvoice.paths at call time. No caller changes needed.

_store = HistoryStore()


def append(entry: dict, audio_src: Path | None = None, keep_audio: bool = False,
           budget_gb: float = 4.0) -> None:
    _store.append(entry, audio_src=audio_src, keep_audio=keep_audio,
                  budget_gb=budget_gb)


def tail(n: int = 20) -> list[dict]:
    return _store.tail(n)


def read_all() -> list[dict]:
    return _store.read_all()


def search(query: str = "", limit: int = 100) -> list[dict]:
    return _store.search(query, limit)


def audio_path_for(ts: float) -> Path | None:
    return _store.audio_path_for(ts)


def delete(ts: float, drop_audio: bool = True) -> int:
    return _store.delete(ts, drop_audio)


def update_text(ts: float, text: str) -> bool:
    return _store.update_text(ts, text)


def clear(drop_audio: bool = True) -> int:
    return _store.clear(drop_audio)


def prune_orphan_audio() -> int:
    """P0.3: drop retained audio no row references (maintenance path)."""
    return _store.prune_orphan_audio()


def export_zip(path: Path, on_note: Callable[[str], None] | None = None) -> int:
    return _store.export_zip(path, on_note=on_note)


def scrub_test_entries(*, apply: bool = False) -> tuple[int, int, Path | None]:
    return _store.scrub_test_entries(apply=apply)


def _rewrite(keep, drop_audio: bool) -> int:
    """Pre-P0.3 internal entry point, kept as a locked transaction."""
    return _store.rewrite(keep, drop_audio)


def _enforce_entry_cap(hpath: Path) -> None:
    with HistoryStore(hpath)._locked(exclusive=True):
        _enforce_entry_cap_unlocked(hpath)


# -- test-row scrub -------------------------------------------------------------

# The exact command strings the suite's command-mode tests wrote into live
# history before tests/conftest.py isolated the XDG dirs (768 rows measured
# 2026-09-04 on the daily-driver machine: "true 1"/"true 2"/"exit 3"/
# "echo hi" x192 each, from tests/test_command.py's default-appender
# tests). Exact set membership ONLY — never a pattern: a near-miss like
# "true 1 && rm -rf /" is a real command and must be kept.
TEST_COMMANDS = frozenset({"true 1", "true 2", "exit 3", "echo hi"})


def is_test_entry(entry: dict) -> bool:
    """Command-mode row whose command string is one of the literal test
    commands (see TEST_COMMANDS)."""
    return (entry.get("mode") == "command"
            and entry.get("command") in TEST_COMMANDS)


def count_test_entries(entries: list[dict] | None = None) -> int:
    """Rows matching the test fingerprint (whole file when entries is None)."""
    if entries is None:
        entries = read_all()
    return sum(1 for e in entries if is_test_entry(e))


def test_command_counts(entries: list[dict] | None = None) -> dict[str, int]:
    """Per-command counts of test-fingerprint rows, for the dry-run report
    (the operator vetoes each command string before applying)."""
    if entries is None:
        entries = read_all()
    out: dict[str, int] = {}
    for e in entries:
        if is_test_entry(e):
            out[e["command"]] = out.get(e["command"], 0) + 1
    return out


# -- today-usage stats ---------------------------------------------------------

def today_stats(entries: list[dict], now: float | None = None) -> dict:
    """Dictations/seconds/words since local midnight over `entries`."""
    now = time.time() if now is None else now
    # isdst=-1 lets mktime pick the right DST offset for local midnight
    midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    today = [e for e in entries if e.get("ts", 0) >= midnight]
    seconds = sum(float(e.get("duration_s") or 0) for e in today)
    words = sum(len(str(e.get("text") or e.get("raw") or "").split())
                for e in today)
    return {"dictations": len(today), "seconds": float(seconds), "words": words}


def format_today(stats: dict) -> str:
    """`"N dictations, M:SS minutes, K words"` (shared by CLI + GTK)."""
    total = int(stats.get("seconds", 0))  # truncate, never round up
    return (f"{stats.get('dictations', 0)} dictations, "
            f"{total // 60}:{total % 60:02d} minutes, "
            f"{stats.get('words', 0)} words")


# -- usage stats (Stats page, upstream StatsView parity) ------------------------

# Time-saved basis (upstream "Time Saved (user WPM)"): typing at 40 wpm vs
# dictating at 150 wpm - the difference is time saved per word.
TYPING_WPM = 40.0
DICTATION_WPM = 150.0


def _entry_words(entry: dict) -> int:
    return len(str(entry.get("text") or entry.get("raw") or "").split())


def usage_stats(entries: list[dict], now: float | None = None) -> dict:
    """Whole-history usage summary for the Stats page: today block, day
    streak (consecutive local days with at least one entry, today counts
    only when active), all-time totals, time-saved estimate, and the
    per-day activity series."""
    now = time.time() if now is None else now
    today = today_stats(entries, now)

    by_day: dict[str, dict] = {}
    for e in entries:
        day = time.strftime("%Y-%m-%d", time.localtime(e.get("ts", 0)))
        agg = by_day.setdefault(day, {"dictations": 0, "words": 0,
                                      "seconds": 0.0})
        agg["dictations"] += 1
        agg["words"] += _entry_words(e)
        agg["seconds"] += float(e.get("duration_s") or 0)

    # streak: walk back from today; a day with nothing breaks it, but an
    # empty TODAY does not (the day is not over yet) - start at yesterday.
    days = set(by_day)
    cursor = time.localtime(now)
    streak = 0
    if time.strftime("%Y-%m-%d", cursor) in days:
        streak = 1
    while True:
        cursor = time.localtime(time.mktime(cursor[:3] + (0, 0, 0, 0, 0, -1))
                                - 86400)
        key = time.strftime("%Y-%m-%d", cursor)
        if key in days:
            streak += 1
        else:
            break
    best = run = 0
    prev: datetime | None = None
    for day in sorted(days):
        d = datetime.strptime(day, "%Y-%m-%d")
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d

    total_words = sum(a["words"] for a in by_day.values())
    total_seconds = sum(a["seconds"] for a in by_day.values())
    n = len(entries)
    minutes_saved = total_words * (1.0 / TYPING_WPM - 1.0 / DICTATION_WPM)
    return {
        "today": today,
        "streak": streak,
        "best_streak": best,
        "dictations": n,
        "words": total_words,
        "seconds": total_seconds,
        "avg_seconds": total_seconds / n if n else 0.0,
        "minutes_saved": minutes_saved,
        "by_day": by_day,
    }
