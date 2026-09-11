# Evidence index — requirements → tests → reports → release

One place to answer "what proves X, and for which artifact?" (Q12).
Update this page as evidence lands; a claim with no entry here is a
claim without support.

## How to read this index

- **Requirement** — the behavior/claim, phrased as users experience it.
- **Automated proof** — tests that run in `just gate` (unit tier) or
  another tier's canonical command (`just test-ui`, `just test-process`,
  `just test-integration`).
- **Dated evidence** — reports/matrices/soak runs attached to a source
  SHA or release; where they live.
- Policy for releases: [release gates](../dev/release-gates.md) —
  required tiers for the changed paths + artifact identity (Q4
  provenance chain), or an explicit recorded waiver.

## Product claims

| Requirement | Automated proof | Dated evidence |
|---|---|---|
| Dictation transcribes locally, offline | unit: `test_daemon.py`, `test_privacy_contracts.py` (local mode stays local; network guard) | real-model e2e: `tests/integration/test_e2e_transcribe.py` (needs_model) |
| Nothing outlives a test / the app exits promptly | unit: conftest leak gate + `test_leak_gate_meta.py` (exit-status proven via subprocess runs) | — |
| History survives crashes, full disks | unit: `test_history_durability.py` (SIGKILL child, ENOSPC, fsync failures) | — |
| Hotkeys pause on screen lock | unit: `test_lifecycle_sequences.py` (lock gate + regression for the missing `_locked` flip), `test_lock_suppression.py` | live lock/resume matrix: **pending** ([wayland-matrix brief](../../requests/wayland-matrix-execution.md), OPEN) |
| Commands never run unconfirmed | unit: `test_command.py` (single/double press, Escape, timeout), `test_command_coord.py` | — |
| Secrets never appear in user-visible surfaces | unit: `test_privacy_contracts.py` (status/get_config/doctor subprocess), `test_config_registry.py`, `test_remote_stt.py` | — |
| Deb installs exactly the reviewed dependency set | unit: `test_deb_lock.py` (hash-locked round trip, pip `--require-hashes` consumer) | hashed-lock deb build 2026-09-12 (Q3 acceptance, 66M artifact, every wheel verified) |
| A release contains what CI tested | unit: `test_release_verify.py` (manifest→bytes→source→lock→evidence chain, all rejection cases) | per-release: prepare manifest + summary sha256; publish verifies and records `evidence` input |

## Speech quality

| Requirement | Automated proof | Dated evidence |
|---|---|---|
| Metric math correct (WER/CER/RTF conventions) | unit: `test_evalharness.py`, `test_properties.py` (incl. WER may exceed 1) | — |
| Real-speech quality per language/mic/noise | — (needs recorded corpus) | **NOT ESTABLISHED**: [speech-corpus brief](../../requests/speech-corpus-recording.md), OPEN; synthetic committed corpus only |
| Dead-capture guard suppresses hallucination | unit: `test_lock_suppression.py` + daemon guard tests | preview-fidelity session 2026-09-11 (F1 0.26/0.49/0.57 @2/3/4s, 16 takes) |

## Desktop / platform

| Requirement | Automated proof | Dated evidence |
|---|---|---|
| GTK UI works (settings/history/onboarding) | display tier: `test_gtkui*.py`, `test_onboarding_window.py`, `test_settings_profiles.py` (CI gtk-x11 lane: skips fail) | live desktop: runs on this machine's GNOME X11; **Wayland/sway matrices pending** (brief OPEN) |
| Daemon/socket/CLI behave as real processes | process lane: `tests/integration/test_daemon_socket.py`, `test_cli.py` (model-free; headless + Xvfb) | — |
| Insertion into real apps (editor/terminal/browser/Electron) | — (needs live session) | **pending** desktop matrix execution (brief OPEN) |
| Package installs/upgrades/rollbacks cleanly | deb build contract: `build-deb.sh` + locklib gates | **NOT ESTABLISHED** for the current release line: install/upgrade/rollback matrix is Q9 work |

## Operations / maintenance

| Requirement | Automated proof | Dated evidence |
|---|---|---|
| Gates are the same locally and in CI | unit: `test_dev_gates.py` (py-floor, thread-excitation), tier commands in `justfile` mirrored by ci.yml | per-CI-run junit + coverage XML artifacts |
| Coverage does not regress | CI unit job uploads `build/coverage/unit.xml` | [coverage baseline 2026-09-12](../research/2026-09-12-coverage-baseline.md): 79.9% lines / 78.9% branches (unit tier) |
| Long-running stability (memory, FDs, recovery) | soak tooling: `scripts/soak.py`, `evalharness soak` | **pending**: 2h pre-release + 24h lifecycle soaks (Q9) |

## Release-to-evidence binding

release-prepare binds the deb to its exact source (manifest: package
sha256, tracked-source digest, lock sha256, run id); release-publish
re-verifies the chain against the release SHA and records the operator's
`evidence` input (which must quote the deb sha256 or carry an explicit
`EVIDENCE-SKIP: <reason>`). Find that record in the release-publish run
summary and the GitHub release page for each tag.
