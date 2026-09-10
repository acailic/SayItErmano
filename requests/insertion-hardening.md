Insertion hardening: paste verification, clipboard hygiene, terminal paste quirks - roadmap "Later" item "Insertion hardening", docs/UPSTREAM-TRACKING.md line 154 ("Reliable pasting in Ghostty/tmux/terminals"). Robustness work with no UI change; the failure modes below are reproducible today.

STATUS: SHIPPED

<!-- shipped in 32c65b3 -->

Today: insert_paste (fluidvoice/insertion.py:76) = read clipboard, write dictation text, xdotool ctrl+v, sleep 0.25 s, restore the previous clipboard blindly. Failure modes: (a) a slow-to-focus app reads the clipboard AFTER our restore - dictation is lost and the user's old clipboard is pasted over it; (b) nothing verifies the paste landed before we restore and report success; (c) clipboard managers (GPaste/Klipper/Clipman) snapshot the dictation text as it flashes through the selection - privacy leak and clutter; (d) terminals and tmux/ghostty front-ends need ctrl+shift+v, not ctrl+v - docs/UPSTREAM-TRACKING.md:154.

Scope:
1) Paste verify-then-restore: replace the fixed 0.25 s with a bounded settle poll - after ctrl+v, wait until the target has had the content (plan verifies the right ICCCM/X11 signal with python-xlib: selection re-ownership, content change on read-back, or a 0.1->0.6 s backoff ladder as last resort), then restore the previous clipboard, then re-read to confirm the restore took; on any mismatch, retry restore once. If settle/verify fails, raise InsertError so insert_text (insertion.py:94) falls back to typed insertion, and surface the fallback through the existing notification path so the user knows paste did not land.
2) Clipboard-manager hygiene: own the selection in a way clipboard managers ignore for the duration of the paste. Plan verifies the technique that works on THIS desktop (GNOME X11; check what is actually running) and X11 generally: candidates to verify - the `x-kde-passwordManagerHint` MIME target (Klipper semantics, honored elsewhere?), a short-lived selection with immediate disown via `xclip`/`xsel`, ICCCM selection ownership without TARGETS advertisement. Success = dictation text absent from the running manager's history on this machine; if no technique verifies, land the minimal-flash variant (shortest hold + restore) and document the residual honestly in docs/STATUS.md.
3) Terminal paste quirks: terminal_apps WM_CLASS list (shared with the chat-formatting brief's key - coordinate, one key, not two) -> paste uses ctrl+shift+v there; verify ghostty/tmux behavior during planning and record what still mis-pastes in docs/STATUS.md rather than guessing.
4) Config: insertion.verify_paste = true (toggle restores today's sleep-restore), insertion.terminal_paste_key = "ctrl+shift+v"; doctor gains one resolution line per key.

Where: fluidvoice/insertion.py, fluidvoice/config.py, tests/test_insertion.py (fake xclip/xdotool processes, hermetic DISPLAY patterns already there), tests/test_config_settings.py, tests/test_infra.py (doctor), optional tests/integration live paste check following tests/integration/test_live_x11.py patterns. Plan against then-current HEAD - insertion.py is stable but config.py may be mid-flight.

Done means: a phased plan under specs/ where each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green; unit tests for the settle/restore state machine against faked clipboard reads (slow app, never-reads app, restore-mismatch retry), terminal key selection, and the InsertError fallback path; the hygiene technique demonstrated against the live desktop's clipboard manager or documented impossible with evidence; timing constants named and testable.

Out of scope: Wayland (ydotool/wtype is v0.3), AT-SPI insertion fallback, typed-mode changes, clipboard manager configuration on the user's behalf.
