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

## The one-command harness (issue #13, 2026-11-21)

`scripts/wayland_smoke.py` executes this runbook: it detects the
current session from the environment (`XDG_SESSION_TYPE`,
`WAYLAND_DISPLAY`, `DISPLAY`, `XDG_CURRENT_DESKTOP`, `SWAYSOCK`,
`HYPRLAND_INSTANCE_SIGNATURE` — the same probe the daemon uses), runs
every applicable row below that a machine can check without a human
(tool presence, `doctor`'s session matrix, the GlobalShortcuts portal
version, GNOME/Plasma minimums), prints the voice/compositor rows as a
dated MANUAL checklist, and writes evidence (JSON + markdown) under
`docs/quality/wayland/<date>-<session>/` — commit that directory.
Exit code 1 iff any automated row is red.

```bash
python3 scripts/wayland_smoke.py            # run the applicable rows
python3 scripts/wayland_smoke.py --dry-run  # plan only; headless-safe
python3 scripts/wayland_smoke.py --row s2-hotkey-portal   # one row (see below)
python3 scripts/wayland_smoke.py --session sway           # override detection
```

Offline proof (what CI can check without a display):
`tests/test_wayland_smoke.py`. The live rows are owner actions —
enable a Wayland login (GDM `WaylandEnable` or a sway VM), log in,
run the harness once per session, do the manual checklist it prints,
commit the evidence. Same-day reruns merge into the same evidence
directory; rerunning a single row after a fix only replaces that row.

Red-row policy: every red row becomes either a fix or a known-issues
entry users can find (STATUS.md + the install/known-issues docs),
linked from the evidence `report.md` — until then this gate stays red.

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

## Matrix S — session × hotkey × insertion axes (the pinned 8-row set)

