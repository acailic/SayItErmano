Context provider (AT-SPI) is silently blind on busy desktops - three reproduced defects from the 2026-09-13 night X11 desktop-matrix run (ledger F-31/F-32/F-33, evidence docs/research/night-2026-09-13-desktop-matrix-x11.md). All three block matrix C3 (and the Wayland REQ-PARITY path uses the same provider); none produce a log line - the read just degrades to missing/stale.

STATUS: OPEN

Today: read_focus (fluidvoice/context/atspi_provider.py) walks desktop -> first ACTIVE-flagged window -> FOCUSED descendant, under ReadLimits (fluidvoice/context/base.py) whose apps cap defaults to 16. Reproduced live on this desktop (26-28 a11y-registered apps, the interesting ones at indices 16+):

1) F-31: the apps cap truncates the tree before the focused app - the read returns missing with no trace; P2 continuation and insertion-time identity silently never engage.
2) F-32: _find_active_window returns the FIRST window carrying STATE_ACTIVE in desktop order; unfocused Electron windows keep ACTIVE (night run: identity attached to an unfocused "Codex|ChatGPT" window while gedit held focus) - wrong-window identity feeds profiles/spoken-send routing.
3) F-33: on GIR installs without python-atspi (the project venv), Accessible.get_text(start, end) hits the deprecated 1-arg interface getter, raises TypeError, and the duck adapter swallows it - selection/preceding never read even when the walk is fine. Verified working form: the unbound interface call Atspi.Text.get_text(node, start, end).

Scope:
1) Raise the walk budget honestly: ReadLimits.apps default 16 -> 64 (verified desktops register ~26-28; bounded cost - the scan is one child + one state read per app). Keep caller-injected limits authoritative.
2) Collect ALL ACTIVE-flagged windows (bounded candidate list, desktop order) and probe each for a FOCUSED descendant with a per-candidate slice of the node budget; first candidate with focus wins; if none has focus, fall back to the first ACTIVE window as today (stale identity-only - compat floor unchanged).
3) GIR unbound interface fallbacks in the _AtspiNode text adapters: when the bound call is missing/raises, try the unbound Atspi.Text.* form (get_text, get_character_count, get_caret_offset, get_selection) before degrading to None. pyatspi queryText() keeps precedence.
4) Unit tests against fakes: busy-desktop cap (focused app beyond index 16), explicit small limit still truncates, multi-ACTIVE selection (wrong-first/second-has-focus/all-unfocused-stale-first), GIR node whose bound get_text raises with a Text interface class that works unbound.

Where: fluidvoice/context/base.py (ReadLimits defaults), fluidvoice/context/atspi_provider.py (_find_active_window -> candidate scan, _AtspiNode text adapters), tests/test_context_atspi.py. No seam/config/daemon changes - ReadLimits stays a code constant, FocusContext consumers unchanged.

Done means: unit tier green with the new tests; live re-verification on this X11 desktop (the night repro) showing read_focus returning a usable field-level context (identity + role + preceding) for a focused gedit with the venv's GIR-only Atspi - recorded in the ledger rows F-31..F-33 as FIXED with pointers; matrix C3 mechanism unblocked (the full C3 cell still needs the night-matrix rerun). Out of scope: paste-mode verification defects F-34/F-35 (separate brief - insertion semantics, not the provider), F-36 copyq test-env skip, Wayland live matrices, raising context.enabled default.
