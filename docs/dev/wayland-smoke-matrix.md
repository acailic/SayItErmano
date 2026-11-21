# Wayland + context smoke matrix — REQUIRED-BEFORE-PARITY

Status: **NOT DONE — required before declaring Wayland parity or
flipping `context.enabled` to true by default** (plan P2: "Complete
live smoke matrices on GNOME Wayland and sway before declaring
Wayland parity").

This is the manual, live-session counterpart of the offline suite
(`tests/test_context*.py` proves the seam against fakes only; the
AGENTS.md rule forbids tests from probing the live X/AT-SPI session).
Run it on real hardware, logged into each session type below. Record
date + commit SHA + machine per matrix.

## One-command harness

`scripts/wayland_smoke.py` IS the runner for everything below: it
detects the session it is logged into (same probe precedence as
`fluidvoice/session.py`), selects the applicable rows of matrices A–D,
runs the automated rows (doctor, daemon status, privacy sweep), prompts
for the manual dictation rows, and records evidence under
[docs/quality/wayland/](../quality/wayland/README.md)
(`<date>-<session>/results.json` + `results.md`). Exit code **1 on any
red row**, 2 on usage errors, 0 otherwise.

```sh
python3 scripts/wayland_smoke.py --dry-run   # plan only — no session/display needed
python3 scripts/wayland_smoke.py             # live run — once per logged-in session
python3 scripts/wayland_smoke.py --rows A7   # exact per-row (re)run: --rows <ID>
python3 scripts/wayland_smoke.py --rows A3-A5,D2
python3 scripts/wayland_smoke.py --answers A2=pass,A3=fail:wrong-case  # scripted
```

- Profile detection: X11/X11-display → Matrix C + D control rows;
  Wayland + GNOME/Ubuntu/Pop → A + D; + sway → B + D; + KDE/Hyprland/
  other → D only. A live run with no display session refuses (exit 2).
- Per compositor, GNOME Wayland first, then sway; the X11 regression
  pass runs on the same build (zero deltas = C1–C3 green + no behavior
  change on the X11 paths).
- Version minimums (GNOME 48 for portal hotkeys, Plasma 6.7.4 for the
  shortcut description change, xdg-desktop-portal ≥ 1.21 vs the older
  consent flow) are probed automatically into the evidence header.
- Automated rows run `<this python> -m fluidvoice doctor|status --json`
  from the repo root; the privacy sweep scans `history.jsonl`.

## Setup (per matrix)

```toml
# ~/.config/sayit-ermano/config.toml (restore afterwards!)
[context]
enabled = true          # prototype default is false — this matrix tests it
provider = "auto"       # or "atspi" on the X11 matrix
max_preceding_chars = 120
```

Also required in the session: `python-atspi` (or GIR Atspi) importable
by the daemon's Python, an accessibility bus running
(`AT_SPI_BUS_ADDRESS` present; on GNOME: Settings → Accessibility →
anything enabled is not required, the bus runs by default), and the
usual tools (wtype or ydotool + ydotoold, wl-clipboard).

Housekeeping: this desktop runs the production daemon LIVE — use the
documented two-step (stop the unit, run the repo daemon, restore
afterwards) and restore any config keys you flip.

## Matrix A — GNOME Wayland (Ubuntu 24.04 / GNOME 46+)

Run: `python3 scripts/wayland_smoke.py` (GNOME Wayland login selects
A1–A15 + D automatically); single row: `--rows A7`.

Test apps: gnome-terminal (or ptyxis), gedit/gnome-text-editor,
nautilus (search field), Firefox (E10s a11y ON), an Electron app
(VS Code), a Qt app (kate/konsole on GNOME if available).

| # | Case | Expected | Result |
|---|------|----------|--------|
| A1 | `doctor` context section | names the atspi provider, no crash | ☐ |
| A2 | Dictate into empty gedit field | text typed; daemon log line `context: atspi (role=text, …)` | ☐ |
| A3 | Dictate after typing `mid sentence` (no trailing space) | one leading space inserted, no capitalization | ☐ |
| A4 | Dictate after a `.` + newline | no leading space, first letter capitalized | ☐ |
| A5 | Select text in gedit, dictate | selection read does NOT disturb the paste/typing; dictation lands at caret | ☐ |
| A6 | Nautilus search field (role=search) | GAAV: lowercase first letter, trailing period stripped | ☐ |
| A7 | gnome-terminal, typed insertion | trailing autocomplete space; paste (long text) uses ctrl+shift+v | ☐ |
| A8 | gnome-terminal, spoken-send phrase | Enter suppressed (log: `spoken-send: Enter suppressed at insert`) | ☐ |
| A9 | Non-terminal app, spoken-send phrase | Enter pressed after typing | ☐ |
| A10 | Canonical rule `{match=["gnome-terminal"], spoken_send="on"}` | Enter IS pressed in the terminal | ☐ |
| A11 | Turn accessibility bus off (kill at-spi2 bus) and dictate | take succeeds, exactly pre-P2 behavior (no context line), no crash | ☐ |
| A12 | `context.provider = "none"` | identical to A11 | ☐ |
| A13 | Privacy sweep after A2–A10 | `~/.local/share/sayit-ermano/history.jsonl` rows contain no preceding/selection text, no AT-SPI app ids beyond the usual take-start field (absent on Wayland) | ☐ |
| A14 | Latency: insertion-time read | no perceptible delay between stop and typing (< ~150 ms extra; walk is bounded) | ☐ |
| A15 | Focus change mid-take (start in gedit, click terminal before stop) | identity/quirks follow the FOCUS AT INSERTION (terminal quirks apply), no wrong-window typing | ☐ |

## Matrix B — sway (wlroots; wtype path)

Run: `python3 scripts/wayland_smoke.py` (sway login selects B1–B4 + D
automatically); single row: `--rows B2`.

Same app set where installable (foot, kitty, firefox, nautilus—or
another GTK search field). Repeat A1–A15 with:

| # | Case | Expected | Result |
|---|------|----------|--------|
| B1 | A2–A6 equivalents (foot/kitty + editor) | same as GNOME matrix | ☐ |
| B2 | wtype insertion + context identity | terminal quirks active in foot/kitty via AT-SPI identity | ☐ |
| B3 | No AT-SPI bus in the session (minimal sway) | degrade: no context, exactly pre-P2 behavior | ☐ |
| B4 | Privacy sweep | as A13 | ☐ |

## Matrix C — X11 control (regression)

Run (X11 login, same build as the Wayland passes): `python3
scripts/wayland_smoke.py` (selects C1–C3 + the D control rows
automatically); single row: `--rows C2`. Zero deltas against the
pre-Wayland build is the gate.

| # | Case | Expected | Result |
|---|------|----------|--------|
| C1 | Default config (context off) | byte-identical pre-P2 behavior | ☐ |
| C2 | `context.enabled=true, provider="auto"` | x11 identity provider; WM_CLASS quirks exactly as before (identity-only: no continuation/GAAV changes) | ☐ |
| C3 | `provider="atspi"` on X11 | role/selection/preceding available in GTK apps; A3/A4/A6 pass on X11 too | ☐ |

## Matrix D — session axes (hotkey × insertion × lifecycle)

Per-compositor expected text (what "pass" means there); the harness
selects the applicable rows per session automatically. Applicable
rows: D1–D5 Wayland only; D6–D8 every session; D9 Wayland only
(x11 additionally runs D2–D3 as its control rows).

| # | Case | Expected (per session) | Invoke |
|---|------|------------------------|-------|
| D1 | Baseline: daemon alive under this session | daemon starts (foreground or user unit); `status` answers; a no-tool session surfaces the honest notification | `python3 scripts/wayland_smoke.py --rows D1` |
| D2 | Hotkey axis | x11: XGrabKey grab toggles dictation · GNOME-WL 48+: GlobalShortcuts portal OR custom shortcut to the toggle script · KDE-WL 6.7.4+: custom shortcut · sway: NO portal backend (xdg-desktop-portal-wlr#240) — compositor `bindsym` + `--toggle` IS the primary · Hyprland: portal exists + compositor `bind` | `python3 scripts/wayland_smoke.py --rows D2` |
| D3 | Insertion axis | x11: xdotool typed + verified paste (xclip) · GNOME-WL: ydotool/uinput (wtype does NOT work — mutter#1974) · sway: wtype (zwp_virtual_keyboard — the expected wlroots path) · KDE-WL: wtype or ydotool, note Klipper snapshots (KDE bug 512996) · Hyprland: wtype or ydotool · all Wayland: paste via wl-clipboard + Ctrl+V | `python3 scripts/wayland_smoke.py --rows D3` |
| D4 | Overlay/preview | recording shows the notification preview; no X11-pill attempt noise in the daemon log | `python3 scripts/wayland_smoke.py --rows D4` |
| D5 | evdev push-to-talk (needs input-group access) | hold-to-talk works; note the matched device name (skip if no /dev/input access) | `python3 scripts/wayland_smoke.py --rows D5` |
| D6 | Lifecycle: sleep/wake | after suspend+resume, hotkey and insertion still work; daemon needed no manual restart (or autorecovers) | `python3 scripts/wayland_smoke.py --rows D6` |
| D7 | Lifecycle: lock/unlock | after lock+unlock, hotkey and insertion still work; recording paused while locked (no take across lock) | `python3 scripts/wayland_smoke.py --rows D7` |
| D8 | Lifecycle: autostart | daemon autostarts at login (systemd user unit enabled) and is alive in THIS session | `python3 scripts/wayland_smoke.py --rows D8` |
| D9 | Non-US keyboard layout | dictation with a non-US layout active lands correct text; keysym layout regressions (the KDE keysym class) noted | `python3 scripts/wayland_smoke.py --rows D9` |

## Definition of parity (gate to flip the default)

1. A and B fully checked (B3 documents the degrade path), C green.
2. D rows green on each session matrix (D5 evdev PTT may record a
   documented skip when the machine has no input-group access); the
   lifecycle rows D6–D8 are part of the gate, not optional.
3. No crash, hang (> 1 s insertion delay) or mis-typed window in any
   case; A11/B3 degrade silently.
4. Privacy sweep clean on both Wayland matrices.
5. X11 regression pass on the same build with zero deltas (C1–C3 green
   plus unchanged X11 behavior).
6. Results recorded here (append a dated section below per run; the
   harness output lands in
   [docs/quality/wayland/](../quality/wayland/README.md)).

### Runs

- **2026-09-13 (night), Matrix C (X11 control) on GNOME X11/Pop!_OS 46,
  worktree 09e1b9f.** C1 ✓, C2 ✓ (x11 identity provider,
  identity-only). C3 ✗ blocked — not by X11 itself but by provider
  defects that equally hit the Wayland REQ-PARITY path: ReadLimits.apps
  =16 truncates this desktop's 28-app a11y tree (silent `missing`),
  `_find_active_window` picks an unfocused Electron window flagged
  ACTIVE, and the GIR text-interface call silently returns None
  (python-atspi absent in the venv). Full detail + reproducers:
  [night-2026-09-13-desktop-matrix-x11.md](../research/night-2026-09-13-desktop-matrix-x11.md)
  (findings F1–F3). Matrices A (GNOME Wayland) and B (sway): NOT RUN.
- **2026-09-14, C3 blocker trio FIXED (1f26739)** — apps cap 64,
  ACTIVE-candidate probing with per-candidate budget slices, GIR
  unbound `Atspi.Text.*` fallbacks (ledger F-31..F-33). Mechanism
  live-verified on the same desktop (focused gedit at app index 26,
  GIR-only venv: usable field-level read, preceding text returned).
  The C3 CELL still needs a matrix rerun to close.
- **2026-11-21, one-command harness landed (#13)** —
  `scripts/wayland_smoke.py` + Matrix D rows committed; live rows
  still need a real Wayland login (GDM here has `WaylandEnable=false`).
  Owner flow per session: log in (GNOME Wayland first, then sway, then
  X11), run the harness once, commit the evidence directory it prints.
