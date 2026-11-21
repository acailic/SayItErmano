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

## Harness — one command per session (2026-11-21, issue #13)

The matrices above are executed by [`scripts/wayland_smoke.py`](../../scripts/wayland_smoke.py):
it detects the session from the environment (same precedence as
`fluidvoice/session.py`: `XDG_SESSION_TYPE` wins, then
`WAYLAND_DISPLAY`, then `DISPLAY`; compositor from
`XDG_CURRENT_DESKTOP`), selects the rows that apply, runs the
automatable checks (daemon `status`, `doctor`, autostart-enabled,
insertion-tool preflights), records MANUAL rows with their exact
invoke line for the owner, and records GAP rows — backends not
implemented in this build (GlobalShortcuts portal hotkey, EIS keysym
insertion, wtype on GNOME/Plasma) — so no axis cell is silently
dropped. Evidence lands under
`docs/quality/wayland/<date>-<session>/` (`results.json` = latest
run, `report.md` = append-only journal + the manual checklist the
owner ticks); the tree's [README.md](../quality/wayland/README.md)
is the generated session tracker (`--init` regenerates it).
Exit code 1 iff an executed row failed; skips and gaps
carry reasons, they are never hidden.

```bash
python3 scripts/wayland_smoke.py --dry-run                 # plan for THIS session
python3 scripts/wayland_smoke.py --dry-run --session sway   # plan per session, CI-safe
python3 scripts/wayland_smoke.py                            # run + record (live session)
python3 scripts/wayland_smoke.py --print-rows               # regenerate the tables below
python3 scripts/wayland_smoke.py --init                     # regenerate the evidence tracker
```

