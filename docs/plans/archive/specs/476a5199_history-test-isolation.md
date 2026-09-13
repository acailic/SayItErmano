# Plan: History integrity — test isolation, scrub tooling, doctor line

Session `476a5199`. Every phase ends with
`.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.

## Measured ground truth (verified on this machine, repo @ 23e2567)

- `~/.local/share/sayit-ermano/history.jsonl`: **782 lines — 768 are
  command-mode TEST rows, 14 are real dictations.** Breakdown: `"true 1"` ×192,
  `"true 2"` ×192, `"exit 3"` ×192, `"echo hi"` ×192 (the purpose-`"p"` rows
  the prompt cites come from `"echo hi"` — see deviation note below).
- Root cause: `fluidvoice/command.py` `CommandSession._write_history` (line
  ~319) falls through to `history_mod.append(entry)` when no
  `history_appender` is injected and `cfg["history"]["save"]` is truthy
  (DEFAULTS → true). Three tests in `tests/test_command.py` build real
  `CommandSession`s with `cfg = deepcopy(DEFAULTS)` and never request
  `quiet_ui` (the only fixture that redirects `history_file`):
  - `test_turn_bound` → writes `"true 1"`, `"true 2"`
  - `test_failure_feeds_back` → writes `"exit 3"` (purpose `"fail"`)
  - `test_messages_shape` → writes `"echo hi"` (purpose `"p"`)
  4 rows per suite run × ~192 runs = current pollution.
- **Deviation from prompt, resolved:** the prompt's fingerprint
  `{"true 1","true 2","exit 3"}` predates the `"echo hi"` rows, yet the
  prompt's own purpose-`"p"` description implicates them (`test_messages_shape`).
  Today ALL 768 command rows are test rows and the 14 dictation rows are real.
  The scrub constant is therefore the exact set
  `{"true 1", "true 2", "exit 3", "echo hi"}`. The dry-run prints a
  per-command breakdown so the operator can veto `"echo hi"` before applying
  (no real command-mode `echo hi` exists in the file — all 192 carry purpose
  `"p"` and `duration_ms` 0–1).
- **Deviation from prompt premise:** `tests/conftest.py` does NOT currently
  isolate `XDG_CONFIG_HOME` — it is only a Pillow warm-up. Only
  `tests/integration/conftest.py` isolates env (function-scoped, per test).
  Also `paths.dictionary_suggestions_file()` resolves under **`config_dir()`**,
  not the data root. So session isolation must cover BOTH `XDG_DATA_HOME` and
  `XDG_CONFIG_HOME` (plus `XDG_CACHE_HOME` as free hardening).
- `fluidvoice/paths.py` reads env lazily on every call (`_app_dir` →
  `os.environ.get`) and nothing in the package caches a path at import —
  env vars set at conftest import time are a complete seam. **No change to
  `fluidvoice/paths.py` is needed** (prompt's preference: env over new code).

## Files

| File | Change |
|---|---|
| `tests/conftest.py` | session XDG isolation + real-path snapshot + session guard fixture |
| `tests/test_conftest_isolation.py` | NEW — regression guard tests |
| `fluidvoice/history.py` | `TEST_COMMANDS`, `is_test_entry`, `count_test_entries`, `scrub_test_entries` |
| `fluidvoice/cli.py` | `history --scrub-tests [--yes]` |
| `fluidvoice/doctor.py` | `_history_lines()` replacing the bare `history:` line |
| `tests/test_history.py` | `TestScrubTestEntries` (helper + CLI level) |
| `tests/test_infra.py` | `TestDoctorHistoryLines` |

Untouched: `fluidvoice/paths.py`, `fluidvoice/command.py`, `fluidvoice/daemon.py`,
`tests/integration/conftest.py`, all History-window GTK code, cap logic,
schema.

---

## Phase 1 — Session-scoped XDG isolation (`tests/conftest.py`)

Add to `tests/conftest.py` (after the existing Pillow warm-up), at **module
import time** — pytest imports conftest before any test module, and env is
inherited by any subprocess a test spawns:

```python
import atexit, hashlib, os, shutil, tempfile
from pathlib import Path

