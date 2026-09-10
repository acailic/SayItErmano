"""Shared settings helpers: field-registry proxies + key naming
(used by the shell and every page mixin)."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gtk

from ... import config

_KEY_REMAP = {
    "Control_L": "Left_Control",
    "Control_R": "Right_Control",
    "Alt_L": "Left_Alt",
    "Alt_R": "Right_Alt",
    "Shift_L": "Left_Shift",
    "Shift_R": "Right_Shift",
    "Super_L": "Left_Super",
    "Super_R": "Right_Super",
}


class _SwitchProxy:
    """Adapter so plain Gtk.Switch rows (inside expanders) load/collect like
    Adw.SwitchRow through the same field registry."""

    def __init__(self, switch: Gtk.Switch, title: str, subtitle: str = ""):
        self.switch = switch
        self.title = title
        self.subtitle = subtitle

    def set_active(self, v: bool) -> None:
        self.switch.set_active(v)

    def get_active(self) -> bool:
        return self.switch.get_active()


class _ListProxy:
    """EntryRow-backed list<string> (comma-separated) for the field registry."""

    def __init__(self, row: Adw.EntryRow):
        self.row = row

    def set_value(self, values) -> None:
        self.row.set_text(", ".join(values or []))

    def get_value(self) -> list:
        return [v.strip() for v in self.row.get_text().split(",") if v.strip()]


class _ExtraShortcutsProxy:
    """Two (key EntryRow, profile ComboRow) pairs backing the
    hotkey.extra_shortcuts list (B1). Index 0 of the combo is '(none)'."""

    def __init__(self, pairs):
        self.pairs = pairs
        model = pairs[0][1].get_model()
        self.names = [model.get_string(i) for i in range(model.get_n_items())]

    def set_value(self, value) -> None:
        value = list(value or [])
        for i, (key_row, combo) in enumerate(self.pairs):
            spec = value[i] if i < len(value) else {}
            key_row.set_text(spec.get("key", ""))
            name = spec.get("profile", "")
            idx = self.names.index(name) if name in self.names else 0
            combo.set_selected(idx)

    def get_value(self) -> list:
        out = []
        for key_row, combo in self.pairs:
            key = key_row.get_text().strip()
            if not key:
                continue
            idx = combo.get_selected()
            name = self.names[idx] if 0 <= idx < len(self.names) else ""
            entry = {"key": key}
            if name and name != "(none)":
                entry["profile"] = name
            out.append(entry)
        return out[:2]


class _ActionTriggersProxy:
    """Four EntryRows (one per action) backing the dict-valued
    processing.formatting_action_triggers; aliases are comma-separated."""

    _ACTIONS = ("new_line", "new_paragraph", "tab", "space")

    def __init__(self, rows: dict):
        self.rows = rows  # action -> Adw.EntryRow

    def set_value(self, value) -> None:
        value = value or {}
        for action in self._ACTIONS:
            aliases = value.get(action) or []
            self.rows[action].set_text(", ".join(aliases))

    def get_value(self) -> dict:
        out = {}
        for action in self._ACTIONS:
            aliases = [
                v.strip() for v in self.rows[action].get_text().split(",") if v.strip()
            ]
            if aliases:
                out[action] = aliases
        return out


class _TextProxy:
    """TextBuffer-backed multi-line string for the field registry (the
    base-prompt editor). Empty is a MEANINGFUL value ("use the built-in
    prompt"), unlike EntryRow's keep-the-saved-value rule."""

    def __init__(self, buffer: Gtk.TextBuffer):
        self.buffer = buffer

    def set_value(self, text) -> None:
        self.buffer.set_text(str(text or ""))

    def get_value(self) -> str:
        start, end = self.buffer.get_start_iter(), self.buffer.get_end_iter()
        return self.buffer.get_text(start, end, False)


class _PasswordProxy:
    """Gtk.Entry (visibility=False) inside an Adw.ActionRow - the field
    registry adapter for model.remote_api_key. Never RENDERS a stored
    value (set_value only ever clears + annotates the row: the value may
    be a masked bool from the daemon or a real secret in file-only mode);
    _collect skips it while empty so the saved key carries over."""

    def __init__(self, entry: Gtk.Entry, row: Adw.ActionRow):
        self.entry = entry
        self.row = row

    def set_value(self, value) -> None:
        # value True (masked, daemon up) or a non-empty string (file-only):
        # show the "saved" hint, keep the field empty either way
        if value:
            self.entry.set_text("")
            self.row.set_subtitle("key saved — type to replace")
        else:
            self.entry.set_text("")
            self.row.set_subtitle("optional bearer token (never shown)")

    def get_text(self) -> str:
        return self.entry.get_text()


class _InstructionRow(Adw.PreferencesRow):
    """A preferences-compatible row that hosts the instructions TextView."""

    def __init__(self, text_view: Gtk.TextView, title: str = "Instructions"):
        super().__init__(title=title)
        sw = Gtk.ScrolledWindow(
            child=text_view,
            hexpand=True,
            height_request=90,
            propagate_natural_height=True,
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            margin_start=14,
            margin_end=14,
            margin_bottom=8,
        )
        box.append(sw)
        self.set_child(box)


def _default(section: str, key: str):
    """The registered default for a setting (config registry lookup)."""
    return config.default(section, key)


def _keyname(keyval) -> str:
    name = Gdk.keyval_name(keyval) or ""
    if len(name) == 1 and name.isalpha():
        return name.lower()
    return _KEY_REMAP.get(name, name)