Version pins the rows carry (evidence base verified 2026-11-21,
issue #13): GNOME 48+ for the GlobalShortcuts portal hotkey;
xdg-desktop-portal >= 1.21 vs the older consent flow; Plasma 6.7.4
shortcut-description changes; the non-US layout row covers the KDE
keysym-layout regression class. sway has no GlobalShortcuts backend
(xdg-desktop-portal-wlr#240) — compositor-bind `--toggle` is the
primary hotkey there and wtype/zwp_virtual_keyboard is the expected
insertion path.

Owner workflow (per compositor, GNOME Wayland first, then sway; the
X11 regression pass runs on the same build):

1. Enable a real Wayland login (GDM: `WaylandEnable=true` in
   `/etc/gdm3/custom.conf` + restart GDM, or boot the sway VM) and
   log into the session.
2. Run `python3 scripts/wayland_smoke.py` once per session; every
   red row gets a fix or a known-issues entry users can find.
3. Tick the MANUAL checklist in the recorded `report.md`; work the
   session's context matrix (A on GNOME Wayland, B on sway, C on
   X11) above.
4. Regenerate the evidence tracker
   (`python3 scripts/wayland_smoke.py --init` — updates
   [docs/quality/wayland/README.md](../quality/wayland/README.md)),
   commit the evidence tree; `docs/STATUS.md` and the Flathub
   supported-environments text must match the recorded evidence.

### Row tables — the exact invoke line per row (generated)

Regenerate with `python3 scripts/wayland_smoke.py --print-rows`;
`tests/test_wayland_smoke.py` fails if this section drifts from the
registry. AUTO rows either run the shown command or check tool
presence; MANUAL rows are ticked by the owner; GAP rows are recorded
known-absence, not run.

<!-- wayland-smoke rows BEGIN — generated by scripts/wayland_smoke.py --print-rows; do not edit between the sentinels -->
### x11 — X11 (control)

| row id | kind | exact invoke line / check |
|---|---|---|
| `x11-baseline-daemon-status` | auto | `sayit-ermano status` |
| `x11-baseline-doctor` | auto | `sayit-ermano doctor` |
| `x11-baseline-first-dictation` | manual | focus an editor, press the session hotkey (or `sayit-ermano toggle`), dictate ~10 words, stop; polished text lands at the caret and the take shows in `sayit-ermano history` |
| `x11-hotkey-xgrabkey` | manual | focus an editor, press/release the configured hotkey (default Right Ctrl) |
| `x11-insert-non-us-layout` | manual | switch the session to a non-US layout (e.g. de), dictate symbols/punctuation-heavy text, verify the inserted characters match the layout |
| `x11-insert-paste-live` | manual | copy something to the clipboard first, then dictate >=300 chars; verify the full text lands once and the clipboard is restored afterwards |
| `x11-insert-terminal-live` | manual | dictate into gnome-terminal: a git-style phrase (trailing space), a long paste, and a spoken-send phrase; verify Enter is suppressed only in the terminal |
| `x11-insert-typed-live` | manual | dictate into a plain editor field (gedit/foot); text arrives at the caret and nowhere else |
| `x11-insert-xclip-tools` | auto | present: xclip |
| `x11-insert-xdotool-tools` | auto | present: xdotool |
| `x11-lifecycle-autostart` | auto | `systemctl --user is-enabled sayit-ermano` |
| `x11-lifecycle-lock-unlock` | manual | lock the session (super/L, swaylock, gdm), verify the hotkey does NOT trigger while locked, unlock, verify it works again |
| `x11-lifecycle-sleep-wake` | manual | run `systemctl suspend`, wake the machine, then verify the hotkey still toggles and one dictation inserts; re-run this harness on the same session |
| `x11-context-matrix` | manual | with [context] enabled=true and provider="auto" in the config (restore afterwards!), work through C1-C3 of docs/dev/wayland-smoke-matrix.md and tick them there |
| `x11-regression-zero-delta` | manual | on the SAME build as the Wayland runs: hotkey grab, pill preview, verified paste, spoken-send, rewrite capture — record any delta vs Matrix C |

### gnome-wayland — GNOME Wayland (portal hotkeys need GNOME 48+)

| row id | kind | exact invoke line / check |
|---|---|---|
| `gnome-baseline-daemon-status` | auto | `sayit-ermano status` |
| `gnome-baseline-doctor` | auto | `sayit-ermano doctor` |
| `gnome-baseline-first-dictation` | manual | focus an editor, press the session hotkey (or `sayit-ermano toggle`), dictate ~10 words, stop; polished text lands at the caret and the take shows in `sayit-ermano history` |
| `gnome-hotkey-compositor-bind` | manual | GNOME: Settings -> Keyboard -> View and Customize Shortcuts -> Custom Shortcuts -> Add, Command = sayit-ermano-toggle, bind a key; press it -> dictation toggles |
| `gnome-hotkey-evdev-ptt` | manual | set hotkey.wayland_evdev=true, restart the daemon, hold the key (default KEY_RIGHTCTRL); note the matched device name in the log |
| `gnome-hotkey-portal` | gap | (gap — recorded, not run) GlobalShortcuts portal hotkey backend not implemented in this build; needs GNOME 48+ and xdg-desktop-portal >= 1.21 (older portals differ in the consent flow) — compositor-bind --toggle is the primary path today |
| `gnome-insert-eis` | gap | (gap — recorded, not run) EIS keysym insertion (libei-class protocol) is not implemented in this build — recorded as a known gap on every session |
| `gnome-insert-non-us-layout` | manual | switch the session to a non-US layout (e.g. de), dictate symbols/punctuation-heavy text, verify the inserted characters match the layout |
| `gnome-insert-paste-live` | manual | copy something to the clipboard first, then dictate >=300 chars; verify the full text lands once and the clipboard is restored afterwards |
| `gnome-insert-terminal-live` | manual | dictate into gnome-terminal: a git-style phrase (trailing space), a long paste, and a spoken-send phrase; verify Enter is suppressed only in the terminal |
| `gnome-insert-typed-live` | manual | dictate into a plain editor field (gedit/foot); text arrives at the caret and nowhere else |
| `gnome-insert-wl-clipboard-tools` | auto | present: wl-copy, wl-paste |
| `gnome-insert-wtype` | gap | (gap — recorded, not run) wtype (zwp_virtual_keyboard) cannot work on GNOME Shell: mutter does not implement the protocol (mutter#1974) — ydotool (uinput) is the GNOME insertion path |
| `gnome-insert-ydotool-tools` | auto | present: ydotool |
| `gnome-lifecycle-autostart` | auto | `systemctl --user is-enabled sayit-ermano` |
| `gnome-lifecycle-lock-unlock` | manual | lock the session (super/L, swaylock, gdm), verify the hotkey does NOT trigger while locked, unlock, verify it works again |
| `gnome-lifecycle-sleep-wake` | manual | run `systemctl suspend`, wake the machine, then verify the hotkey still toggles and one dictation inserts; re-run this harness on the same session |
| `gnome-context-matrix` | manual | with [context] enabled=true and provider="auto" in the config (restore afterwards!), work through A1-A15 of docs/dev/wayland-smoke-matrix.md and tick them there |
| `gnome-surface-overlay-preview` | manual | start a take and watch the notification preview; grep the daemon log for x11-pill attempt noise (there must be none) |
| `gnome-surface-settings-script` | manual | open Settings -> Wayland, press Copy, inspect the script, bind it and toggle dictation via the shortcut |

### kde-wayland — KDE Plasma Wayland 6.x (6.7.4 shortcut-description changes)

| row id | kind | exact invoke line / check |
|---|---|---|
| `kde-baseline-daemon-status` | auto | `sayit-ermano status` |
| `kde-baseline-doctor` | auto | `sayit-ermano doctor` |
| `kde-baseline-first-dictation` | manual | focus an editor, press the session hotkey (or `sayit-ermano toggle`), dictate ~10 words, stop; polished text lands at the caret and the take shows in `sayit-ermano history` |
| `kde-hotkey-compositor-bind` | manual | Plasma: System Settings -> Shortcuts -> Add New -> Command or Script... sayit-ermano-toggle, assign a key; press it -> dictation toggles |
| `kde-hotkey-evdev-ptt` | manual | set hotkey.wayland_evdev=true, restart the daemon, hold the key (default KEY_RIGHTCTRL); note the matched device name in the log |
| `kde-hotkey-portal` | gap | (gap — recorded, not run) GlobalShortcuts portal hotkey backend not implemented in this build; Plasma 6.7.4+ changed shortcut descriptions — re-pin when implementing — compositor-bind --toggle is the primary path today |
| `kde-insert-eis` | gap | (gap — recorded, not run) EIS keysym insertion (libei-class protocol) is not implemented in this build — recorded as a known gap on every session |
| `kde-insert-non-us-layout` | manual | switch the session to a non-US layout (e.g. de), dictate symbols/punctuation-heavy text, verify the inserted characters match the layout |
| `kde-insert-paste-live` | manual | copy something to the clipboard first, then dictate >=300 chars; verify the full text lands once and the clipboard is restored afterwards |
| `kde-insert-terminal-live` | manual | dictate into konsole: a git-style phrase (trailing space), a long paste, and a spoken-send phrase; verify Enter is suppressed only in the terminal |
| `kde-insert-typed-live` | manual | dictate into a plain editor field (gedit/foot); text arrives at the caret and nowhere else |
| `kde-insert-wl-clipboard-tools` | auto | present: wl-copy, wl-paste |
| `kde-insert-wtype` | gap | (gap — recorded, not run) wtype (zwp_virtual_keyboard) cannot work on Plasma: KWin does not implement the protocol (KDE bug 512996) — ydotool (uinput) is the Plasma insertion path |
| `kde-insert-ydotool-tools` | auto | present: ydotool |
| `kde-lifecycle-autostart` | auto | `systemctl --user is-enabled sayit-ermano` |
| `kde-lifecycle-lock-unlock` | manual | lock the session (super/L, swaylock, gdm), verify the hotkey does NOT trigger while locked, unlock, verify it works again |
| `kde-lifecycle-sleep-wake` | manual | run `systemctl suspend`, wake the machine, then verify the hotkey still toggles and one dictation inserts; re-run this harness on the same session |
| `kde-surface-overlay-preview` | manual | start a take and watch the notification preview; grep the daemon log for x11-pill attempt noise (there must be none) |
| `kde-surface-settings-script` | manual | open Settings -> Wayland, press Copy, inspect the script, bind it and toggle dictation via the shortcut |

### sway — sway (wlroots)

| row id | kind | exact invoke line / check |
|---|---|---|
| `sway-baseline-daemon-status` | auto | `sayit-ermano status` |
| `sway-baseline-doctor` | auto | `sayit-ermano doctor` |
| `sway-baseline-first-dictation` | manual | focus an editor, press the session hotkey (or `sayit-ermano toggle`), dictate ~10 words, stop; polished text lands at the caret and the take shows in `sayit-ermano history` |
| `sway-hotkey-compositor-bind` | manual | sway config: bindsym $mod+d exec sayit-ermano-toggle, then swaymsg reload; press it -> dictation toggles |
| `sway-hotkey-evdev-ptt` | manual | set hotkey.wayland_evdev=true, restart the daemon, hold the key (default KEY_RIGHTCTRL); note the matched device name in the log |
| `sway-hotkey-portal` | gap | (gap — recorded, not run) no GlobalShortcuts backend exists in xdg-desktop-portal-wlr (issue #240) — the compositor-bind + --toggle path IS the primary hotkey path on sway |
| `sway-insert-eis` | gap | (gap — recorded, not run) EIS keysym insertion (libei-class protocol) is not implemented in this build — recorded as a known gap on every session |
| `sway-insert-non-us-layout` | manual | switch the session to a non-US layout (e.g. de), dictate symbols/punctuation-heavy text, verify the inserted characters match the layout |
| `sway-insert-paste-live` | manual | copy something to the clipboard first, then dictate >=300 chars; verify the full text lands once and the clipboard is restored afterwards |
| `sway-insert-terminal-live` | manual | dictate into foot: a git-style phrase (trailing space), a long paste, and a spoken-send phrase; verify Enter is suppressed only in the terminal |
| `sway-insert-typed-live` | manual | dictate into a plain editor field (gedit/foot); text arrives at the caret and nowhere else |
| `sway-insert-wl-clipboard-tools` | auto | present: wl-copy, wl-paste |
| `sway-insert-wtype-tools` | auto | present: wtype |
| `sway-insert-ydotool-tools` | auto | present: ydotool |
| `sway-lifecycle-autostart` | auto | `systemctl --user is-enabled sayit-ermano` |
| `sway-lifecycle-lock-unlock` | manual | lock the session (super/L, swaylock, gdm), verify the hotkey does NOT trigger while locked, unlock, verify it works again |
| `sway-lifecycle-sleep-wake` | manual | run `systemctl suspend`, wake the machine, then verify the hotkey still toggles and one dictation inserts; re-run this harness on the same session |
| `sway-context-matrix` | manual | with [context] enabled=true and provider="auto" in the config (restore afterwards!), work through B1-B4 + the A2-A6 equivalents of docs/dev/wayland-smoke-matrix.md and tick them there |
| `sway-surface-overlay-preview` | manual | start a take and watch the notification preview; grep the daemon log for x11-pill attempt noise (there must be none) |
| `sway-surface-settings-script` | manual | open Settings -> Wayland, press Copy, inspect the script, bind it and toggle dictation via the shortcut |

### hyprland — Hyprland (wlroots)

| row id | kind | exact invoke line / check |
|---|---|---|
| `hyprland-baseline-daemon-status` | auto | `sayit-ermano status` |
| `hyprland-baseline-doctor` | auto | `sayit-ermano doctor` |
| `hyprland-baseline-first-dictation` | manual | focus an editor, press the session hotkey (or `sayit-ermano toggle`), dictate ~10 words, stop; polished text lands at the caret and the take shows in `sayit-ermano history` |
| `hyprland-hotkey-compositor-bind` | manual | hyprland.conf: bind = SUPER, D, exec, sayit-ermano-toggle; press it -> dictation toggles |
| `hyprland-hotkey-evdev-ptt` | manual | set hotkey.wayland_evdev=true, restart the daemon, hold the key (default KEY_RIGHTCTRL); note the matched device name in the log |
| `hyprland-hotkey-portal` | gap | (gap — recorded, not run) Hyprland implements the GlobalShortcuts portal; a portal hotkey backend is not implemented in this build — compositor-bind --toggle is the primary path today |
| `hyprland-insert-eis` | gap | (gap — recorded, not run) EIS keysym insertion (libei-class protocol) is not implemented in this build — recorded as a known gap on every session |
| `hyprland-insert-non-us-layout` | manual | switch the session to a non-US layout (e.g. de), dictate symbols/punctuation-heavy text, verify the inserted characters match the layout |
| `hyprland-insert-paste-live` | manual | copy something to the clipboard first, then dictate >=300 chars; verify the full text lands once and the clipboard is restored afterwards |
| `hyprland-insert-terminal-live` | manual | dictate into kitty: a git-style phrase (trailing space), a long paste, and a spoken-send phrase; verify Enter is suppressed only in the terminal |
| `hyprland-insert-typed-live` | manual | dictate into a plain editor field (gedit/foot); text arrives at the caret and nowhere else |
| `hyprland-insert-wl-clipboard-tools` | auto | present: wl-copy, wl-paste |
| `hyprland-insert-wtype-tools` | auto | present: wtype |
| `hyprland-insert-ydotool-tools` | auto | present: ydotool |
| `hyprland-lifecycle-autostart` | auto | `systemctl --user is-enabled sayit-ermano` |
| `hyprland-lifecycle-lock-unlock` | manual | lock the session (super/L, swaylock, gdm), verify the hotkey does NOT trigger while locked, unlock, verify it works again |
| `hyprland-lifecycle-sleep-wake` | manual | run `systemctl suspend`, wake the machine, then verify the hotkey still toggles and one dictation inserts; re-run this harness on the same session |
| `hyprland-surface-overlay-preview` | manual | start a take and watch the notification preview; grep the daemon log for x11-pill attempt noise (there must be none) |
| `hyprland-surface-settings-script` | manual | open Settings -> Wayland, press Copy, inspect the script, bind it and toggle dictation via the shortcut |

### wayland — Wayland, unidentified compositor

| row id | kind | exact invoke line / check |
|---|---|---|
| `wayland-baseline-daemon-status` | auto | `sayit-ermano status` |
| `wayland-baseline-doctor` | auto | `sayit-ermano doctor` |
| `wayland-baseline-first-dictation` | manual | focus an editor, press the session hotkey (or `sayit-ermano toggle`), dictate ~10 words, stop; polished text lands at the caret and the take shows in `sayit-ermano history` |
| `wayland-hotkey-compositor-bind` | manual | bind sayit-ermano-toggle as a custom compositor shortcut; press it -> dictation toggles |
| `wayland-hotkey-evdev-ptt` | manual | set hotkey.wayland_evdev=true, restart the daemon, hold the key (default KEY_RIGHTCTRL); note the matched device name in the log |
| `wayland-hotkey-portal` | gap | (gap — recorded, not run) GlobalShortcuts portal support varies by compositor and is not implemented in this build |
| `wayland-insert-eis` | gap | (gap — recorded, not run) EIS keysym insertion (libei-class protocol) is not implemented in this build — recorded as a known gap on every session |
| `wayland-insert-non-us-layout` | manual | switch the session to a non-US layout (e.g. de), dictate symbols/punctuation-heavy text, verify the inserted characters match the layout |
| `wayland-insert-paste-live` | manual | copy something to the clipboard first, then dictate >=300 chars; verify the full text lands once and the clipboard is restored afterwards |
| `wayland-insert-terminal-live` | manual | dictate into a terminal: a git-style phrase (trailing space), a long paste, and a spoken-send phrase; verify Enter is suppressed only in the terminal |
| `wayland-insert-typed-live` | manual | dictate into a plain editor field (gedit/foot); text arrives at the caret and nowhere else |
| `wayland-insert-wl-clipboard-tools` | auto | present: wl-copy, wl-paste |
| `wayland-insert-wtype-tools` | auto | present: wtype |
| `wayland-insert-ydotool-tools` | auto | present: ydotool |
| `wayland-lifecycle-autostart` | auto | `systemctl --user is-enabled sayit-ermano` |
| `wayland-lifecycle-lock-unlock` | manual | lock the session (super/L, swaylock, gdm), verify the hotkey does NOT trigger while locked, unlock, verify it works again |
| `wayland-lifecycle-sleep-wake` | manual | run `systemctl suspend`, wake the machine, then verify the hotkey still toggles and one dictation inserts; re-run this harness on the same session |
| `wayland-surface-overlay-preview` | manual | start a take and watch the notification preview; grep the daemon log for x11-pill attempt noise (there must be none) |
| `wayland-surface-settings-script` | manual | open Settings -> Wayland, press Copy, inspect the script, bind it and toggle dictation via the shortcut |

<!-- wayland-smoke rows END -->

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
