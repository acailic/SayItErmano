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

Same app set where installable (foot, kitty, firefox, nautilus—or
another GTK search field). Repeat A1–A15 with:

| # | Case | Expected | Result |
|---|------|----------|--------|
| B1 | A2–A6 equivalents (foot/kitty + editor) | same as GNOME matrix | ☐ |
| B2 | wtype insertion + context identity | terminal quirks active in foot/kitty via AT-SPI identity | ☐ |
| B3 | No AT-SPI bus in the session (minimal sway) | degrade: no context, exactly pre-P2 behavior | ☐ |
| B4 | Privacy sweep | as A13 | ☐ |

## Matrix C — X11 control (regression)

| # | Case | Expected | Result |
|---|------|----------|--------|
| C1 | Default config (context off) | byte-identical pre-P2 behavior | ☐ |
| C2 | `context.enabled=true, provider="auto"` | x11 identity provider; WM_CLASS quirks exactly as before (identity-only: no continuation/GAAV changes) | ☐ |
| C3 | `provider="atspi"` on X11 | role/selection/preceding available in GTK apps; A3/A4/A6 pass on X11 too | ☐ |

## Definition of parity (gate to flip the default)

1. A and B fully checked (B3 documents the degrade path), C green.
2. No crash, hang (> 1 s insertion delay) or mis-typed window in any
   case; A11/B3 degrade silently.
3. Privacy sweep clean on both Wayland matrices.
4. Results recorded here (append a dated section below per run).

### Runs

_(none yet — first run pending)_
