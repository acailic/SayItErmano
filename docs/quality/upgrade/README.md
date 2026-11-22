# Upgrade/rollback rehearsal evidence (Q9 — user-space lifecycle)

Every file here is produced by `scripts/upgrade_rehearsal.py`: a full
install → update → rollback lifecycle through the REAL one-shot
installer (`scripts/install-one-shot.sh`) inside a sandboxed fake
machine — throwaway `$HOME`, stub `systemctl`/`dpkg`/`curl` binaries
that log their calls (the same sandboxing technique as
`tests/test_installer_update.py`). No root, no apt, no network: the
"new release" is a locally-built payload venv (this tree with a bumped
`fluidvoice/__init__.py`) packaged into the one-shot deb layout.

## What each rehearsal proves

1. **install** — `install-one-shot.sh <deb>` puts a working user-space
   install down (`~/.local/share/sayit-ermano/venv`, launcher,
   unit) reporting v0.
2. **seed** — config at `~/.config/sayit-ermano/config.toml`
   (hand-written, like a user's) and history rows written through the
   app's own `history.append` API.
3. **update** — `install-one-shot.sh --update <newer.deb>` stops the
   daemon, swaps the venv, restarts; the binary reports v1.
4. **state gates** — config keys preserved (byte-identical reported
   too), history rows **byte-identical** (sha256 both sides).
5. **daemon smoke** — the upgraded venv actually runs the daemon
   (socket answers `status` with the new version).
6. **rollback** — `--update` back to v0 reverts the version with the
   user state still byte-identical.

## Files

- `YYYY-MM-DD.json` / `.md` — one per rehearsal; the JSON carries the
  step table (with timings), the state-check results, the stub call
  log tail and the source SHA.

## Reproduce

```sh
python scripts/upgrade_rehearsal.py            # evidence into this dir
python scripts/upgrade_rehearsal.py --keep-work  # keep the sandbox
```

The pure gates (version bumping, config-preservation, history
byte-diff) are unit-tested offline in `tests/test_q9_soak_upgrade.py`;
the full real-subprocess rehearsal runs in the process lane
(`tests/integration/test_q9_harness.py`, `just test-process`).

This rehearsal covers the user-space (one-shot) route. The apt/deb
routes' fresh-VM upgrade cells stay with the
[install matrix](../install-matrix/README.md) (issue #14).
