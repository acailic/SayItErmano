# Wayland support

Part of the [documentation index](../README.md). The capability
matrix, the insertion tools, and what X11 gets that Wayland cannot
(and why). The release-evidence matrix for developers lives in
[../dev/wayland-smoke-matrix.md](../dev/wayland-smoke-matrix.md).

| Capability | X11 | Wayland |
|---|---|---|
| Global hotkey | `XGrabKey` (toggle + hold) | **GlobalShortcuts portal** (`hotkey.wayland_portal`, default on — GNOME 48+, Plasma 5.27+, Hyprland; the portal's Activated/Deactivated edges give native hold-PTT). No portal backend (sway, COSMIC) or portal off: DE custom shortcut → the generated `sayit-ermano-toggle` script (Settings → Wayland prints the per-GNOME/KDE/COSMIC steps; `sayit-ermano doctor` too). Optional **evdev push-to-talk**: hold a physical key read from `/dev/input` (`hotkey.wayland_evdev`, privileged — `input` group + `pip install 'sayit-ermano[wayland]'`) |
| Text insertion | `xdotool type` | `eis` (**RemoteDesktop portal + libei/EIS**, default-first on GNOME 46+/Plasma where the portal probe passes; one-time consent dialog, `pip install 'sayit-ermano[portal]'` for the host install), `wtype` (needs the `zwp_virtual-keyboard` protocol — wlroots/Hyprland, not GNOME/KDE) or `ydotool` (any compositor; needs `ydotoold` running + `/dev/uinput` access). `insertion.wayland_tool` = `auto\|eis\|wtype\|ydotool`; **auto picks by runtime protocol probe, never by desktop name**. Out-of-layout Unicode (emoji, other scripts) always falls back to clipboard + Ctrl+V — EIS cannot upload keymaps |
| Paste mode | verified read-observation + clipboard restore | `wl-clipboard` + fixed settle + restore — **paste verification is impossible on Wayland** (no client can observe another client's selection reads), and no clipboard-manager hygiene markers can be advertised |
| Live preview | X11 pill | notification bubble (the layer-shell pill on wlroots compositors is future work; no pill on GNOME-Wayland) |
| Tray | StatusNotifierItem | StatusNotifierItem (same) |
| App hints / terminal quirks | WM_CLASS via `xdotool` | unavailable (AT-SPI is future work) — `general.terminal_apps` quirks are inert |

Install the tools:

```bash
sudo apt install wl-clipboard              # clipboard everywhere
sudo apt install wtype wl-clipboard        # sway/wlroots, Hyprland
sudo apt install ydotool wl-clipboard       # any compositor incl. GNOME:
sudo systemctl enable --now ydotool         # ydotoold must run; /dev/uinput
sudo usermod -aG input $USER                # only for evdev push-to-talk
pip install 'sayit-ermano[portal]'          # eis + portal hotkey (host installs)
```

`sayit-ermano doctor` prints the per-capability matrix with per-tool
found/missing on your session, and `sayit-ermano status` carries the same
matrix (additive `session`/`capabilities` keys in the JSON).