from fluidvoice import paths as _paths

# 1) Snapshot the REAL resolved paths BEFORE overriding env (module import
#    happens before the override below, so these are the production paths).
REAL_HISTORY_FILE = _paths.history_file()
REAL_SUGGESTIONS_FILE = _paths.dictionary_suggestions_file()
REAL_CONFIG_FILE = _paths.config_file()

# 2) Session XDG root: every paths.py resolution now lands under tmp.
TEST_XDG_ROOT = Path(tempfile.mkdtemp(prefix="sayit-test-xdg-"))
os.environ["XDG_DATA_HOME"] = str(TEST_XDG_ROOT / "data")
os.environ["XDG_CONFIG_HOME"] = str(TEST_XDG_ROOT / "config")
os.environ["XDG_CACHE_HOME"] = str(TEST_XDG_ROOT / "cache")
atexit.register(shutil.rmtree, TEST_XDG_ROOT, ignore_errors=True)
```

Why import-time `os.environ` and not a session fixture: session fixtures run
at first test, after test-module import; import-time covers module-level
path use and subprocess env inheritance, and cannot be skipped by fixture
ordering. Hard `os.environ[...] =` (not `setdefault`) — a leaked outer XDG
var must still be overridden. Existing per-test
`monkeypatch.setenv`/`setattr` overrides keep winning (they run later).

3) Session guard fixture in the same file:

```python
def _fingerprint(p: Path):
    try:
        data = p.read_bytes()
    except OSError:
        return None
    return (p.stat().st_mtime_ns, p.stat().st_size,
            hashlib.sha256(data).hexdigest())

@pytest.fixture(scope="session", autouse=True)
def _real_data_untouched():
    before = {name: _fingerprint(p) for name, p in {
        "history": REAL_HISTORY_FILE,
        "suggestions": REAL_SUGGESTIONS_FILE,
        "config": REAL_CONFIG_FILE}.items()}
    yield
    after = {name: _fingerprint(p) for name, p in ...same...}
    for name in before:
        assert after[name] == before[name], (
            f"suite wrote to real {name} file: {before[name]} -> {after[name]}")
```

Hash catches size-preserving rewrites (e.g. a cap-trim); mtime_ns/size catch
appends. `None` (missing file, e.g. CI) must stay `None` — the guard then
asserts non-creation. A failure raises in session teardown → reported as a
session error; that is the intended tripwire semantics — document in a
comment. (This fixture plus the Phase 2 module is scope item 2's guard.)

4) Routing audit (scope item 1) — outcome, no code changes needed:
- Patch-their-own-tmp already: `test_history_audio.py`,
  `test_history_export_stats.py`, `test_infra.py`, `test_daemon.py`
  (`quiet_ui` + line 773), `test_gtkui_client.py`, `test_hotkey_grab.py`,
  `test_daemon_lock.py` (patches `cli.paths.config_dir`),
  `test_config_settings.py` (patches `paths.config_file`),
  `test_model_catalog.py` / `test_infra.py` / `test_backends_selection.py` /
  `test_parakeet_onnx.py` (patch `XDG_CACHE_HOME`).
- Default-appender writers: the three `test_command.py` tests above —
  **leave them unmodified**; they are the canary the guard watches, and under
  isolation their writes land in the session tmp dir.
- No test constructs the real data path literally (`~/.local/share` /
  `Path.home()`) and none unsets the XDG vars (verified by grep).
- `tests/integration/conftest.py` keeps its function-scoped env — untouched.

Gate: full suite green; after the run, `sayit-test-xdg-*` tmp root contains
`data/sayit-ermano/history.jsonl` with the test rows (positive signal), and
the session guard did not fire. Watch for surprises: `Daemon` onboarding
marker (`paths.data_dir() / ".onboarded"`) now resolves under tmp — no unit
test depends on the real marker (daemon tests construct `Daemon` directly).

## Phase 2 — Regression guard module (`tests/test_conftest_isolation.py`, NEW)

`tests/` is a package (`__init__.py` exists), so import conftest constants
directly: `from tests.conftest import REAL_HISTORY_FILE, TEST_XDG_ROOT`.

- `test_env_points_into_session_tmp` — `XDG_DATA_HOME` starts with
  `TEST_XDG_ROOT`; `paths.history_file()`, `paths.audio_dir()` resolve under
  it; `paths.dictionary_suggestions_file()` under the tmp config root;
  `paths.config_file()` likewise (not the real `~/.config` path).
- `test_real_paths_captured_before_override` — `REAL_HISTORY_FILE` is NOT
  under `TEST_XDG_ROOT` (sanity that the snapshot ran pre-override).
- `test_append_lands_in_tmp` — `history.append({...})` via default paths
  creates a file under `TEST_XDG_ROOT` only; `REAL_HISTORY_FILE`
  fingerprint unchanged.
- `test_command_session_default_appender_isolated` — the would-have-caught-it
  test: build `CommandSession` with `StubAIClient`-style stub replies
  (`'{"command": "true 1", "purpose": "p", "done": false}'` then done),
  `start()` + `confirm()`; assert the entry exists in the session-tmp
  history and `REAL_HISTORY_FILE` fingerprint is unchanged. (Before Phase 1
  this test writes 2 rows to production and the fingerprint assert fails.)

Gate: suite green including the new module.

## Phase 3 — Scrub: `history.py` helper + `cli.py` flag (+ tests)

`fluidvoice/history.py`:

```python
TEST_COMMANDS = frozenset({"true 1", "true 2", "exit 3", "echo hi"})

