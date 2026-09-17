Plan the implementation of a system-following / light theme for the preview overlay - ROADMAP "Platform polish" ("system/light overlay theme") and the most visible polish gap: the pill overlay is dark-only today, which looks wrong on a light desktop theme and on light wallpapers.

STATUS: OPEN

Today: fluidvoice/overlay.py renders pill frames as RGBA images with a
hard-coded dark pill family (Pillow renderer - `_paint`, `_gloss_border`,
chip colors around lines ~1276-1281); mode accents (dictate/rewrite/
command) are the only color variation; `recording.preview_overlay_size`
(pill/small/medium/large) is the sole style key. A gsettings probe
pattern already exists in the module (`_gsetting_animations` reads
org.gnome.desktop.interface) so the system-preference read has a local
precedent.

Scope:
1) Two palettes: the current dark pill (default, unchanged pixel-for-
   pixel - it is the macOS look) and a light pill (near-white surface,
   dark text, same accent hues; contrast ratios documented in the plan
   for text-on-pill and waveform-on-pill).
2) `recording.preview_overlay_theme` = "dark" | "light" | "system"
   (default "dark" for zero-change upgrades); "system" resolves via the
   color-scheme preference (org.gnome.desktop.interface color-scheme,
   prefer-dark) with a gsettings watch OR a cheap per-take re-read -
   pick one and justify; resolution result shows in `status`.
3) Notification-preview fallback (Wayland) uses the same theme
   resolution so the two paths never disagree.
4) Every state must be legible in both palettes: recording waveform +
   streaming text, processing label, downloading hint, cancel/send
   chips, and the amber/green send-success band shift.

Where: fluidvoice/overlay.py (palette table + `_paint` family),
fluidvoice/config.py (`_spec` + enum + whitelist), status/doctor
surface the resolved theme; docs/guides/configuration.md regenerates.

Done means: a phased plan leaving the suite green each phase; unit
tests asserting the palette table covers every (palette × state)
combination used by `_paint` (no dark-on-dark or light-on-light cell),
theme resolution for the three modes (fake gsettings runner, following
the micmon watcher test shapes), and config coercion; a golden-image
test per palette at one fixed frame (Pillow is deterministic - the
existing renderer tests show the pattern).

Out of scope: user-custom color pickers (accents stay the three mode
hues), the tray icon, a GTK widget overlay (stays a Pillow-composited
window).
