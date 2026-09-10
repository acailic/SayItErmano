Plan the implementation of input-device monitoring with automatic microphone switching - UPSTREAM-TRACKING row "Mic priority list, drag-to-reorder, device history" and docs/STATUS.md "Input-device monitoring / Bluetooth auto-switch" (MPRIS media pause already landed; this is the remaining half).

STATUS: SHIPPED

<!-- shipped in 60c4d60 -->

Today: the tray menu's Microphone submenu (fluidvoice/tray.py list_microphones + daemon._set_device) lists pactl sources and switching persists recording.device, but nothing reacts when devices appear/disappear - a Bluetooth headset connecting mid-session never becomes the mic unless the user re-opens the menu, and a disconnect mid-dictation kills the take.

Scope v1:
1) Mic priority list config: recording.mic_priority = [] (ordered source-name patterns, e.g. ["bluez", "usb-cam"]; matched case-insensitively as substrings, first match wins) + whitelist + a rows editor in the GTK Dictation section (add/remove/reorder with up/down buttons - simpler and safer than drag-and-drop in v1) + tray submenu shows the priority-ordered list with the effective device checkmarked.
2) Device monitoring: a daemon-side watcher thread (fluidvoice/media.py or a new fluidvoice/micmon.py - plan picks) polling `pactl subscribe`-style events OR a 3 s `pactl list short sources` diff poll (prefer the poll: no long-lived subprocess parsing, works everywhere); on change it (a) logs, (b) if the current device vanished and a priority match exists -> auto _set_device(best_match) + notification "microphone switched to X"; (c) if recording.device is "" (auto) nothing is switched - auto means system default, we do not fight it.
3) Mid-dictation safety: if the active device disappears WHILE recording, do not switch mid-take - finish the take on the still-open stream (pw-record keeps the node), and apply the reselection at the next idle moment; document this behavior in the config comment.
4) Bluetooth: nothing Bluetooth-specific beyond name-pattern matching (bluez sources are just sources); the docs/README mention the pattern example.

Where: fluidvoice/config.py (mic_priority + comments), new module for the watcher, fluidvoice/daemon.py (watcher lifecycle in run()/shutdown(), auto-switch logic reusing _set_device), fluidvoice/tray.py (priority ordering in the menu), fluidvoice/gtkui/settings_window.py (editor rows), tests. Suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (green at the then-current HEAD).

Done means: a phased, file-level plan under `specs/` a builder can implement without questions - each phase leaves the suite green; unit tests with a fake pactl runner (poll diff detects add/remove, priority matching order, auto-switch only when configured device vanishes, never while recording, ""-auto never switches, watcher stops cleanly) and config coercion tests for the list; GTK smoke like existing ones.

Out of scope: drag-and-drop reordering, per-app mic profiles,PipeWire native API (stay on pactl), Wayland audio portals, UI notifications beyond the existing notify path.