def is_test_entry(entry: dict) -> bool:
    """Exact fingerprint: command-mode rows whose command string is one of
    the literal test commands. Exact set membership only — never a pattern
    that could match real commands."""
    return entry.get("mode") == "command" and entry.get("command") in TEST_COMMANDS

def count_test_entries(entries: list[dict] | None = None) -> int:
    return sum(1 for e in (entries if entries is not None else read_all())
               if is_test_entry(e))

def scrub_test_entries(*, apply: bool = False) -> tuple[int, int, Path | None]:
    """Filter test-fingerprint rows. Dry-run by default; returns
    (removed, total, backup_path). Reuses _atomic_write; never touches
    audio (command rows carry none)."""
```

Semantics: `read_all()`; split kept/removed; dry-run → counts only, zero
writes. `apply` with `removed == 0` → no backup, no rewrite (mtime
unchanged). `apply` with `removed > 0` → `shutil.copy2` the file to
`history.jsonl.bak-<YYYYmmdd-HHMMSS>` **beside it before mutation**, then
`_atomic_write(hpath, kept_lines)` with `json.dumps(..., ensure_ascii=False)`
exactly like `_rewrite`. Missing file → `(0, 0, None)`. Do not touch
`MAX_ENTRIES`/cap logic.

`fluidvoice/cli.py` — history parser (currently `-n`, `--export`) gains:

```python
p.add_argument("--scrub-tests", action="store_true",
               help="remove test-suite fingerprint rows (dry-run by default)")
p.add_argument("--yes", action="store_true",
               help="with --scrub-tests: apply (writes a .bak-<ts> backup first)")
