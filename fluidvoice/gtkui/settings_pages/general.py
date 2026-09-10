"""General page: language + app behaviour basics (macOS-parity groups:
General / Notifications / Sounds; the language cycle + whitelist live on
the Dictation page). Mixin methods are defined on SettingsWindow; this
split is structural only."""

from __future__ import annotations

from gi.repository import Adw

from ...config import KNOWN_LANGUAGES as LANGUAGES


class GeneralPageMixin:
    def _build_general(self) -> None:
        page = Adw.PreferencesPage(
            name="general", icon_name="fluidvoice-general-symbolic", title="General"
        )
        grp = Adw.PreferencesGroup(title="General")
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
        page.add(grp)

        notif = Adw.PreferencesGroup(title="Notifications")
        notif.add(self._switch("notifications", "enabled", "Notifications"))
        page.add(notif)

        sounds = Adw.PreferencesGroup(title="Sounds")
        sounds.add(self._switch("sounds", "enabled", "Sounds"))
        sounds.add(self._spin("sounds", "volume", "Volume", 0.05, digits=2))
        page.add(sounds)

        page.add(self._save_group())
        self._add_page(page)
