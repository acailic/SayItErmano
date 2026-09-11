# Agent working rules for this repo

## One tree per agent — not negotiable

This repository historically ran multiple agent sessions (ZCode, claude
factory chains) in the SAME working tree. That caused repeated incidents
(2026-09-05/07): uncommitted in-flight work from one session was swept
into another's commits, and twice **hard-discarded** as "contamination"
(`git restore` with nothing in reflog or stash). Committed work was never
lost; uncommitted work was.

**Fix in place (2026-09-07):** a dedicated worktree exists for agent use:

- Main tree: `/home/nistrator/Documents/github/FluidVoiceLinux` — branch
  `linux` (the project's main branch). The human and ONE designated
  session work here.
- Agent tree: `/home/nistrator/Documents/github/FluidVoiceLinux-agent` —
  branch `agent/linux`. Any additional/parallel session works HERE.

### Rules

1. If you are not the only active session, work in the agent worktree:
   `cd ../FluidVoiceLinux-agent`. Create further worktrees as needed
   (`git worktree add ../FluidVoiceLinux-<name> -b agent/<name>`) — one
   per concurrent agent.
2. Never `git restore`/`checkout --` files you did not create. If the
   tree contains someone else's WIP, either leave it alone or coordinate;
   discarding it destroys their work.
3. Tests: run the canonical unit tier from your worktree root —
   `SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python
   bash scripts/run_test_tier.sh unit` (the script resolves the shared
   venv via SAYIT_PY; cwd first on sys.path, so your worktree's
   `fluidvoice/` is the copy under test). It is offline-only: a network
   guard fails any outbound socket, model/GTK/network-marked tests are
   deselected. Never bare `pytest`. GUI lane: `... run_test_tier.sh gtk`
   (needs a display). Integration (`... run_test_tier.sh integration`)
   needs the real model/mic and grabs hotkeys — coordinate with the live
   daemon before running it on this desktop.
4. Merge-back: commit on `agent/linux` (fast-forward it onto `linux` when
   clear: `git -C <main> merge --ff-only agent/linux`), or cherry-pick.
   Push `linux`; do not push `origin/main` (mirrors upstream) and do not
   touch the ~300 inherited upstream branches.
5. If a brief/spec is already marked SHIPPED (see `requests/*.md`
   STATUS headers), do not re-implement it — check `git log --oneline`
   first.
6. Housekeeping for live smoke tests on this desktop: restarting the
   production daemon is a two-step (stop the unit, run the repo daemon,
   then `systemctl --user start sayit-ermano` afterwards); restore any
   config keys you flip in `~/.config/sayit-ermano/config.toml`.
