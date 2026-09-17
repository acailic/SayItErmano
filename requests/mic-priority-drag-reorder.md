Plan the implementation of drag-to-reorder for the mic priority list - ROADMAP "Small standing items" (the mic-priority brief shipped up/down buttons and explicitly left drag out of scope) and UPSTREAM-TRACKING row "Mic priority list, drag-to-reorder, device history".

STATUS: OPEN

Today: `recording.mic_priority` (ordered case-insensitive source-name
patterns, `_coerce_mic_priority` in fluidvoice/config.py) is edited in the
GTK Settings → Dictation section via add/remove/up/down button rows
(fluidvoice/gtkui/settings_window.py `_load_mic_priority` /
`_collect_mic_priority`); micmon.py consumes the order for auto-switch.

Scope:
1) GTK4 drag-and-drop reordering of the mic-priority rows (Gtk.ListBox
   with GtkDragSource/GtkDropTarget or Gtk.DropTarget + row index swap -
   pick the mechanism that survives touchpads and a11y tooling best) -
   keep the up/down buttons as the keyboard/a11y path (do NOT remove
   them; keyboard reorder must stay possible with focus alone).
2) The row order is the persisted truth: dropping writes through the
   same dirty-tracking/save path the buttons use today; no new config
   key, no migration.
3) Visual feedback while dragging: an insertion gap or moved-row
   highlight that respects `prefers-reduced-motion`-equivalent (GTK
   reduced-motion setting) - no animation when the system asks for none.
4) Discovery: a one-line hint near the list ("drag to reorder" in the
   section's helper text).

Where: fluidvoice/gtkui/settings_window.py (rows + DnD controllers);
tests follow the existing GTK smoke-test shapes (no real drag events
needed: assert the reorder callback maps row indices to the persisted
list, and that buttons and DnD share one write path).

Done means: a phased plan a builder can implement without questions;
unit tests for the index-swap → config-list write and the shared
button/DnD path; a GTK smoke test that the rows render and the shared
reorder entry point is wired; the up/down buttons unchanged in behavior.

Out of scope: per-app mic profiles, changing micmon's matching semantics,
Wayland-specific work.
