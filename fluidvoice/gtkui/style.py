"""App-wide CSS: small presentation classes shared by the GTK windows.

The stylesheet is embedded (not a data file) so any install layout —
editable checkout, deb venv, wheel — gets it without packaging changes.
load_style() is idempotent per process; every window calls it first thing
so windows constructed directly (tests, `python -m fluidvoice app`) are
styled identically.
"""
from __future__ import annotations

_CSS = """
/* Metadata pill tags on history rows (mode chip, "AI polished"). */
.tag {
    border-radius: 9999px;
    padding: 2px 9px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.tag.outline {
    border: 1px solid alpha(currentColor, 0.35);
}
.tag.accent {
    background: alpha(@accent_bg_color, 0.16);
    color: @accent_color;
}

/* Date section labels between history card groups ("Today", "Yesterday"). */
.section-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
}
"""

_loaded = False


def load_style() -> None:
    """Register the app stylesheet once per process (per display)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from gi.repository import Gdk, Gtk
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)
    display = Gdk.Display.get_default()
    if display is not None:  # no display -> windows are never shown anyway
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
