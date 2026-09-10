# ADR-0004: The deb is an Ubuntu 24.04 / x86_64 / Python 3.12 contract

Date: 2026-09-10
Status: Accepted

## Context

The published deb embedded a Python 3.12 venv (its `python` symlinks
the build machine's interpreter, so the build host's minor version
becomes the package's) while declaring `Depends: python3 (>= 3.11)` —
an install on 3.11 could not work, and the old `-bin` AUR recipe
repackaged that same host-dependent venv. Five releases through v0.8.1
shipped without a CI run, so nothing caught it. The improvement program
(P0.6 "Correct packaging and documentation") required an honest
compatibility statement and reproducible artifacts.

## Decision (shipped in 305bb44, 26953e8, acf87e2)

- The deb is **single-target**: Ubuntu 24.04 (close derivatives
  included), x86_64/amd64, CPython 3.12 — declared honestly via
  `Depends: python3 (>= 3.12), python3 (<< 3.13)` so incompatible
  minors fail at install time through package dependencies.
- `packaging/build-deb.sh` hard-checks Linux / CPython 3.12 / amd64 and
  refuses to build anywhere else.
- Release artifacts are built only in the pinned `ubuntu:24.04`
  container (`packaging/deb/Dockerfile`) against a committed, reviewed
  dependency lock (`packaging/deb/constraints.txt`, regenerable with
  `update-constraints.sh`) — no hidden `pip freeze` at build time.
- Other distributions are served by **pipx** (Python 3.11+, any distro;
  `scripts/verify-pipx.sh`) and the **native AUR package**
  (`packaging/aur/`, PEP 517 source build against Arch's current
  Python; main speech dependency available as `python-faster-whisper`)
  — instructions only, not published by this project.
- The README leads install docs with this contract.

## Consequences

- The package metadata matches reality; the 3.11-on-Ubuntu-24.10-style
  failures are impossible to reach by install.
- Building a release deb requires the pinned container (a Docker step
  the release workflow owns); local ad-hoc builds outside it fail fast.
- The lock file must be reviewed/regenerated on dependency bumps — a
  small standing cost for reproducible artifacts.
- New Ubuntu LTS or ARM support means a new, deliberate contract
  (container + lock + Depends), not an implicit claim.
