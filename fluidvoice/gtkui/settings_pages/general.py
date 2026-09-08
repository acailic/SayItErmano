"""General page: language basics + language-cycle editor.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ...config import KNOWN_LANGUAGES
from ...config import KNOWN_LANGUAGES as LANGUAGES
from .common import _ListProxy


class GeneralPageMixin:
    def _build_general(self) -> None:
        page = Adw.PreferencesPage(
            name="general", icon_name="fluidvoice-general-symbolic", title="General"
        )
        grp = Adw.PreferencesGroup(title="Basics")
        lang = self._combo(
            "general",
            "language",
            "Language",
            [("auto (detect)", "auto")] + [(c, c) for c in LANGUAGES],
            subtitle="Whisper code the recognizer locks to",
        )
        self.lang_row = lang
        grp.add(lang)
        grp.add(
            self._switch(
                "general",
                "copy_to_clipboard",
                "Copy transcriptions to clipboard",
                "Every dictation also lands in the clipboard",
            )
        )
        grp.add(
            self._switch(
                "general",
                "tray_enabled",
                "Tray icon",
                "Panel icon while the daemon runs (toggle applies live)",
            )
        )
        grp.add(self._switch("notifications", "enabled", "Notifications"))
        grp.add(self._switch("sounds", "enabled", "Sounds"))
        grp.add(self._spin("sounds", "volume", "Volume", 0.0, 1.0, 0.05, digits=2))
        page.add(grp)

        # Language cycle (mic-priority editor pattern): the ordered list
        # hotkey.language_key steps through. Runtime daemon state - the
        # list is saved, the cycle POSITION never is.
        self.cycle_group = Adw.PreferencesGroup(
            title="Language cycle",
            description="Languages the cycle key steps through — e.g. "
            "auto, en, sl (runtime state; never persisted; "
            "empty = cycle key off)",
        )
        self._cycle_add_row = Adw.ActionRow(title="Add language")
        add_lang_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_lang_btn.connect("clicked", lambda *_: self._add_cycle_lang(""))
        self._cycle_add_row.add_suffix(add_lang_btn)
        self.cycle_group.add(self._cycle_add_row)
        page.add(self.cycle_group)

        wl = Adw.EntryRow(title="Language whitelist — e.g. sl, en")
        wl.connect("changed", lambda *_: self._touch())
        self._rows[("general", "language_whitelist")] = _ListProxy(wl)
        wgrp = Adw.PreferencesGroup(
            title="Wrong-language guard",
            description="Comma-separated codes; when auto-detection lands "
            "outside them the take is re-decoded once with "
            "the first entry. Whisper multilingual backends "
            "only; parakeet (English-only) and whisper.cpp "
            "auto are unaffected",
        )
        wgrp.add(wl)
        page.add(wgrp)

        page.add(self._save_group())
        self.add(page)

    def _load_language_cycle(self, codes: list) -> None:
        for ref in list(self._cycle_rows):
            self.cycle_group.remove(ref["row"])
        self._cycle_rows = []
        for code in codes:
            self._add_cycle_lang(str(code))

    def _add_cycle_lang(self, value: str) -> None:
        row = Adw.EntryRow(title="Code — e.g. auto, en, sl")
        row.set_text(value)
        row.connect("changed", self._on_cycle_lang_changed)
        up = Gtk.Button(
            icon_name="go-up-symbolic", css_classes=["flat"], tooltip_text="Move up"
        )
        down = Gtk.Button(
            icon_name="go-down-symbolic", css_classes=["flat"], tooltip_text="Move down"
        )
        rm = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            tooltip_text="Remove this language",
        )
        ref = {"row": row, "up": up, "down": down}
        up.connect("clicked", lambda *_: self._move_cycle_lang(ref, -1))
        down.connect("clicked", lambda *_: self._move_cycle_lang(ref, 1))
        rm.connect("clicked", lambda *_: self._remove_cycle_lang(ref))
        row.add_suffix(up)
        row.add_suffix(down)
        row.add_suffix(rm)
        self._cycle_rows.append(ref)
        self._rebuild_cycle_lang()
        self._on_cycle_lang_changed(row)

    def _on_cycle_lang_changed(self, row) -> None:
        """Non-blocking pre-flight: an entry that is neither "auto" nor a
        known code gets the error style; the daemon's coerce_setting is
        the authority (a bad value toasts as rejected on save)."""
        code = row.get_text().strip().lower()
        if code and code != "auto" and code not in KNOWN_LANGUAGES:
            row.add_css_class("error")
        else:
            row.remove_css_class("error")
        self._touch()

    def _move_cycle_lang(self, ref: dict, delta: int) -> None:
        i = self._cycle_rows.index(ref)
        j = i + delta
        if not 0 <= j < len(self._cycle_rows):
            return  # already at the edge
        self._cycle_rows[i], self._cycle_rows[j] = (
            self._cycle_rows[j],
            self._cycle_rows[i],
        )
        self._rebuild_cycle_lang()

    def _rebuild_cycle_lang(self) -> None:
        # the same widgets are re-added, so entered text survives
        for ref in list(self._cycle_rows):
            self.cycle_group.remove(ref["row"])
        self.cycle_group.remove(self._cycle_add_row)
        self.cycle_group.add(self._cycle_add_row)
        for ref in self._cycle_rows:
            self.cycle_group.add(ref["row"])
        last = len(self._cycle_rows) - 1
        for i, ref in enumerate(self._cycle_rows):
            ref["up"].set_sensitive(i > 0)
            ref["down"].set_sensitive(i < last)

    def _remove_cycle_lang(self, ref: dict) -> None:
        self.cycle_group.remove(ref["row"])
        self._cycle_rows.remove(ref)
        self._rebuild_cycle_lang()
        self._touch()

    def _collect_language_cycle(self) -> list[str]:
        return [
            r["row"].get_text().strip().lower()
            for r in self._cycle_rows
            if r["row"].get_text().strip()
        ]
