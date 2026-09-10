# Post-P0 sweep — 2026-09-10

Fresh-eyes audit of the tree at `abc4e05` ("merge agent/p0-gates"), i.e.
the full P0 reliability program (control server, transactional history,
MCP JSON-RPC, lifecycle gates, packaging) plus repo hygiene, test
quality, and a gap read against the plan's P1/P2/P3 and Test/Release
sections. Every defect below was reproduced on this machine (repro
commands inline) or carries file:line evidence; anything without
reproducible evidence is filed as a note, not a defect.

## Summary

The P0 implementations are solid where their tests look: the control
matrix, history transaction proofs, MCP spec examples, first-PCM timer
lifecycle, and release gates all hold, and the offline suite is green
with zero warnings (1882 passed serial 96 s; `-n 4` xdist 25.8 s, no
flakes across three runs under six-agent CPU load). The defects found
live one corner *past* the tests:

- **4 × P1** — the MCP bridge process dies on a slow-but-legal
  `transcribe_file` (>15 s); history rows containing U+2028/U+2029/U+0085
  are silently dropped from reads and permanently erased by any rewrite;
  a single invalid UTF-8 byte in a >128 KB history file makes every
  subsequent `append()` raise while deleting that take's retained audio;
  one transient `accept()` OSError permanently kills the control server's
  accept thread with the listening socket still bound.
- **7 × P2** — a redundant blind socket unlink in `daemon.shutdown()`
  that defeats the new inode-checked unlink; NaN/Infinity ids make the
  bridge emit invalid JSON; JSON-RPC batch arrays lose their valid
  members; no backpressure on the accept queue plus a per-recv (not
  per-request) idle deadline; "hash-locked" deb constraints are actually
  pin-only; ruff targets py312 while `requires-python` is 3.11+; the new
  P0 modules have zero observability (silent unlocked-fallback included).
- **10 notes** (hygiene/staleness, no reproducible defect), plus a gap
  analysis showing P1 work not yet started (backend seam, config
  registry, eval harness, ADRs/glossary) and what P0 already absorbed.

## Findings table

| ID | Severity | Area | One-line | Evidence |
|----|----------|------|----------|----------|
| F1 | P1 | mcp_server | Bridge process dies on any non-`ControlError` from `control.request` — a `transcribe_file` needing >15 s (client timeout) or a daemon restart mid-call kills the whole bridge | `fluidvoice/mcp_server.py:225,234`, `fluidvoice/control.py:56`; repro below |
| F2 | P1 | history | Rows whose text contains U+2028/U+2029/U+0085 are silently dropped by every reader and permanently erased by the next rewrite | `fluidvoice/history.py:166,256,270` (`splitlines()`) vs writers at `322,350,435,481,537` (`ensure_ascii=False`); repro below |
| F3 | P1 | history | One invalid UTF-8 byte anywhere in a >128 KiB history file makes every `append()` raise `UnicodeDecodeError` (row still lands; audio rollback then deletes its retained audio) | `fluidvoice/history.py:166` strict `read_text("utf-8")` inside the cap path; repro below |
| F4 | P1 | control_server | Any transient `accept()` OSError (EMFILE, ECONNABORTED) permanently exits the accept thread; socket stays bound, control plane silently bricked until restart | `fluidvoice/control_server.py:176-177` (`except OSError: break`); injected-error repro below |
| F5 | P2 | daemon | `shutdown()` blind-unlinks the socket path *after* `ControlServer`'s inode-checked unlink — can delete a replacement daemon's freshly bound socket | `fluidvoice/daemon.py:560` vs `control_server.py:_unlink_owned_path` |
| F6 | P2 | mcp_server | NaN/Infinity request ids are accepted (lenient `json.loads`) and echoed, producing wire output that is not valid JSON | `fluidvoice/mcp_server.py:118-123,245`; repro below |
| F7 | P2 | mcp_server | JSON-RPC batch arrays get one `INVALID_REQUEST` object; valid members' responses never come (JSON-RPC 2.0 §Batch) | `fluidvoice/mcp_server.py:validate()` non-dict branch |
| F8 | P2 | control_server | Accept queue is unbounded and the idle deadline is per-`recv`, not per-request: 8 drip-feeding clients freeze all workers; queued connections hold fds up to EMFILE (→ F4) | `control_server.py:183` (`Queue()` unbounded), `218-241` (per-recv loop) |
| F9 | P2 | packaging | The "hash-locked dependency set" is pin-locked only: 26 `==` pins, zero `--hash` lines; `--require-hashes` never used | `packaging/deb/constraints.txt` (`grep -c -- '--hash'` → 0, the single grep hit is a comment) vs plan P0.6 / `docs/dev/release-gates.md` §5 "hashed constraints" |
| F10 | P2 | gates | Ruff `target-version = "py312"` while `requires-python = ">=3.11"` (pipx path): lint cannot flag 3.12-only syntax breaking 3.11 users | `pyproject.toml` `[tool.ruff]` vs line 10 |
| F11 | P2 | control/history | The new P0 modules never log anything (house `log()` idiom unused): accept-thread death, silent unlocked-write fallback, worker swallows are all invisible | no `log`/`logging` in `control_server.py`, `history.py`, `mcp_server.py`; `history.py:227` `yield  # nothing to protect: proceed unlocked` |

