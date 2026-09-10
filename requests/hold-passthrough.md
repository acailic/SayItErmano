Plan the implementation of hold-mode key passthrough - docs/STATUS.md line "Hold-mode key passthrough (other keys interrupt, not swallow)". Small, focused fix.

STATUS: SHIPPED

<!-- shipped in d99caab -->

Today: hotkey "hold" mode (fluidvoice/hotkey.py _hold_cycle) grabs the WHOLE keyboard from hotkey press to release so it can see the release; every other key pressed during the hold is swallowed (GrabModeAsync on keyboard means events come to us and we discard everything that is not the hotkey release or Escape). Upstream macOS push-to-talk lets you keep typing while holding the dictation modifier.

Scope:
1) During a hold cycle, REPLAY non-hotkey, non-Escape key events to the focused application instead of swallowing them. Implementation direction (plan verifies feasibility against python-xlib): replace the full-keyboard grab with a paired XGrabKey on press + polling/xfree... or keep the keyboard grab but re-send each swallowed event with XSendEvent/XTEST (xdotool-free; python-xlib can do XTEST via the XTEST extension if present, else XSendEvent with send_event flag) - the plan must pick the approach that does not risk duplicate keys or stuck modifiers and degrades to today's swallow behavior on failure.
2) Modifier hygiene: modifier-only presses during hold (shift/ctrl) must reach the app and not break the release detection.
3) If replay is not reliably achievable, the fallback scope is: swallow only KEYSYMS that can produce characters (printable keys pass through via replay, function/modifier keys still swallowed) - document whichever lands honestly in the config comment and docs/STATUS.md.
4) Config: no new keys; behavior improves in place. doctor unaffected.

Where: fluidvoice/hotkey.py (_hold_cycle + maybe an XTEST helper), tests/test_cli_ui_hotkey.py (unit: event classification - which events get replayed; the replay helper against a fake display object). Suite: `.venv/bin/python -m pytest -q tests --ignore=tests/integration` (green at then-HEAD). A desktop-marked live test may verify no key loss during a synthetic hold (xdotool), following tests/integration/test_live_x11.py patterns - optional but preferred.

Done means: a phased plan under `specs/` a builder can implement without questions - each phase leaves the suite green; unit tests for the event-classification and replay-invocation logic with a fake display; the chosen approach's failure mode (replay unavailable) leaves hold mode exactly as today.

Out of scope: Wayland, remapping keys, per-app hold behavior, changing default hotkey.
