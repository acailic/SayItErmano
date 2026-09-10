Hotkey-grab self-healing - the daemon must never sit "ready" with a dead hotkey. Live evidence 2026-09-04: at login a second daemon (stale fluidvoice-linux 0.2.1 deb autostart) won the XGrabKey race for Right_Control; this daemon's 8 lock-mask grabs were all refused (8x BadAccess, GrabKey, major_opcode 33) yet startup logged "hotkey Right_Control = keycode 105 ... ready" and kept running silently keyless until manually restarted. The stale deb is being purged from this machine, but the class is general: any second grab holder (WM rebind, another dictation tool, a test daemon) reproduces it.

STATUS: SHIPPED

<!-- shipped in 23e2567 -->

Today: fluidvoice/hotkey.py grabs the hotkey once in HotkeyListener.setup() via _grab() (one grab_key per _LOCK_MASKS combo, 8 total) and never retries; python-xlib delivers the BadAccess through the DEFAULT error handler (it prints "X protocol error:" - it does NOT raise through the grab_key call), so the failure is invisible to the listener and to everything above it. Only the cancel key self-heals (_sync_cancel_grab, hardened in c720b25, retried each 10 ms poll loop iteration). Grab health appears nowhere: not in doctor.py, not in the tray tooltip (tray.py), not in status.

Scope:
1) Error routing: install a python-xlib error handler for the listener's Display (Xlib.error.CatchError or a custom handler set via Display.set_error_handler) so grab refusals are captured as data, not printed; track per-combo grab state (keycode x lock-mask -> grabbed bool).
2) Self-heal: extend the existing poll loop (_run in hotkey.py) to re-attempt missing combos every loop tick that has already proven cheap (piggyback _sync_cancel_grab's cadence); after the grab succeeds, log one line "hotkey grab recovered". Cap: if a combo is still refused after the Nth distinct attempt (N=10), WARN once per recording-idle period instead of every tick.
3) Startup honesty: if the initial grab is refused, log WARN "hotkey 'Right_Control' grab refused - held by another client, will retry" AND send a desktop notification (existing ui.notify path, as used by the hotkey-unavailable branch in daemon.py _start_hotkey).
4) Surfaces: daemon status (control socket "status" response) gains "hotkey_grabbed": true/false; doctor.py gains a line reporting the last-known grab state from the daemon if reachable (query the socket, same as other doctor daemon checks) or "unknown (daemon down)"; tray tooltip appends " - hotkey blocked!" when unhealthy (tray.py tooltip already shows state + hotkey).
5) Tests: unit - a fake Display whose grab_key records calls and triggers the error path (the handler routing and retry loop logic must be testable without a server); integration (tests/integration/test_live_x11.py pattern) - a deliberate conflicting grab on the test hotkey key (F9 in TEST_CONFIG), assert WARN + recovery after the conflicting grab is released.

Where: fluidvoice/hotkey.py (error handler, per-combo state, retry), fluidvoice/daemon.py (status field, startup notification hook), fluidvoice/tray.py (tooltip suffix), fluidvoice/doctor.py (line), tests/test_hotkey_grab.py (new unit), tests/integration/test_live_x11.py (recovery case appended).

Done means: a phased plan under specs/ where each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green; with a conflicting holder the daemon logs the WARN + notification, "status" reports hotkey_grabbed false, and within ~1 s of the holder releasing, the grab is re-taken and status flips true without a daemon restart; doctor prints the state; the offline suite has unit coverage for the retry state machine.

Out of scope: Wayland hotkey binding, mouse-button hotkeys (separate request), changing the cancel-key logic, removing the python-xlib default handler globally (other X users in this process: overlay.py, insertion.py), any UI beyond the tooltip/status/doctor/notification surfaces named above.
