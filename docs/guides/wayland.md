# Wayland support

Part of the [documentation index](../README.md). The capability
matrix, the insertion tools, and what X11 gets that Wayland cannot
(and why). The release-evidence matrix for developers lives in
[../dev/wayland-smoke-matrix.md](../dev/wayland-smoke-matrix.md).

| Capability | X11 | Wayland |
|---|---|---|
| Global hotkey | `XGrabKey` (toggle + hold) | DE custom shortcut → the generated `sayit-ermano-toggle` script (Settings → Wayland prints the per-GNOME/KDE/COSMIC steps; `sayit-ermano doctor` too). Optional **evdev push-to-talk**: hold a physical key read from `/dev/input` (`hotkey.wayland_evdev`, privileged — `input` group + `pip install 'sayit-ermano[wayland]'`) |
| Text insertion | `xdotool type` | `wtype` (wlroots/KDE — GNOME has no virtual-keyboard protocol) or `ydotool` (any compositor; needs `ydotoold` running + `/dev/uinput` access). `insertion.wayland_tool` = `auto\|wtype\|ydotool` |
| Paste mode | verified read-observation + clipboard restore | `wl-clipboard` + fixed settle + restore — **paste verification is impossible on Wayland** (no client can observe another client's selection reads), and no clipboard-manager hygiene markers can be advertised |
| Live preview | X11 pill | notification bubble (the layer-shell pill on wlroots compositors is future work; no pill on GNOME-Wayland) |
| Tray | StatusNotifierItem | StatusNotifierItem (same) |
| App hints / terminal quirks | WM_CLASS via `xdotool` | unavailable (AT-SPI is future work) — `general.terminal_apps` quirks are inert |

Install the tools:

```bash
sudo apt install wtype wl-clipboard        # sway/wlroots, KDE
sudo apt install ydotool wl-clipboard       # any compositor incl. GNOME:
sudo systemctl enable --now ydotool         # ydotoold must run; /dev/uinput
sudo usermod -aG input $USER                # only for evdev push-to-talk
```

`sayit-ermano doctor` prints the per-capability matrix with per-tool
found/missing on your session, and `sayit-ermano status` carries the same
matrix (additive `session`/`capabilities` keys in the JSON).
