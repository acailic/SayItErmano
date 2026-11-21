# Wayland smoke evidence — the parity gate (#13)

Live-session evidence tree for the
[Wayland + context smoke matrix](../../dev/wayland-smoke-matrix.md)
(REQUIRED-BEFORE-PARITY): Ubuntu and Fedora default to Wayland, so a
paid Linux product proven only on X11 is unsellable. One directory per
run, written by the harness:

    docs/quality/wayland/<date>-<session>/
        results.json   machine-readable rows (merged across same-day reruns)
        report.md      RESULT line, automated table, MANUAL checklist

`session` is one of `x11`, `gnome-wl`, `kde-wl`, `sway`, `hyprland`
(run order: GNOME Wayland first, then sway; X11 is the same-build
regression control and must show zero deltas against the previous
recorded X11 pass).

## Owner workflow

1. Enable a real Wayland login (GDM `WaylandEnable=true`, or a sway VM).
2. Log into the session and run, from the repo root:

   ```bash
   python3 scripts/wayland_smoke.py
   ```

3. Do the MANUAL checklist the printed `report.md` carries (dictation,
   sleep/wake, lock/unlock, autostart), ticking items in the file.
4. Commit the evidence directory. Append a dated line to the Runs
   section of the [runbook](../../dev/wayland-smoke-matrix.md).
5. Every red row becomes a fix or a known-issues entry users can find
   (STATUS.md + the support docs), linked from the run's `report.md`.

Offline proof of the harness itself (session detection, row selection,
evidence writing, red-row exit code; headless-safe):
`tests/test_wayland_smoke.py`. No run directories yet — the gate opens
with the first owner run.