Session axes: **x11**, **GNOME-WL 48+**, **KDE-WL 6.x**, **sway**,
**Hyprland**. Hotkey axes: GlobalShortcuts portal / XGrabKey /
compositor-bind `--toggle`. Insertion axes: EIS keysym /
clipboard+Ctrl+V / zwp_virtual_keyboard / uinput. Backend facts
verified 2026-11-21 (links on issue #13): GNOME 48+ and Plasma 6.7.4+
ship the GlobalShortcuts portal; sway has none
(xdg-desktop-portal-wlr#240) so the compositor-bind `--toggle` path IS
primary there; wtype/zwp_virtual_keyboard is wlroots-only (mutter#1974,
KDE bug 512996) so GNOME/KDE insertion rides ydotool (uinput) or the
clipboard. Run order: GNOME Wayland first, then sway; the X11 row is
the same-build regression control and must show zero deltas. The
lifecycle rows are Handy's open gaps: hotkey and insertion must still
work after sleep/wake, lock/unlock and autostart.

The harness encodes this table (`scripts/wayland_smoke.py`); the
Invoke column is the exact per-row line (from the repo root, logged
into the session). `auto` = the harness checks it itself; `manual` =
printed on the checklist for the owner. Sessions: G=GNOME-WL,
K=KDE-WL, S=sway, H=Hyprland, X=X11; "all" = every matrix session.

| Cell | Case | Sessions | Mode | Invoke |
|---|---|---|---|---|
| S1 | session detection + doctor session matrix agree (ROADMAP 5) | all | auto | `--row s1-session-doctor` |
| S2 | GlobalShortcuts portal exists (version ≥ 1) | G K H | auto+manual | `--row s2-hotkey-portal` |
| S3 | no GlobalShortcuts backend on sway (expected absence) | S | auto | `--row s3-hotkey-portal-absent` |
| S4 | X11 global hotkey grab resolves | X | auto+manual | `--row s4-hotkey-x11-grab` |
| S5 | compositor-bind `--toggle` path surfaced (primary on sway) | G K S H | auto+manual | `--row s5-hotkey-compositor-toggle` |
| S6 | zwp_virtual_keyboard typed insertion (wtype) — ROADMAP 2 | S H | auto+manual | `--row s6-insert-wtype` |
| S7 | uinput typed insertion (ydotool) — ROADMAP 3 | G K | auto+manual | `--row s7-insert-uinput` |
| S8 | clipboard + Ctrl+V paste path | G K S H | auto+manual | `--row s8-insert-clipboard` |
| S9 | xdotool typed insertion | X | auto+manual | `--row s9-insert-xdotool` |
| S10 | GNOME ≥ 48 (portal hotkeys) | G | auto | `--row s10-version-gnome` |
| S11 | Plasma ≥ 6.7.4 (shortcut description changes) | K | auto | `--row s11-version-plasma` |
| S12 | portal version recorded (≥ 1.21 = new consent flow) | G K H | auto | `--row s12-version-portal` |
| S13 | non-US keyboard layout (KDE keysym regression class) | K | auto+manual | `--row s13-layout-nonus` |
| S14 | sleep/wake: hotkey + insertion still work | all | manual | `--row s14-lifecycle-suspend` |
| S15 | lock/unlock: suppressed while locked, works after | all | manual | `--row s15-lifecycle-lock` |
| S16 | autostart: daemon up on login, dictation works | all | manual | `--row s16-lifecycle-autostart` |
| S17 | baseline with no insertion tools (ROADMAP 1) | all | manual | `--row s17-baseline-bare` |
| S18 | overlay degrades to notification preview (ROADMAP 4) | G K S H | manual | `--row s18-overlay-notifications` |
| S19 | evdev push-to-talk (ROADMAP 7) | G K S H | manual | `--row s19-evdev-ptt` |
| S20 | X11 regression, same build, zero deltas (ROADMAP 8) | X | manual | `--row s20-x11-regression` |

## Matrix A — GNOME Wayland (Ubuntu 24.04 / GNOME 46+)

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

Harness rows (per-cell invoke, logged into GNOME Wayland):
`--row a1-doctor-context` (auto+manual), `--row a2-empty-field`,
`--row a3-mid-sentence`, `--row a4-after-period`, `--row a5-selection`,
`--row a6-search-field`, `--row a7-terminal-typed`,
`--row a8-spoken-send-terminal`, `--row a9-spoken-send-app`,
`--row a10-spoken-send-rule`, `--row a11-no-bus`,
`--row a12-provider-none`, `--row a13-privacy-sweep`,
`--row a14-latency`, `--row a15-focus-change`.

## Matrix B — sway (wlroots; wtype path)

Same app set where installable (foot, kitty, firefox, nautilus—or
another GTK search field). Repeat A1–A15 with:

| # | Case | Expected | Result |
|---|------|----------|--------|
| B1 | A2–A6 equivalents (foot/kitty + editor) | same as GNOME matrix | ☐ |
| B2 | wtype insertion + context identity | terminal quirks active in foot/kitty via AT-SPI identity | ☐ |
| B3 | No AT-SPI bus in the session (minimal sway) | degrade: no context, exactly pre-P2 behavior | ☐ |
| B4 | Privacy sweep | as A13 | ☐ |

Harness rows (per-cell invoke, logged into sway):
`--row b1-atspi-context`, `--row b2-wtype-identity` (tool check:
wtype), `--row b3-no-atspi`, `--row b4-privacy-sweep`.

## Matrix C — X11 control (regression)

| # | Case | Expected | Result |
|---|------|----------|--------|
| C1 | Default config (context off) | byte-identical pre-P2 behavior | ☐ |
| C2 | `context.enabled=true, provider="auto"` | x11 identity provider; WM_CLASS quirks exactly as before (identity-only: no continuation/GAAV changes) | ☐ |
| C3 | `provider="atspi"` on X11 | role/selection/preceding available in GTK apps; A3/A4/A6 pass on X11 too | ☐ |

Harness rows (per-cell invoke, logged into X11):
`--row c1-default-config`, `--row c2-context-auto`,
`--row c3-provider-atspi`. Plus the S-matrix X11 rows S4/S9/S20 —
S20 on the SAME build as the Wayland runs, zero deltas.

## Definition of parity (gate to flip the default)

1. A and B fully checked (B3 documents the degrade path), C green.
2. No crash, hang (> 1 s insertion delay) or mis-typed window in any
   case; A11/B3 degrade silently.
3. Privacy sweep clean on both Wayland matrices.
4. Results recorded here (append a dated section below per run).

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
