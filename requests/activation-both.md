STATUS 2026-09-06: SHIPPED in 86fad3e - hotkey.mode 'both' with a 250 ms tap/hold disambiguation window, _hold_cycle refactored into _free_keyboard/_hold_until_release helpers (incl. the passthrough-restore fix). This brief is done; do not re-open.

Activation mode "both" (B3, macOS parity: upstream v1.5.14 ships Toggle / Hold / Automatic; the "both" ask is the common hybrid - short tap toggles, press-and-hold talks). Today config.py hotkey.mode is toggle | hold (hold-passthrough shipped earlier: keys typed during a hold pass through to the focused app natively, keyboard freed, swallowed only if freeing fails; modifier-only keys work in toggle mode only). ROADMAP B3 row: "activation mode both (tap toggles, hold talks)".

Scope:
1) hotkey.mode gains "both": on press start a hold-candidate timer; release before hotkey.both_tap_s (new key, default 0.30, float seconds, 0.05-1.0 validated) = tap -> toggle semantics (start recording if idle; stop+transcribe if recording); press held >= both_tap_s while idle = start recording immediately at threshold crossing (so speech is never delayed by the timer - the take MUST open no later than both_tap_s after press; document that trade-off in the config comment), release = stop + transcribe. While recording via a tap, a later press+hold stops at release (tap) or immediately when the threshold elapses (hold) - planner picks the least surprising of the two and pins it in a test.
2) The hold path inherits the existing passthrough design (keys typed during an active hold pass through; swallow only on free-failure) and the existing constraint that modifier-only keys need the toggle fallback: in "both" mode a modifier-only key degrades to pure toggle with a doctor note (no silent half-mode).
3) Interactions: cancel_key (Escape while recording) unchanged; mouse push-to-talk and wayland_evdev hold listeners unchanged; the immediate-stop/VAD auto-stop and segmented preview treat a both-mode stop exactly like a hold release.
4) Doctor: activation-mode line reflects "both" (+ tap threshold, + modifier-only degradation note when applicable); Settings hotkey mode dropdown gains "both" with the tap-threshold spinbutton (0.05-1.0 s) shown only for both.
5) Tests: a state-machine table with a fake clock covering: tap-to-start, tap-to-stop, hold-to-talk start at threshold (assert take-open latency <= both_tap_s), release-to-stop, tap while holding-recording, cancel mid-hold, modifier-only degradation, config validation bounds.

Where: fluidvoice/hotkey.py, fluidvoice/config.py, fluidvoice/daemon.py (stop-path unification if needed), fluidvoice/doctor.py, fluidvoice/gtkui/settings_window.py, tests/test_activation_both.py (new).

Done means: a phased plan under specs/ where each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green; live X11 smoke with a non-modifier key: tap starts/stops, hold records with passthrough typing working, no first-word loss at hold start (first-word probe test still green); toggle and hold modes byte-identical in behavior (existing suite unchanged).

Out of scope: Automatic/VAD-start mode, per-shortcut activation modes, wayland gesture/shortcut work, changing passthrough internals beyond wiring.

Deliverable constraint: the planning phase produces a plan document under specs/ only - planning never edits implementation, config, docs, or test files (the builder phase owns all code changes).
