"""Suite-wide process-state guards.

Loaded before any test module is imported (pytest imports conftest first),
which is exactly what the Pillow warm-up below needs — and what the XDG
isolation below needs too: every paths.py resolution must already point
into the session tmp root by the time any test module runs.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


def _warm_pillow_freetype() -> None:
    """Load one Pillow truetype face before GTK/Pango can render text.

    Pillow wheels vendor their own FreeType/HarfBuzz (pillow.libs). If the
    first face load happens AFTER Pango has rendered in this process, every
    later Pillow text measurement returns garbage (negative or huge
    advances) - pill widths collapse and test_overlay renders
    hundred-megapixel canvases, but only when a gtkui test ran first, so it
    reads as an unexplained order dependency. Loading any face (and walking
    one glyph advance) before the first test pins the vendored library into
    a good state for the whole session. No-op when no system font exists
    (headless CI): overlay falls back to load_default there and no gtkui
    test runs without a display anyway.
    """
    try:
        from fluidvoice.overlay import _load_font

        font = _load_font(13, bold=True)
        if font is not None:
            font.getlength("warm")
    except Exception:
        pass  # measurement guards must never break collection


_warm_pillow_freetype()


# ---------------------------------------------------------------------------
# Session XDG isolation — the suite must NEVER write into the live data dir.
# ---------------------------------------------------------------------------
# History of the bug this guards against: command-mode tests built real
# CommandSessions whose default history appender fell through to
# history.append(), and paths.py resolved history.jsonl under
# ~/.local/share/sayit-ermano — 768 test rows polluted the production file
# (~192 suite runs x 4 rows), inflating `status` today-counts, the History
# window, exports and dictionary learning.
#
# paths.py reads the XDG env vars lazily on every call and nothing in the
# package caches a resolved path at import, so setting the env HERE (conftest
# import time, before any test module) is a complete seam — no production
# code change needed. Import-time (not a session fixture) so module-level
# path use in test modules and any spawned subprocess env are covered too.
# Hard assignment, not setdefault: a leaked outer XDG var must still lose.
# Per-test monkeypatch.setenv/monkeypatch.setattr overrides keep winning
# (they run later). tests/integration/conftest.py isolates its own env per
# test on top of this; it is untouched.
from fluidvoice import paths as _paths

# Snapshot the REAL resolved paths BEFORE the override: these are the
# production locations the guard below watches (and what
# tests/test_conftest_isolation.py asserts stay untouched).
REAL_HISTORY_FILE = _paths.history_file()
REAL_SUGGESTIONS_FILE = _paths.dictionary_suggestions_file()
REAL_CONFIG_FILE = _paths.config_file()

TEST_XDG_ROOT = Path(tempfile.mkdtemp(prefix="sayit-test-xdg-"))
os.environ["XDG_DATA_HOME"] = str(TEST_XDG_ROOT / "data")
os.environ["XDG_CONFIG_HOME"] = str(TEST_XDG_ROOT / "config")
os.environ["XDG_CACHE_HOME"] = str(TEST_XDG_ROOT / "cache")
atexit.register(shutil.rmtree, TEST_XDG_ROOT, ignore_errors=True)

# Pin the session type to X11 — but ONLY for the deterministic tiers
# (unit/gtk/dev runs): the Wayland port gates ONLY on this probe
# (fluidvoice/session.py), and the pre-existing tests exercise the
# xdotool/xclip paths — pinning means they take the X11 branch no matter
# what display server the dev machine/CI runner sits on (headless
# runner: "unknown" also behaves as X11, but pinning makes it explicit
# and immune to a runner with a stray WAYLAND_DISPLAY). Per-test
# monkeypatch.setenv("XDG_SESSION_TYPE", "wayland") keeps winning.
#
# The INTEGRATION tier is exempt (quality plan Q2, finding E7): real
# subsystem tests must see the ambient compositor identity — X11 or
# Wayland — unchanged, so the desktop matrices actually exercise the
# session they claim to. scripts/run_test_tier.sh exports
# FLUIDVOICE_TEST_TIER=integration for exactly this switch.
if os.environ.get("FLUIDVOICE_TEST_TIER") != "integration":
    os.environ["XDG_SESSION_TYPE"] = "x11"
    os.environ.pop("WAYLAND_DISPLAY", None)

# Unit-tier network guard (quality plan Q1): under the canonical tier
# runner (FLUIDVOICE_TEST_TIER=unit), any outbound non-loopback socket
# raises before bytes hit the wire. Import-time install covers module
# scope and every thread the suite spawns; loopback fake servers are
# exempt. See tests/_network_guard.py for the full contract.
from tests import _network_guard as _net_guard

_net_guard._install()


def _fingerprint(p: Path):
    """None when the file is missing, else (mtime_ns, size, sha256) —
    catches appends (size/mtime) AND size-preserving rewrites (hash)."""
    try:
        data = p.read_bytes()
    except OSError:
        return None
    return (p.stat().st_mtime_ns, p.stat().st_size,
            hashlib.sha256(data).hexdigest())


_GUARDED_REAL_FILES = {
    "history": REAL_HISTORY_FILE,
    "suggestions": REAL_SUGGESTIONS_FILE,
    "config": REAL_CONFIG_FILE,
}

# Quality plan Q2: an external live daemon dictating during the ~85 s
# suite window changes the real history file from OUTSIDE — the tripwire
# cannot tell that apart from a suite leak by content (same writer
# library, same schema), but it CAN by lineage: every process the suite
# (or its children) owns descends from this interpreter; the production
# daemon does not. Set FLUIDVOICE_TOLERATE_EXTERNAL_HISTORY_WRITES=1 on
# a daily-driver machine to downgrade a *classified-external* change to
# a warning; anything without that classification still fails.
TOLERATE_EXTERNAL_WRITES = "FLUIDVOICE_TOLERATE_EXTERNAL_HISTORY_WRITES"


def _is_descendant(pid: int, ancestor: int, max_hops: int = 32) -> bool:
    """True when `pid`'s parent chain (from /proc) reaches `ancestor`."""
    for _ in range(max_hops):
        if pid == ancestor:
            return True
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                pid = int(fh.read().rsplit(") ", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid <= 1:
            return False
    return False


def _external_daemon_processes() -> list[str]:
    """Live fluidvoice/sayit-ermano daemons NOT owned by this run."""
    me = os.getpid()
    found: list[str] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                cmdline = fh.read().decode("utf-8", "replace").split("\0")
        except OSError:
            continue
        joined = " ".join(p for p in cmdline if p)
        if ("fluidvoice" in joined or "sayit-ermano" in joined) \
                and "daemon" in cmdline:
            if not _is_descendant(int(entry), me):
                found.append(f"pid {entry} ({joined[:70]})")
    return found


def _appended_row_times(path: Path, before_rows: int) -> list[str]:
    """Timestamps of the rows appended after `before_rows` (best effort:
    unreadable/malformed rows count as '?' — this is diagnostics, not a
    parser)."""
    times: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["<unreadable>"]
    for line in lines[before_rows:]:
        try:
            ts = json.loads(line).get("ts")
            times.append(str(ts) if ts is not None else "?")
        except ValueError:
            times.append("?")
    return times or ["<size/mtime change without new rows — rewrite?>"]


def _classify(name: str, path: Path, before_rows: int) -> str | None:
    """Human-readable external-write classification, or None when the
    change looks suite-owned. Only the append-only history file gets the
    row forensics; every guarded file gets the daemon lineage scan."""
    daemons = _external_daemon_processes()
    if not daemons:
        return None
    detail = ""
    if name == "history":
        rows = _appended_row_times(path, before_rows)
        detail = (f"; new rows at ts={rows[:5]}"
                  + (" …" if len(rows) > 5 else ""))
    return ("an external (non-suite) daemon is live: "
            + "; ".join(daemons) + detail
            + " — if those rows are your own dictations, rerun the gate "
              "in a quiet window (or export " + TOLERATE_EXTERNAL_WRITES
            + "=1 on this daily-driver machine)")


@pytest.fixture(scope="session", autouse=True)
def _real_data_untouched():
    """Tripwire: the whole suite must leave the real data/config files
    byte-identical (a missing file must stay missing — non-creation).
    Failing here raises during session teardown, so pytest reports it as a
    session ERROR even when every test passed — intended: a green run that
    mutated production is exactly the failure this exists to catch.

    Q2 addition: when the changed file is explainable by an external
    live daemon (see _classify), the failure message says so, and the
    TOLERATE_EXTERNAL_WRITES env downgrades exactly that classified
    case to a stderr warning. A suite-owned write NEVER gets that
    mercy."""
    before = {name: _fingerprint(p) for name, p in _GUARDED_REAL_FILES.items()}
    before_rows = {name: (len(p.read_text(encoding="utf-8",
                                            errors="replace").splitlines())
                           if p.exists() else 0)
                   for name, p in _GUARDED_REAL_FILES.items()}
    yield
    after = {name: _fingerprint(p) for name, p in _GUARDED_REAL_FILES.items()}
    for name, was in before.items():
        if after[name] == was:
            continue
        path = _GUARDED_REAL_FILES[name]
        verdict = _classify(name, path, before_rows[name])
        if verdict and os.environ.get(TOLERATE_EXTERNAL_WRITES) == "1":
            print(f"tripwire: real {name} file changed, classified as "
                  f"EXTERNAL ({verdict}); tolerated by {TOLERATE_EXTERNAL_WRITES}",
                  file=sys.stderr)
            continue
        message = (f"suite wrote to the real {name} file ({path}): "
                   f"{was} -> {after[name]}")
        if verdict:
            message += f"\n  NOTE: possibly external — {verdict}"
        assert after[name] == was, message


# ---------------------------------------------------------------------------
# Runner hygiene — nothing a test starts may outlive the test.
# ---------------------------------------------------------------------------
# Moved (quality plan Q2, finding E2) into the globally-loaded plugin
# tests/_runner_hygiene.py, registered from the repository-root conftest
# so focused runs and every pytest-xdist worker get the gate too. The
# historical narrative lives with the plugin.