```

Handler order in the `history` branch: `--export` first (unchanged), then
`--scrub-tests` (return 0 after handling), then the tail listing. Dry-run
output must include the per-command breakdown
(`true 1: 192, true 2: 192, exit 3: 192, echo hi: 192`) plus
`would remove N of M entries — run with --yes to apply`. With `--yes`:
apply, print `removed N entries (kept M), backup: <path>`. Exit code 0 in
all scrub outcomes (maintenance info, not an error).

`tests/test_history.py` — new `TestScrubTestEntries` (monkeypatch
`paths.history_file` to tmp like the existing classes):
- dry-run: counts correct, file bytes identical afterwards;
- apply: exactly fingerprint rows removed, remaining lines byte-identical
  and order-preserved; backup file exists beside it, name matches
  `history.jsonl.bak-*`, content == pre-scrub content;
- no-match negative: file without test rows → `(0, n, None)`, mtime AND
  content unchanged, no backup;
- exact-match safety: `mode != "command"` entry whose `command` field
  happens to equal `"true 1"` is kept; near-miss commands (`"true 1x"`,
  `"true 1 && rm -rf /"`, `"Exit 3"`) are kept;
- missing file → zeros;
- CLI level (follow `test_transcribe_cli.py`'s `cli.main([...])` + capsys
  pattern): `["history", "--scrub-tests"]` dry-run text + no write;
  `[..., "--yes"]` applies + prints backup path; `--yes` alone still lists
  history (no scrub).

Gate: suite green.

## Phase 4 — Doctor history sanity line (`fluidvoice/doctor.py` + tests)

Add `_history_lines() -> list[str]` (lazy `paths.history_file()`,
`read_all()` for oldest ts, `count_test_entries()` for the warning) and
replace the current `print(f"history: {paths.history_file()}")` in `run()`
with its output:

```
history: /home/USER/.local/share/sayit-ermano/history.jsonl
  entries: 14 (159.5 KB), oldest: 2026-08-30 12:01, test rows: 0
```

Missing file → `  entries: 0 (no history yet), test rows: 0`. `test rows > 0`
→ extra line:
`  WARNING: N test-fingerprint rows present — run `sayit-ermano history --scrub-tests``.
Import the counter from `history` (no fingerprint duplication). Entry count
and oldest from `read_all()` (oldest first already); entry with no `ts` →
skip for the date. Keep `run()`'s structure and exit-code logic untouched.

`tests/test_infra.py` — new `TestDoctorHistoryLines` (patch
`paths.history_file` to tmp like `TestDoctorSuggestionsLine` does): seeded
history → counts/size/oldest/warning line present; zero test rows → no
`WARNING`; missing file → the no-history line. Existing
`test_run_prints_section` (substring assert) stays green.

Gate: suite green.

## Phase 5 — Manual runbook on this machine (post-merge)

1. `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — green,
   session guard silent (this IS the decoy proof: the real file's
   mtime/size/sha256 are asserted unchanged across a full append-heavy run).
2. `sayit-ermano history --scrub-tests` (dry-run) — expect
   `would remove 768 of 782`, breakdown `true 1: 192, true 2: 192,
   exit 3: 192, echo hi: 192`. If the file changed since measurement,
   re-derive: today every `mode == "command"` row is a test row; if an
   unfamiliar command string appears in the breakdown, STOP and re-evaluate
   the constant instead of applying.
3. `sayit-ermano history --scrub-tests --yes` — verify the
   `history.jsonl.bak-<ts>` backup appears beside the file.
4. `sayit-ermano status` — today-line now reflects only real dictations.
   `sayit-ermano doctor` — history line reads `entries: 14 …, test rows: 0`.
   History window and `history --export` ZIP contain only real rows.
5. Delete the `.bak` after eyeballing (optional).

## Done means

- Every phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.
- The session guard + `test_conftest_isolation.py` prove zero writes outside
  tmp on every run (real history/config/suggestions fingerprints unchanged).
- Dry-run on the live file removes exactly the 768 test rows (per-command
  breakdown verified), 0 others; `--yes` writes the backup first.
- Post-scrub: `status` today-line real-only, doctor reports `test rows: 0`.

## Out of scope (restated)

- No change to what the daemon/CommandSession writes (the leak is the test
  env); no schema changes; no touching the 5000-entry cap; no History-window
  UI work beyond it reading clean data; no per-entry test flags/provenance
  fields; `tests/integration/conftest.py` untouched.

## Commit strategy

One commit per phase, e.g. `test: isolate suite XDG dirs from live data`,
`test: regression guard for real history file`, `history: add --scrub-tests
scrub with dry-run + backup`, `doctor: history sanity line with test-row
warning`.