Notes (no reproducible defect — hygiene/staleness): N1 `daemon.shutdown()`
mutates `recording`/`_watchdog`/`_first_pcm_timer` without `self._lock`,
racing the hotkey thread (benign today: callbacks re-validate identity;
`daemon.py:514-523`). N2 `justfile:64` points at `docs/release-gates.md`;
the file is `docs/dev/release-gates.md`. N3 `docs/STATUS.md` header stale
("2026-09-05 · v0.5.0 · 1114 tests" vs v0.8.1 / 1882) and
`docs/UPSTREAM-TRACKING.md` baseline still 2026-09-02 (the 09-08 audit
asked for a refresh). N4 14 of 27 `requests/*.md` lack STATUS headers
(plan P1.5 automation not started). N5 dead compat code with zero
callers: `history._rewrite`, `history._enforce_entry_cap`,
`control._probe_live`. N6 nothing guards the `filterwarnings` gate line
itself — no meta-test or canary proves a deliberate thread exception
fails the suite (the P0.4 commit verified it manually). N7 the MCP stdio
loop has no inbound line bound (the control socket got 1 MiB; the bridge
got nothing). N8 the transaction tests' strict reader
(`tests/test_history_transactions.py:_read_lines`) uses `splitlines()`
too — F2 is invisible to the suite. N9 `_check_first_pcm`/`_cancel_locked`
call `recorder.cancel()` under `self._lock` (pre-existing; a slow process
join briefly blocks control handlers). N10 `append()` mutates the
caller's `entry` dict (`entry["audio"] = ...`) — harmless today, worth
documenting at the seam when P1 refactors it.

## Detailed findings

### F1 (P1) — MCP bridge dies on slow `transcribe_file` or daemon restart

`handle_message` catches only `control.ControlError`
(`mcp_server.py:225`); `serve()`'s loop (`:234`) has no handler at all.
But `control.request` — the bridge's transport — raises plain OSErrors
from its 15 s socket timeout (`control.py:56`) and `ConnectionResetError`
if the daemon restarts mid-call. `transcribe_file` forwards to the
daemon's `transcribe` action, which accepts files up to 200 MB
(`daemon.py:1738` `_API_MAX_BYTES`); any file whose model time exceeds
15 s (minutes of audio on CPU backends) kills the bridge process with a
traceback; the MCP client loses the connection entirely.

Repro (bridge loop with the exact exception `control.request` raises at
15 s):

```python
import io, json, socket
from fluidvoice import mcp_server
def slow_daemon(action, **kw): raise socket.timeout("timed out")
inbox = io.StringIO(json.dumps({"jsonrpc":"2.0","id":1,"method":"tools/call",
    "params":{"name":"transcribe_file","arguments":{"path":"/x.wav"}}})+"\n")
mcp_server.serve(inbox, io.StringIO(), request=slow_daemon)
# -> CRASH: TimeoutError: timed out   (bridge process exits; client hangs)
```

Impact: the flagship MCP tool is unusable for long files and fragile
across daemon restarts; a crashed bridge also strands the client until
its own supervisor restarts it.

