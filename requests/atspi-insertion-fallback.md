Plan the implementation of an AT-SPI insertion route as a rung in the insertion ladder - ROADMAP "Small standing items" ("AT-SPI insertion fallback ... insertion itself still has no AT-SPI route") and the natural next step of the P2 context seam (the AT-SPI adapter already supplies focused-field identity/role/text at insertion time; see fluidvoice/context/atspi_provider.py and its use in fluidvoice/insertion.py verify paths).

STATUS: OPEN

Today: insertion ladders typed (xdotool/wtype keystrokes) → paste
(verified, clipboard restore) → clipboard + notice (fluidvoice/insertion.py).
AT-SPI is already imported lazily (python-atspi first, then GIR) and
read-only: identity, role, selection, bounded preceding text. Nothing
ever WRITES through the accessibility stack.

Scope:
1) New ladder rung (config `insertion.atspi = "auto"` default semantics:
   tried only when earlier rungs fail, or `"off"`; a `"first"` opt-in can
   come later) using Atspi.Text / editable-text interfaces to set/insert
   the payload into the focused field - character-level insert at the
   caret when the role exposes it.
2) Verify stays the same shape: after the AT-SPI write, read back
   bounded text (the existing read_field_text path) and confirm the
   payload landed - same evidence bar as paste verification.
3) Capability surfacing: doctor/status/Settings insertion matrix grows
   an AT-SPI row (found when the a11y stack imports AND the focused
   field role is editable in a live probe - reuse the doctor mic-probe
   pattern); no capability means the rung is skipped, never failed.
4) Safety: the write path is bounded (payload length cap already
   exists for probes), never targets password/terminal roles (respect
   the terminal safety rules), and is skipped when context.enabled is
   false unless explicitly opted in - decide and document the default
   coupling.
5) Privacy invariants: same as the context seam (dev/context-seam.md) -
   the a11y bus read is transient, bounded, never persisted.

Where: fluidvoice/insertion.py (ladder), fluidvoice/context/
atspi_provider.py (write helpers next to the read ones), fluidvoice/
config.py (spec + whitelist), doctor.py; docs/guides/configuration.md
regenerates from the registry.

Done means: a phased plan each phase leaving the suite green; unit
tests with a fake Atspi module covering write-success, verify-fail →
next rung, role refusal (password/terminal), and config gating; the
capability-matrix test shapes follow the existing insertion ladder
tests; a live-verification note template for the desktop matrix.

Out of scope: Wayland-specific insertion (this is X11+AT-SPI; a
Wayland text-input protocol route is a separate brief), narrating
insertion in the overlay, AT-SPI2 event subscription.
