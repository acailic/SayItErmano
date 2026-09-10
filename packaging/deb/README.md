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

### Hash locking (F9)

Exact pins lock *versions*, not *artifacts* — a compromised or
yanked-and-replaced PyPI wheel under the same version string would install
silently. A release therefore regenerates the lock **with wheel hashes**:

```bash
packaging/deb/update-constraints.sh               # pins + wheel hashes (network)
packaging/deb/update-constraints.sh --pins-only   # pins only (offline refresh)
```

The full run downloads the exact wheels the Ubuntu 24.04 / py3.12 / x86_64
build would fetch and records each `--hash=sha256:…` beside its pin (needs
pip ≥ 23.1 — `build-deb.sh` upgrades the build venv's pip first). **The
committed lock is pin-only today: hashes require that network run and are
regenerated + committed at release time**, before the locked deb build.

When the lock carries hashes, `build-deb.sh` switches to fully artifact-
locked installation automatically:

- the lock is installed with `pip install --require-hashes -r constraints.txt`
  (via `-r`, not `-c` — pip only *enforces* hashes carried by requirements;
  hashes in a bare constraints file are parsed but never checked), so every
downloaded wheel is verified against the committed sha256;
- the application itself is installed `--no-deps` from the committed source
tree — nothing about it is fetched from an index.

Without hashes the build is pin-locked only and prints a loud note to that
effect.
