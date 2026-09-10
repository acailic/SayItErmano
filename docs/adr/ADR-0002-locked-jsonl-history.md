# ADR-0002: History stays JSONL, made transactional with a sidecar flock

Date: 2026-09-10
Status: Accepted

## Context

`history.jsonl` is the product's memory: stats, dictionary
auto-learning, the History window, and the `history` socket/MCP routes
all read it, while the daemon appends and the GTK app edits/trims —
from different processes. The 2026-09-10 baseline had a confirmed
defect: a concurrent history edit could overwrite and lose a daemon
append (read-modify-write races, no locking). The improvement program
(P0.3) weighed keeping JSONL with real transactions against migrating
to SQLite.

## Decision

Keep JSONL; make every mutation transactional (shipped in 238a85f):

- A deep `HistoryStore` interface; the old module-level functions stay
  as compatibility wrappers with unchanged signatures and the unchanged
  row schema and timestamp-keyed callers.
- A **sidecar `flock`** (`<history>.lock`, never the JSONL fd):
  `LOCK_SH` for reads, `LOCK_EX` spanning each complete append or
  read-modify-replace transaction — across threads *and* processes.
- Rewrites write a unique same-directory temp file, flush + `fsync`,
  `os.replace` atomically, then fsync the directory. A crash leaves at
  most a stale `*.tmp`, which the next exclusive transaction removes.
- Retained audio gets collision-proof names, is rolled back if the
  history append fails, and orphans are pruned under the same lock.
- The 5000-entry cap and the tail read (never loading the whole file)
  are preserved.

No SQLite migration: users keep a plain-text, greppable, easily backed
up file in the same location and shape; the plan's Public Interfaces
section promises "History remains JSONL in the same location and shape;
locking requires no user migration."

## Consequences

- Append-vs-edit and append-vs-trim races are closed; two processes
  (daemon + GTK) interoperate safely (test-enforced).
- Crash residue is bounded and self-healing; audio can no longer be
  orphaned by a failed append.
- Complexity stays inside `fluidvoice/history.py`; callers were not
  touched in the P0 release, keeping the diff auditable.
- Future schema work still means JSONL evolution (additive keys, as
  `edited_from` and `confidence_band` already do), not relational
  queries — accepted for a single-user desktop store at 5000 rows.
