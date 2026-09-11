# App × desktop support matrix (phase 0)

- Date: 2026-09-12
- Status: **DRAFTED — every cell UNTESTED; phase 0 does not run these**
- Origin: [product excellence plan](../research/2026-09-11-product-excellence-and-monetization-plan.md)
  phase 0 item 4. Extends
  [wayland-smoke-matrix.md](wayland-smoke-matrix.md) (the context/AT-SPI
  parity gate, still NOT DONE) into a full application × session matrix
  for the phase 1 insertion audit.
- Tier legend:
  - **REQ-PARITY** — inherited from wayland-smoke-matrix.md; required
    before declaring Wayland parity / flipping `context.enabled` true.
  - **EXTENDED** — broader coverage for the phase 1 audit and any
    future "supported desktops" claim; does not block the context
    default flip, but an untested EXTENDED cell may not be advertised
    as supported.

## Housekeeping (applies to every live run)

Same rules as wayland-smoke-matrix.md: this desktop runs the
production daemon live — use the documented two-step (stop the unit,
run the repo daemon, `systemctl --user start sayit-ermano`
afterwards) and restore every config key you flip in
`~/.config/sayit-ermano/config.toml`. Record date + commit SHA +
machine per matrix run (append a dated section at the bottom).

## Insertion method legend

From `fluidvoice/insertion.py` and the shipped wayland-session work:

| method | X11 session | Wayland session |
|---|---|---|
| typed | `xdotool type` | `wtype` (default) / `ydotool` fallback |
| paste | `xclip` + Ctrl+V, read-observation verified, clipboard restored | `wl-copy` + Ctrl+V, fixed-delay settle (read-observation impossible on Wayland), clipboard restored |
| paste in terminals | Ctrl+**Shift**+V (`insertion.terminal_paste_key`) | same |

Per-app profiles (`[profiles]` rules, `general.terminal_apps`) pick
typed/paste and terminal quirks per app; long text and text starting
with `-` must paste (tool leading-dash hazard).

## The per-app check (pattern for every cell)

Each cell is exercised by the applicable numbered steps from
[wayland-smoke-matrix.md](wayland-smoke-matrix.md) (A2–A15, B1–B4,
C1–C3), plus this minimal 3-step check where no numbered step covers
the app (covers typed + paste + terminal/spoken-send where relevant):

1. **Empty field**: dictate a 10–20 word utterance into a fresh field;
   text arrives at caret, nothing elsewhere.
2. **Mid-sentence**: type `mid sentence` (no trailing space), dictate;
   one leading space, correct continuation capitalization after `.`
   + newline.
3. **Paste path**: force paste (long text ≥300 chars, or leading `-`);
   full text lands once, no duplication, clipboard restored
   afterwards; in terminals verify Ctrl+Shift+V and trailing-space
   handling (A7), spoken-send suppression (A8/A9/A10).

Plus the cross-cutting checks where the cell is chat/editor (not a
plain text field): focus change mid-take (A15), and — on Wayland with
`context.enabled` — the privacy sweep (A13/B4).

## Matrix 1 — GNOME Wayland (Ubuntu 24.04 / GNOME 46+)

REQ-PARITY cells mirror Matrix A of wayland-smoke-matrix.md.

| app | class | methods | known risks | steps | tier | status |
|---|---|---|---|---|---|---|
| gnome-terminal / kgx / ptyxis | terminal | typed; paste = Ctrl+Shift+V | bracketed-paste mangling; autocomplete trailing space; Enter handling | A7, A8, A10 + check 3 | REQ-PARITY | UNTESTED |
| gedit / gnome-text-editor | GTK editor | typed; paste | selection read vs caret landing (A5) | A2–A5, A11–A14 | REQ-PARITY | UNTESTED |
| nautilus search field | GTK | typed | GAAV lowercase first letter, trailing period stripped | A6 | REQ-PARITY | UNTESTED |
| Firefox | browser | typed; paste | E10s a11y must be ON for context identity; slow clipboard read can miss restore window | A1–A15 + check 1–3 | REQ-PARITY | UNTESTED |
| VS Code | Electron editor | typed; paste | Electron AT-SPI often disabled by default; IME/keystroke timing; keybinding conflicts | A1–A15 + check 1–3 | REQ-PARITY | UNTESTED |
| Chromium / Chrome | browser | typed; paste | XWayland vs native modes differ; clipboard-manager interference | check 1–3 | EXTENDED | UNTESTED |
| Slack | Electron chat | typed; paste | Electron focus quirks; clipboard-manager snapshot of dictation; Enter = send (spoken-send hazard) | check 1–3 + A15 | EXTENDED | UNTESTED |
| Discord | Electron chat | typed; paste | as Slack; overlay/permissions dialogs steal focus mid-take | check 1–3 + A15 | EXTENDED | UNTESTED |
| Telegram | Qt chat | typed; paste | Qt input method timing; Enter = send | check 1–3 + A15 | EXTENDED | UNTESTED |
| LibreOffice Writer | office | typed; paste | slow focus → paste-after-restore data loss (insertion-hardening mode (a)); autocorrect rewrites inserted text | check 1–3 | EXTENDED | UNTESTED |
| kate / konsole (on GNOME) | Qt | typed; paste | Qt under GNOME a11y bus | B-style + check 1–3 | EXTENDED | UNTESTED |

