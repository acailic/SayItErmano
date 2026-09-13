Paste verification semantics are wrong in both directions - four live-reproduced data-corruption defects from the 2026-09-13 night X11 desktop-matrix run (ledger F-34/F-35, evidence docs/research/night-2026-09-13-desktop-matrix-x11.md): duplicated transcripts in terminals, silently lost dictations in Firefox/Discord, and stale-clipboard inserts in Chromium. The read-observation signal (a selection read after the keystroke) is counted too generously and released too early.

STATUS: SHIPPED

<!-- shipped in 33431b2 + 023c44d (content-only verification, field
     probe with poll loop pinned to the keystroke time, 1.5 s proxy-
     aware deadline; gate green, 14 new unit tests). Live evidence
     2026-09-14 on the repro desktop: gedit + gnome-terminal paste
     cells PASS (the F-34 duplication case verifies via the field
     probe, no typed fallback, clipboard restored); Firefox paste
     lands once with the signal on Firefox's own content read in
     bisect runs replicating the exact production preamble; a late
     manager content-read can no longer false-verify. Full browser
     matrix cells deferred to the night-matrix rerun - the desktop
     was in live use and windows were closed under the harness; F-36
     copyq test-env skip stays a separate follow-up.) -->

Today: insert_paste (fluidvoice/insertion.py) verifies via SelectionHold.wait_read - ANY selection read by a window not seen during the quiesce counts, including TARGETS probes and clipboard-manager proxy reads - and on any signal releases ownership and restores the previous clipboard immediately. Reproduced live:

1) F-34 (gnome-terminal, twice incl. a 450-char insert): the paste lands but verification misses it (the read is proxied by an already-known window) -> InsertError(not_verified) -> auto-mode typed fallback types the text AGAIN - duplicated transcript in the shell.
2) F-35a (Firefox, Discord): a TARGETS/proxy read fires the signal ~25 ms after the keystroke -> ownership released + restored BEFORE the app's own content read -> the field receives nothing (silent loss; transcript only in History) or, when the read resolves after the restore, the PREVIOUS clipboard content (Chromium inserted the tester's marker instead of the dictation).

Scope:
1) Content-only verification (selection.py): the paste-verify signal becomes a read of the selection's TEXT CONTENT (UTF8_STRING/text/plain/... target) by a new window - TARGETS/TIMESTAMP/hygiene-marker reads no longer count. New SelectionHold.wait_content_read; wait_read keeps its any-read semantics for diagnostics/tests.
2) Field-content verification (insertion.py + context seam): before the keystroke, snapshot the focused field's text tail via the AT-SPI provider (new read_field_text, bounded read, transient by contract - never stored, never logged; the F-31..F-33 fixes make this readable on real desktops). After the content-read wait: verified = content-read OR payload landed in the field (rescue for proxied/late reads - F-34); a content-read signal is downgraded ONLY on a provably-unchanged field (before == after, the F-35a proxy signature) so password fields (atspi-blind) never lose a real signal. On unverified, raise as today AFTER the restore (typed fallback still clean).
3) Terminal duplication guard: the typed fallback runs only when the field provably lacks the payload (the F-34 rescue covers paste-landed-but-unobserved); auto-mode notice unchanged.
4) Unit tests: content-only _new_reader filtering; wait_content_read on fakes; insert_paste sequences (reader+field agree, field rescue upgrades, strong-negative downgrade, probe-blind passthrough, unverified-raises); FakeHold gains wait_content_read. Integration live_x11 paste-verify test switches to wait_content_read.
5) Live matrix paste cells re-run on this desktop (gedit/gnome-terminal/Firefox/Chromium/Discord) - recorded in the ledger rows.

Where: fluidvoice/selection.py, fluidvoice/insertion.py, fluidvoice/context/atspi_provider.py (read_field_text + shared focus-walk refactor), tests/test_selection.py, tests/test_insertion.py, tests/test_first_use_funnel_insertion.py, tests/integration/test_live_x11.py. No new config keys: everything rides the existing insertion.verify_paste (false = today's legacy path, untouched).

Done means: unit tier green with the new tests; the five live paste cells green or honestly degraded (no duplication, no silent loss, no stale insert) with evidence pointers in ledger F-34/F-35 -> FIXED; gate green. Out of scope: Wayland paste verification (fixed settle stays; the field probe could upgrade it later), F-36 copyq test-env skip, password-manager hint coverage, clipboard-indicator residuals.
