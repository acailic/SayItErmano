# Wayland/X11 live smoke-matrix evidence

Output of the one-command harness
([scripts/wayland_smoke.py](../../../scripts/wayland_smoke.py), runbook:
[docs/dev/wayland-smoke-matrix.md](../../dev/wayland-smoke-matrix.md)).
One directory per run: `<date>-<session-profile>` (`gnome-wayland`,
`sway`, `kde-wayland`, `hyprland`, `wayland`, `x11`; a same-day rerun
of the same profile gets `-2`, `-3`, … — earlier evidence is never
overwritten). Each directory holds:

- `results.json` — machine-readable rows (schema, session probe,
  commit, env + compositor versions, per-row status/evidence, summary).
- `results.md` — the human-readable table + verdict.

Commit the whole directory next to the run; matrix conclusions and the
parity verdict are recorded in the matrix doc's §Runs section.