Fix (files: `fluidvoice/mcp_server.py`): catch `OSError` alongside
`control.ControlError` in `handle_message` and map to
`DAEMON_UNREACHABLE` (restart case), and either give the bridge's
request path a longer, per-call timeout (e.g. a `timeout=` kwarg on
`control.request` scaled for transcribe) or convert a timeout into an
`isError` tool *result* rather than a protocol error, so one slow file
can't kill the process. Add tests for both (the existing suite covers
only `ControlError`).

### F2 (P1) — U+2028/U+2029/U+0085 rows vanish from history and get erased

Writers serialize with `ensure_ascii=False` (five sites, `history.py`
322/350/435/481/537), which does **not** escape U+2028 (LINE SEPARATOR),
U+2029, or U+0085 (NEL) — Python's `json` leaves them raw. Every reader
splits lines with `str.splitlines()` (`:166,256,270`), which *does*
split on those three characters. A row containing one therefore breaks
into two halves, both unparseable: dropped from `read_all`/`tail`/
`search`/stats/export, and — because rewrites re-serialize only what
parses — **permanently erased** by the next `update_text`/`delete`/cap
trim. ASR never emits these characters, but a user *edit* can: paste
text containing one into the GTK history editor, save, and the row is
gone on the next read.

Repro (real store, real files):

```python
store.append({"ts": 1.0, "text": "hello"})
store.append({"ts": 2.0, "text": "line one\u2028line two"})
store.append({"ts": 3.0, "text": "world"})
len(store.read_all())            # -> 2   (row 2 gone)
store.update_text(3.0, "WORLD")  # rewrite
# file on disk now has 2 lines: the U+2028 row is permanently erased
```

Impact: silent, permanent loss of history entries — exactly the class
P0.3 ("no lost entries") exists to prevent. Pre-existing before P0.3,
but the transactional rewrite replicated it into three new readers and
five writers, and the test suite shares the blind spot (N8).

Fix (files: `fluidvoice/history.py`): split on `"\n"` only —
`data.split("\n")` — at all three reader sites (matches the writer's
separator exactly; `\r` alone never occurs since writers never emit it).
Add a regression test appending a U+2028 row through `append()` and
asserting `read_all`/`tail`/rewrite round-trip it.

### F3 (P1) — one invalid UTF-8 byte >128 KiB breaks every append

`_enforce_entry_cap_unlocked` (`history.py:160`) reads with strict
`hpath.read_text(encoding="utf-8")` while every other reader tolerates
torn bytes via `decode("utf-8", errors="replace")`. Its guard is
`except OSError` — `UnicodeDecodeError` (a `ValueError`) escapes. So one
invalid byte anywhere in a file larger than the 128 KiB tail window
turns **every** `append()` into an exception *after* the row was already
written: the caller sees a failure for a successful write, and the
rollback path deletes the retained audio for that take even though the
row on disk references it (dangling audio path). The existing
corrupt-line tests only exercise small files, so the cap path never
engages.

Repro (real store):

```python
for i in range(600): store.append({"ts": float(i), "text": "x" * 300})
data = hpath.read_bytes()
hpath.write_bytes(data.replace(b'"ts"', b'\xff"ts', 1))  # one bad byte
len(store.read_all())      # -> 599: readers tolerate it
store.append({"ts": 999.0, "text": "new take"})
# -> UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff ...
# row 999.0 landed on disk anyway; its retained audio was rolled back
```

Impact: after any pre-P0 torn write (the scrub tooling exists because
real corruption happened — 768 test rows cleaned off the daily driver on
2026-09-04), a grown history permanently fails appends once it passes
128 KiB, and deletes audio along the way.

Fix (files: `fluidvoice/history.py:160-166`): decode with
`errors="replace"` like the other readers, and broaden the guard to
`(OSError, ValueError)`. Extend `test_history_transactions.py` with a
>128 KiB file containing an invalid byte asserting append succeeds.

### F4 (P1) — one transient `accept()` error permanently kills the accept thread

`_accept_loop` (`control_server.py:176-177`) treats *any* OSError as
"listening socket closed → shutdown" and breaks. But `EMFILE` (fd
exhaustion), `ECONNABORTED`, or `ENOMEM` also land there — and the
thread never recovers: the socket stays bound and listening, new clients
connect into the kernel queue, nobody ever accepts, `status` hangs, and
`probe_live` times out so a *restarting* second instance will judge the
daemon dead and steal the socket path. There is no logging (F11), so the
death is invisible. F8 makes the trigger realistic: every queued
connection holds an fd, so a misbehaving same-user client looping
connects without sending reaches the 1024-fd default limit quickly.

