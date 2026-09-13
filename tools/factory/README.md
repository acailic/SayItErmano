# SSSF agent factory (vendored)

The SSSF/ADW agent factory that produced this repository's
implementation plans and build chains. **Agent tooling, not
SayItErmano product code** — kept in-repo, self-contained under
`tools/factory/` (org plan 2.4, option B, 2026-09-14):

- `adws/` — workflow scripts (`adw_prompt.py`, `adw_scout.py`,
  `adw_plan*.py`, `adw_simple_sdlc.py`, ...) plus runtime data
  (`adw_data/`, mostly gitignored).
- `sssf-skill/` — the Claude Code skill (was `.claude/skills/sssf/`).
  To use it as a skill again, symlink or copy it back into
  `.claude/skills/sssf`.
- `justfile` — the `factory-*` recipes, imported by the root justfile.
  Invoke from the repo root: `just factory-sessions`,
  `SSSF_CONFIG=other.yaml just factory-prompt "..."`.

Historical ADW implementation plans moved to
`docs/plans/archive/specs/`.

## Option A (future)

The org plan recommends eventually extracting the factory into its own
`sssf-factory` repo (or submodule) loaded via a justfile import — this
folder is structured so that move is a pure `git filter-repo`/copy +
one import-line change.
