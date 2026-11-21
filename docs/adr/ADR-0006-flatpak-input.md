# ADR-0006: Flatpak input architecture — GlobalShortcuts portal for the hotkey, RemoteDesktop/EIS for insertion

Date: 2026-11-21
Status: Accepted — the client implementation landed with #10
(`fluidvoice/portal_hotkey.py`, `fluidvoice/eis.py`,
`fluidvoice/portal_eis.py`, `fluidvoice/input_probes.py`); the
live-session verification rows for the matrix are tracked in #13.

## Context

Flathub is the distribution funnel for the paid launch (issue #12), but
the Flatpak sandbox removes the two mechanisms the daemon relies on:
the X11 global grab (XGrabKey) and typed insertion (xdotool). The
verification pass of 2026-11-21
([research](../research/2026-11-21-productization-verification.md))
established, at primary sources, what works inside a sandbox today:

- **GlobalShortcuts portal v2** is the sanctioned Wayland hotkey:
  `Activated`/`Deactivated` deliver press and release (native
  push-to-talk). Backends: GNOME 48+, Plasma 5.27+, Hyprland. **sway and
  COSMIC have none** (xdg-desktop-portal-wlr#240, open since 2022).
  InputCapture is not a hotkey trigger (pointer barriers only).
- **RemoteDesktop portal + libei/EIS** is the sanctioned injection path
  (GNOME ≥46 via `ConnectToEIS`, KWin EIS backend; wlroots does not
  implement RemoteDesktop). Consent persists on portal ≥1.21, dialog
  per session on older. EIS cannot upload keymaps, so out-of-layout
  Unicode needs a clipboard fallback; `ei_text` UTF-8 (libei 1.6) is
  the future fix.
- **zwp_virtual_keyboard_manager_v1** (wtype) works on wlroots-family
  compositors only — mutter will not implement it (mutter#1974), KWin
  does not (bug 512996).
- **X11** proper keeps working via XGrabKey + xdotool
  (`--socket=fallback-x11`), but Xwayland ≥23.2 routes XTEST through
  the RemoteDesktop portal with a prompt, and GNOME 49+ drops X11
  sessions — X11 is legacy, not the escape hatch.
- Precedent: Handy's Flatpak migration ships exactly this stack
  (GlobalShortcuts PR #1287, RemoteDesktop text pipeline PR #689,
  `--socket=wayland` only, `handy --toggle` fallback); Vocalinux's
  Flathub submission was closed with the reviewer directing them to the
  GlobalShortcuts portal.
- CUDA has no verified Flathub precedent and a breakage history;
  Vulkan via the runtime GL extension does work (Blender pattern).

## Decision

Per session type, the input path and its fallback ladder are:

| Session | Hotkey (primary → fallback) | Insertion (primary → fallback) |
|---|---|---|
| GNOME Wayland 48+ | GlobalShortcuts portal (hold PTT) → GNOME custom shortcut running `sayit-ermano --toggle` | RemoteDesktop/EIS keysyms → clipboard + EIS-sent Ctrl+V (out-of-layout Unicode) |
| KDE Wayland 5.27+ | GlobalShortcuts portal (re-bind every session) → compositor bind → `--toggle` | RemoteDesktop/EIS → clipboard + Ctrl+V |
| sway / wlroots | compositor `bindsym` → `sayit-ermano --toggle` (no portal backend exists) | zwp_virtual_keyboard (wtype) — runtime protocol probe gates it → wl-copy + Ctrl+V |
| Hyprland | GlobalShortcuts portal (xdph) or hyprctl bind → `--toggle` | zwp_virtual_keyboard (wtype) → clipboard + Ctrl+V |
| X11 (true session) | XGrabKey (unchanged) | xdotool typed / verified paste (unchanged) |

Supporting decisions:

1. The **CLI-toggle fallback is first-class**: `sayit-ermano --toggle`
   and `--cancel` over the existing Unix control socket (ADR-0001),
   bindable in any DE. It is the only zero-dependency cross-DE path and
   the primary on sway.
2. **wtype selection is gated on a runtime protocol probe**, never on
   desktop name.
3. **Flatpak GPU path is whisper.cpp Vulkan / CPU int8**; CUDA stays a
   host-build advantage and is out of scope for the sandbox build.
4. **Default manifest permissions**: `--socket=wayland`,
   `--socket=pulseaudio`, `--device=dri`, `--share=network` (model
   download). No `--device=all` by default — ydotool/uinput is an
   opt-in, documented override.
5. App ID `io.github.acailic.SayItErmano` (com.github.* is rejected for
   new apps; renames force resubmission).
6. `doctor` gains a Flatpak mode that probes portal interfaces and
   versions, detects the session/compositor family, verifies EIS
   connect, and reports which rung of the ladder is active.

## Consequences

- The daemon must run without the systemd user unit inside the sandbox
  (the GTK app process owns it); XDG autostart replaces the unit.
- Consent UX differs by portal version (≥1.21 persisted vs per-session
  dialog) — onboarding and docs must show both.
- Tracked follow-ups, not gates: `ei_text` UTF-8 insertion when
  compositor adoption lands (follow-up ticket), `xx-hotkey-v1` once
  backends ship (native triggered/released semantics).
- The spike (#10) validates each row of the table on real sessions
  before the manifest work (#11) and the Flathub submission (#12);
  Vocalinux's rejection shows reviewers check exactly this.
