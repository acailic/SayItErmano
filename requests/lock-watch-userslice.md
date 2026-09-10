Lock-watch session resolution must work when the daemon runs under the systemd USER unit - live bug observed 2026-09-05 02:50 on the daily-driver machine: daemon started via `systemctl --user start sayit-ermano` logs "lock watch: session lookup failed (DBusException: org.freedesktop.login1.NoSessionForPID: PID 1288740 does not belong to any known session)" then "lock watch unavailable (no logind session for this process - headless?)" - pause-when-locked is inert.

STATUS: SHIPPED

<!-- shipped in 87f2b2c -->

Cause: processes under the user manager live in user.slice (user@1000.service), NOT in a session-XX.scope, so resolving the logind session BY THE DAEMON'S OWN PID (GetSessionByPID(self_pid) or the session D-Bus path for it) finds nothing. The lookup works only for session-scoped launches (the old GNOME autostart). The daemon may be started both ways; lock suppression must work in both.

Scope:
1) Resolution fallback in the lock watcher (fluidvoice/daemon.py lock-watch section from commit c2a95f6): when the daemon's own PID has no session, list sessions via the logind manager (org.freedesktop.login1 ListSessions) and select the active graphical session of the daemon's own user (UID match; prefer state=active, then type=desktop/x11/wayland, lowest FIFO for ties). Subscribe to that session's Lock/Unlock signals exactly as today; re-resolve on the session closing (dbus NameOwnerChanged/session-removed signal or a lazy re-resolve on next lookup) so logout/login swaps the watched session.
2) PrepareForSleep via the MANAGER signal (already manager-level if implemented that way - keep it) stays as the belt-and-braces path when no graphical session exists at all (genuinely headless): then and only then log the current "unavailable (headless)" line.
3) Tests: extend the lock-suppression tests (tests/test_lock_suppression.py from c2a95f6): (a) own-PID resolution succeeds -> current behavior; (b) own-PID fails -> ListSessions fallback picks the active graphical session of the same UID; (c) multiple sessions -> active preferred over online; (d) no sessions -> unavailable, PrepareForSleep still wired; (e) session closes -> re-resolve. Mock the bus objects.

Where: fluidvoice/daemon.py (lock-watch block), tests/test_lock_suppression.py.

Done means: a phased plan under specs/ where each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green; with a mocked bus where GetSessionByPID raises NoSessionForPID and ListSessions returns one active graphical session of the same UID, the watcher subscribes to that session's Lock/Unlock and the pause-on-lock state machine works identically; live on the user-unit daemon the WARN lines disappear and the doctor lock line reports watching the real session (verify on the daily-driver machine, note result in docs/STATUS.md).

Out of scope: logind inhibitory locks, multi-seat policy decisions beyond the preference order above, Wayland-specific lock surfaces (logind is compositor-neutral), reworking the pause-when-locked state machine itself.