Repro (injected single `ConnectionAbortedError`, delegating wrapper
around the live socket — the thread dies on its next loop iteration and
subsequent clients freeze in the kernel queue):

```
baseline:              replied {"ok": true}
through in-flight call: replied {"ok": true}
accept thread alive after ONE transient accept() OSError: False
new client after error: NO REPLY (accept thread dead, kernel queue frozen)
```

Fix (files: `fluidvoice/control_server.py`): on OSError, check
`self._stopping` — if not stopping, log and `continue` (with a short
sleep for EMFILE/ENOMEM to avoid a hot loop). A `maxsize` on
`_connections` (F8) plus accept-side `settimeout` bounds the queue.

### F5 (P2) — blind socket unlink in `daemon.shutdown()` defeats the inode check

`ControlServer.shutdown()` unlinks its socket only when the path still
points at the file *it* bound (`_unlink_owned_path`, inode check —
deliberate: a replacement daemon may already have rebound the path).
Two statements later, `daemon.py:560` runs
`paths.socket_path().unlink(missing_ok=True)` unconditionally. If a new
daemon instance bound a fresh socket between the two (or rebound during
the server's join window), the old daemon's exit deletes the *new*
daemon's control channel: new connects fail, `probe_live` says dead, and
a third starter steals the path — two daemons, one socket path. Fix:
delete the redundant line (`self._srv.close()` already unlinks safely);
files: `fluidvoice/daemon.py:555-560`.

### F6 (P2) — NaN/Infinity ids produce invalid JSON output

Python's `json.loads` accepts `NaN`/`Infinity` literals by default, and
`_usable_id` (`mcp_server.py:118-123`) happily passes a float NaN through;
`serve`'s `json.dumps` (`:245`) emits it raw:

```python
reply = mcp_server.handle_message(
    json.loads('{"jsonrpc": "2.0", "id": NaN, "method": "ping"}'), ...)
json.dumps(reply)   # '{"jsonrpc": "2.0", "id": NaN, "result": {}}'
```

`NaN` is not valid JSON (RFC 8259); strict client parsers (JavaScript
`JSON.parse`) throw on the reply line. Fix: reject non-finite ids in
`_usable_id` (`math.isfinite`) and/or serialize with
`allow_nan=False` guarded by a fallback envelope.

### F7 (P2) — batch arrays lose their valid members

JSON-RPC 2.0 permits batch requests; the reply must be an array of
per-member responses. `validate()` classifies any non-dict as an invalid
request and the bridge answers one `INVALID_REQUEST` with `id: null` —
so the valid members of `[{...ping...}]` never get replies and a
batching client hangs on their ids. The tests pin this behavior
deliberately (`test_invalid_request_shapes`). MCP 2024-11-05 is
JSON-RPC-based, so a generic JSON-RPC client is within its rights; no
known MCP client batches over stdio today, hence P2 not P1. Cheapest
compliant fix: detect a list, validate members individually, and reply
with an array (empty batch → no output per spec).

### F8 (P2) — no backpressure: unbounded queue, per-recv deadline

`self._connections.put(conn)` never blocks (plain `Queue()`,
`control_server.py:183`), and the 10 s idle timeout is applied per
`recv()` call, not per request (`_read_request_line` loop, `:218-241`).
Consequences: (a) a client dripping one byte every 9 s holds a worker
forever; eight of them freeze the entire control plane — and a frozen
`status` makes `probe_live` report the daemon dead, inviting a second
instance to steal the socket; (b) connections queue without bound, each
holding an fd, until EMFILE kills the accept thread outright (F4).
Mode-0600 limits this to same-user processes (sandbox helpers, buggy
scripts), which is exactly what the socket-steal guard exists for. Fix:
`queue.Queue(maxsize=workers * 4)` with close-on-overflow + an overall
request deadline (monotonic deadline per connection rather than
socket-timeout-per-call).

### F9 (P2) — "hash-locked" constraints are pin-only

Plan P0.6 and `docs/dev/release-gates.md` §5 describe a "reviewed,
hash-locked dependency set"; `packaging/deb/constraints.txt` has 26 exact
`==` pins and zero `--hash` lines (the only grep hit is the comment
explaining the format). Exact pins lock *versions* but not *artifacts*:
a compromised or yanked-and-replaced PyPI artifact under the same
version string installs silently into the release deb. Fix: regenerate
with `pip freeze --hashes`-style hashes (or `pip-compile --generate-hashes`)
and build with `pip install --require-hashes -c constraints.txt .` in
`packaging/build-deb.sh`; the freshness check in the workflows would
still pass as-is.

### F10 (P2) — ruff py312 vs requires-python 3.11

`pyproject.toml` declares `requires-python = ">=3.11"` (the pipx
contract, P0.6: "pipx on Python 3.11–3.13") but `[tool.ruff]
target-version = "py312"`. Ruff will not flag syntax that requires 3.12
(e.g. PEP 695 `type` statements, f-string grammar changes) even though
those break 3.11 users. Fix: `target-version = "py311"` (matches the
oldest supported interpreter); the CI matrix doesn't build 3.11, so lint
is currently the only automated 3.11 guard.

### F11 (P2) — zero observability in the new modules

The house idiom is `log()` (`pipeline.py:27`, stderr, timestamps);
`control_server.py`, `history.py`, and `mcp_server.py` call no logging at
all. Concrete silent paths: the unlocked-fallback in `_locked()`
(`history.py:227` — on EROFS/EACCES an *exclusive* transaction proceeds
without the lock, i.e. without the very lost-update guarantee P0.3
promises, and nobody learns); accept-thread death (F4); the worker
swallow at `control_server.py:194`; handler exceptions flattened to
`{"ok": false}` with no daemon-side trace. Fix: import the `log()` helper
(or `logging`) and emit one line on each of those paths — F4's window
shrinks from "silent brick" to "one log line".

## Test quality (runs on this machine, six agents active)

- Serial gate: `pytest -q tests --ignore=tests/integration -W error` →
  **1882 passed, 0 warnings, 96 s** — matches the abc4e05 baseline.
- Collection sanity: 1882 collected (`--collect-only -q`).
- xdist: `-n 4` → 1882 passed in 25.8 s; repeat runs of the four P0 test
  files (`test_control_server`, `test_history_transactions`,
  `test_mcp_server`, `test_first_pcm_lifecycle`, 77 tests) green twice
  more. **No order or parallelism flakes found.** (`pytest-randomly` is
  not installed, so `-p no:randomly` is a no-op.)
- `ruff check .` → clean.
- Coverage gaps the defects expose: no test drives a non-`ControlError`
  transport failure through the bridge (F1); the corrupt-row tests stay
  under the 128 KiB cap window (F3); the suite's own strict JSONL reader
  uses `splitlines()` (N8), so F2 cannot fail any test today; no test
  survives a transient accept error (F4); no test asserts the
  `filterwarnings` line exists (N6).

## Already done in P0 (so the next waves don't redo it)

Verified in this tree, not just claimed:

- **Control socket (P0.2)**: 8-worker pool + accept thread; status <250 ms
  behind a 2 s job; one-active-job arbitration preserved; 1 MiB/16 MiB
  limits with structured errors; 0600 mode; deterministic idempotent
  shutdown joining all threads; in-flight request finishes; live-socket
  steal refusal (`probe_live` + `SAYITERMANO_SOCKET`); blank-line and
  non-object handling (`tests/test_control_server.py`, 20 tests).
- **History (P0.3)**: real cross-process `flock` semantics (test proves
  an external LOCK_EX blocks the store); append-vs-edit/trim without
  loss; two-process hammer test; unique same-dir temps + fsync + atomic
  replace + dir fsync; stale-tmp cleanup under LOCK_EX; lock/backup
  files never cleaned; collision-proof audio names; failed-append audio
  rollback; corrupt-line tolerance in readers; export under one shared
  lock (`tests/test_history_transactions.py`, 22 tests). No read-lock
  upgrades exist (reads never upgrade — correct deadlock-avoiding
  design); all renames are same-directory, so no cross-filesystem
  rename assumptions.
- **MCP (P0.4)**: every reply a valid envelope with echoed id; official
  spec invalid-request examples covered; notifications silent; version
  negotiation answers unsupported requests with our latest (per
  2024-11-05); Inspector-style handshake through the stdio loop; daemon
  errors become `isError` results; README security note present.
- **Lifecycle (P0.4)**: first-PCM timer tracked and cancelled on
  stop/cancel/shutdown/restart, identity-checked via captured recorder
  (10 tests); thread-exception gate live (`filterwarnings`), suite green
  with `-W error`; fake-STT server quiets only
  BrokenPipe/ConnectionReset while real handler bugs still print.
- **Gates (P0.5)**: `testpaths`, `just lint/test/test-parallel/gate`,
  ruff `[tool.ruff.lint]` + clean tree, pytest-xdist in dev extra,
  manual-only CI, split `release-prepare`/`release-publish` with
  exact-SHA CI verification, version/constraints/deb-version gates,
  `CHANGELOG.md` with `[Unreleased]` (the publish default notes path
  works).
- **Packaging (P0.6)**: deb contract (Ubuntu 24.04/x86_64/py3.12) with
  pinned base image + contract checks in `build-deb.sh`; native PEP 517
  AUR recipe (replaces the unpublished `-bin`); `.dockerignore` keeps the
  build context lean; README duplicate-`[general]`/hotwords fix landed;
  no-TCP decision canonical in ROADMAP/STATUS.

## Gap analysis vs the plan

- **P1.1 (speech-backend seam)** — not started: no
  `SpeechBackend`/`BackendCapabilities`/`Transcript`/`TranscriptSegment`;
  the five backends remain duck-typed dict producers.
- **P1.2 (daemon decomposition)** — partially pre-served by earlier
  work: `DictationPipeline` lives in `pipeline.py`; the GTK settings
  window is split (`gtkui/settings_pages/`, `settings_window.py`
  2132→517 lines). `daemon.py` is still a 2352-line composition root —
  the SpeechEngineManager/CaptureCoordinator/CommandCoordinator/
  RuntimeTasks split is fully open.
- **P1.3 (configuration registry)** — not started: no `SettingSpec`;
  the parallel structures remain (DEFAULTS/RANGES/ALLOWED/
  _SAVE_WHITELIST/TEMPLATE). The meta-tests from the 09-08 audit (C4)
  hold — the new `first_pcm_timeout`/`stall_timeout_s` keys were
  correctly registered in all structures (verified live).
- **P1.4 (evaluation harness)** — not started (no `eval/` module, no
  metrics tests).
- **P1.5 (project knowledge)** — partially: `CHANGELOG.md` exists and is
  wired into publish; no domain glossary, no ADRs, STATUS-header
  standardization only 13/27 briefs, STATUS.md header badly stale (N3).
- **Test/Release plan section** — control, history, lifecycle, and MCP
  matrices are implemented (with the corner gaps listed under F1–F8);
  packaging gates structurally done but hash-locking (F9), the clean
  Arch chroot AUR build, and the 3.11–3.13 pipx matrix remain unverified
  in CI (Actions are manual-only by policy, so "never ran" is expected —
  but nothing automates them either).
- **Contradiction noticed**: plan P0.5 says "Move Ruff configuration to
  the current `lint` section" (done) but leaves `target-version`
  contradicting `requires-python` (F10); plan P0.6 says "hash-locked"
  while the shipped artifact is pin-only (F9).

## Opportunities (small, high-leverage)

1. **One-line-per-site F2 fix** (`split("\n")` in three readers) plus a
   regression test — removes a silent-permanent-data-loss class for
   ~15 minutes of work; also fix N8 so the suite could have caught it.
2. **Tolerant decode + broadened except in the cap path** (F3) — two
   lines, converts "every append fails forever" into "trim skips".
3. **`OSError` handling around tool calls in the MCP bridge** (F1) and a
   per-call timeout for transcribe — makes the bridge survive long files
   and daemon restarts.
4. **Accept-loop resilience + bounded queue** (F4/F8) — ~20 lines
   including logging.
5. **Regenerate constraints with hashes + `--require-hashes`** (F9) —
   one script run plus one flag; closes the plan-vs-artifact gap before
   the first gated release ships.
6. **`target-version = "py311"`** (F10) — one line; the only automated
   3.11 guard available.
7. **Delete the dead compat shims** (N5) and fix the two stale pointers
   (N2, N3) — ten minutes of hygiene.
8. **Guard the gate**: a two-line meta-test asserting `filterwarnings`
   contains the thread-exception filter (N6), so the P0.4 guarantee
   can't silently regress.