## Matrix 2 — sway (wlroots; wtype path)

REQ-PARITY cells mirror Matrix B. Same app set where installable.

| app | class | methods | known risks | steps | tier | status |
|---|---|---|---|---|---|---|
| foot | terminal | typed (wtype); paste = Ctrl+Shift+V | minimal session: no AT-SPI bus → context degrades (B3 must be documented) | B1–B4 + check 3 | REQ-PARITY | UNTESTED |
| kitty | terminal | typed; paste | as foot | B1–B4 + check 3 | REQ-PARITY | UNTESTED |
| firefox | browser | typed; paste | native Wayland vs MOZ_ENABLE_WAYLAND; clipboard managers absent by default | B1–B4 + check 1–3 | REQ-PARITY | UNTESTED |
| nautilus / GTK app | GTK | typed | GAAV behavior; search-field role | B1 + A6-equivalent | REQ-PARITY | UNTESTED |
| VS Code | Electron | typed; paste | XWayland Electron → xdotool-under-Xwayland identity quirks; wtype vs XWayland windows | check 1–3 + B2 | EXTENDED | UNTESTED |
| Slack / Discord / Telegram | chat | typed; paste | as GNOME matrix; Enter = send | check 1–3 + A15 | EXTENDED | UNTESTED |
| LibreOffice Writer | office | typed; paste | as GNOME matrix | check 1–3 | EXTENDED | UNTESTED |

## Matrix 3 — KDE Plasma Wayland

All EXTENDED for the parity gate, but required before advertising KDE
support. Insertion: wtype/ydotool; paste via wl-clipboard.

| app | class | methods | known risks | steps | tier | status |
|---|---|---|---|---|---|---|
| konsole | terminal | typed; paste = Ctrl+Shift+V | **Klipper** clipboard manager snapshots dictation text (privacy/clutter) unless hygiene holds | check 1–3 | EXTENDED | UNTESTED |
| kate / kwrite | Qt editor | typed; paste | Klipper; Qt IME | check 1–3 | EXTENDED | UNTESTED |
| Firefox | browser | typed; paste | Klipper; a11y under KDE | check 1–3 | EXTENDED | UNTESTED |
| VS Code | Electron | typed; paste | Electron + Klipper; Wayland flags | check 1–3 | EXTENDED | UNTESTED |
| Slack / Discord / Telegram | chat | typed; paste | Klipper + Enter = send | check 1–3 + A15 | EXTENDED | UNTESTED |
| LibreOffice Writer | office | typed; paste | as above | check 1–3 | EXTENDED | UNTESTED |

## Matrix 4 — X11 sessions (GNOME Xorg, XFCE)

Matrix C of wayland-smoke-matrix.md (context regression control,
REQ-PARITY) runs on the maintainer's GNOME X11 desktop. The extended
cells below broaden application coverage; XFCE additionally probes
another WM/clipboard-manager combination.

| app | class | methods | known risks | steps | tier | status |
|---|---|---|---|---|---|---|
| (context regression set) | — | typed/paste | — | C1–C3 | REQ-PARITY | UNTESTED |
| gnome-terminal + gedit | GTK | typed; paste | baseline X11 path (read-observation verified paste) | C1–C3 + check 1–3 | REQ-PARITY | UNTESTED |
| Firefox / Chromium | browser | typed; paste | clipboard-indicator GNOME extension snapshots paste flash | check 1–3 | EXTENDED | UNTESTED |
| VS Code | Electron | typed; paste | focus timing; extension keybindings | check 1–3 | EXTENDED | UNTESTED |
| Slack / Discord / Telegram | chat | typed; paste | Enter = send; clipboard managers | check 1–3 + A15 | EXTENDED | UNTESTED |
| LibreOffice Writer | office | typed; paste | slow-focus paste loss; autocorrect | check 1–3 | EXTENDED | UNTESTED |
| XFCE: xfce4-terminal, mousepad, **Clipman** | GTK | typed; paste | third clipboard-manager implementation to verify hygiene against | check 1–3 | EXTENDED | UNTESTED |

## Cross-cutting risks to watch in every session

- **Clipboard managers** (GNOME clipboard-indicator, Klipper,
  Clipman, CopyQ): dictation text must not persist in their history
  after a paste-mode insertion.
- **Focus changes mid-take** (A15): quirks follow the window focused
  at insertion; zero wrong-window typing.
- **Enter/spoken-send**: suppression only in terminals (A8–A10); in
  chat apps an unsuppressed Enter SENDS — observe, don't assume.
- **Slow-focus apps**: paste completing after clipboard restore
  (insertion-hardening failure mode (a)); LibreOffice and Electron
  are the prime suspects.
- **Text starting with `-`**: must paste on every tool (xdotool/wtype
  parse it as an option).

## Recording results

Append a dated section per run (like wayland-smoke-matrix.md §Runs):
date, commit SHA, machine, session type, per-cell ✓/✗ with one-line
evidence. A REQ-PARITY cell failing blocks the context default flip
(see wayland-smoke-matrix.md §Definition of parity); an EXTENDED cell
failing blocks only advertising that combination as supported.

### Runs

_(none yet — first run pending)_
