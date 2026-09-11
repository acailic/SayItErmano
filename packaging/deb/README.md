# deb packaging — Ubuntu 24.04 / x86_64 / Python 3.12 only

The `.deb` produced by [`build-deb.sh`](../build-deb.sh) is a **single-target
package** (plan P0.6, the deb contract):

| Axis | Supported value |
|---|---|
| Distribution | **Ubuntu 24.04** (and its close derivatives, e.g. Pop!_OS 24.04) |
| Architecture | **x86_64 / amd64** |
| Python | **3.12** (declared via `Depends: python3 (>= 3.12), python3 (<< 3.13)`) |

Other distributions and Python versions are served by **pipx**
(Python 3.11+, any distro) or the **native AUR package** on Arch — see the
[README install section](../../README.md#installation). Historically the deb
declared `python3 (>= 3.11)` while bundling a Python 3.12 venv; the contract
above makes the package honest instead.

## Why the build environment is pinned

The deb's runtime is a venv bundled under `/opt/sayit-ermano/venv` whose
`python` **symlinks the system interpreter** — the build machine's Python
minor becomes the deb's Python minor. `build-deb.sh` therefore hard-checks
Linux / CPython 3.12 / amd64 and refuses to build anywhere else, and release
artifacts are built in the pinned container:

```bash
docker build -t sayit-ermano-deb -f packaging/deb/Dockerfile .
mkdir -p dist && docker run --rm -v "$PWD/dist:/out" sayit-ermano-deb
```

The `Dockerfile` pins `ubuntu:24.04` (digest-pinning via
`--build-arg UBUNTU_IMAGE=ubuntu:24.04@sha256:…` is documented inside it).

## The reviewed dependency lock — `constraints.txt`

`pip install .` inside the build resolves dependencies; without a lock that
resolution is a hidden `pip freeze` at build time. Instead
[`constraints.txt`](constraints.txt) is a **committed, exact-pin lock** of the
complete runtime closure (generated from the tested repo venv):

- every version bump arrives as a reviewable diff of the lock file;
- the release gate fails on a stale lock (a constraint that no longer
  satisfies the resolved tree) — resolution errors out instead of drifting.

### Hash locking (F9, repaired Q3)

Exact pins lock *versions*, not *artifacts* — a compromised or
yanked-and-replaced PyPI wheel under the same version string would install
silently. The lock is **hash-locked in the tree**: every pin carries the
sha256 of the exact wheel the Ubuntu 24.04 / py3.12 / x86_64 build fetches.

```bash
packaging/deb/update-constraints.sh               # pins + wheel hashes (network)
packaging/deb/update-constraints.sh --pins-only   # pins only (offline refresh)
```

Hashing, wheel-to-pin matching, validation, and the atomic replace all
live in [`locklib.py`](locklib.py) (stdlib-only, unit-tested offline in
`tests/test_deb_lock.py` against a local wheelhouse with a real
`pip install --require-hashes` consumer). The history: the original
shell generator scraped `python -m pip hash` output with an `awk
'/^sha256=/'` pattern, but pip prints `--hash=sha256:…` — the pattern
matched nothing and the writer emitted malformed `name==version --`
lines. `locklib.py write` now hashes the downloaded wheel bytes directly,
rejects missing/duplicate/version-mismatched wheels, self-validates the
generated text, and only then replaces `constraints.txt` (any failure
preserves the previous lock).

Release builds are **hash-locked by refusal, not by note** (Q3/E4):
`build-deb.sh` validates the lock (`locklib.py validate
--require-hashes`) and fails outright when it carries no hashes. Pin-only
builds remain available for development as an explicit opt-in that
publishing cannot accept:

```bash
DEB_ALLOW_PIN_ONLY=1 packaging/build-deb.sh   # dev only — never ship this
```

When the lock carries hashes, `build-deb.sh` installs fully artifact-locked:

- the lock is installed with `pip install --require-hashes -r constraints.txt`
  (via `-r`, not `-c` — pip only *enforces* hashes carried by requirements;
  hashes in a bare constraints file are parsed but never checked), so every
  downloaded wheel is verified against the committed sha256;
- the application itself is installed `--no-deps` from the committed source
  tree — nothing about it is fetched from an index.

After changing `pyproject.toml` runtime dependencies, refresh with a full
(networked) `update-constraints.sh` run and commit the diff — the diff is
the review.
