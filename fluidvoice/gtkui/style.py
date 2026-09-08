"""App-wide presentation bootstrap: shared CSS + bundled icon resource.

Both are idempotent per process and are registered by load_style(),
which every window calls right after super().__init__() — so ANY
construction path (the app shell, tests, screenshot drivers, future
tooling) gets styled windows with resolvable fluidvoice-* icons, with or
without the packaging's hicolor copies. The stylesheet is embedded (not
a data file) so any install layout — editable checkout, deb venv, wheel
— gets it without packaging changes.
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
_icons_done = False


def register_icons() -> None:
    """Make bundled `fluidvoice-*` icons resolvable by name (idempotent).

    The symbolic page icons ship as SVGs in assets/icons/symbolic/actions.
    We compile them into a GResource (cached in the cache dir) and
    register it with the icon theme — the standard GNOME-app way, immune
    to icon-theme swaps (third-party themes hijack generic names like
    `preferences-system-symbolic`, which rendered as washed-out defaults).
    The deb additionally installs the SVGs into the system hicolor theme;
    user installs (pip/venv) rely on this path alone.
    """
    global _icons_done
    if _icons_done:
        return
    _icons_done = True
    try:
        import hashlib
        import subprocess
        import tempfile
        from importlib import resources
        from pathlib import Path

        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, Gtk

        src = resources.files("fluidvoice.assets").joinpath(
            "icons/symbolic/actions")
        with resources.as_file(src) as srcdir:
            svgs = sorted(Path(srcdir).glob("*.svg"))
            if not svgs:
                return
            names = "".join(p.name for p in svgs)
            digest = hashlib.sha1(names.encode()
                                  + b"".join(p.read_bytes() for p in svgs)
                                  ).hexdigest()[:16]
            from .. import paths
            cache = paths.cache_dir() / "icons"
            cache.mkdir(parents=True, exist_ok=True)
            gresource = cache / f"icons-{digest}.gresource"
            if not gresource.exists():
                xml = "<gresources><gresource prefix='/io/github/acailic/sayitermano/icons'>"
                for p in svgs:
                    # strip the trailing -symbolic; GTK looks up by basename
                    file_attr = f" alias='scalable/actions/{p.name}'"
                    xml += f"<file{file_attr}>{p}</file>"
                xml += "</gresource></gresources>"
                with tempfile.NamedTemporaryFile("w", suffix=".xml",
                                                 delete=False) as f:
                    f.write(xml)
                    manifest = f.name
                try:
                    subprocess.run(["glib-compile-resources", "--target",
                                    str(gresource), manifest],
                                   check=True, capture_output=True)
                finally:
                    Path(manifest).unlink(missing_ok=True)

        Gio.resources_register(Gio.Resource.load(str(gresource)))
        display = Gdk.Display.get_default()
        if display is not None:  # no display -> icons resolve if one opens?
            theme = Gtk.IconTheme.get_for_display(display)
            if theme is not None:
                theme.add_resource_path("/io/github/acailic/sayitermano/icons")
    except Exception:
        pass  # icon-name lookups degrade to theme fallbacks


def load_style() -> None:
    """Register the app stylesheet + bundled icons once per process."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    register_icons()
    from gi.repository import Gdk, Gtk
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)
    display = Gdk.Display.get_default()
    if display is not None:  # no display -> windows are never shown anyway
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
