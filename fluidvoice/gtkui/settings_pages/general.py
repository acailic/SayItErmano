"""General page: UI language + app behaviour basics (macOS-parity
groups: General / Notifications / Sounds; the dictation language cycle
+ whitelist live on the Dictation page). Mixin methods are defined on
SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw

from ... import i18n
from ...config import KNOWN_LANGUAGES as LANGUAGES
from ...i18n import _


class GeneralPageMixin:
    def _build_general(self) -> None:
        page = Adw.PreferencesPage(
            name="general", icon_name="fluidvoice-general-symbolic",
            title=_("General"),
        )
        grp = Adw.PreferencesGroup(title=_("General"))
        ui_lang = self._combo(
            "general",
            "ui_language",
            _("Language"),
            i18n.combo_choices(),
            subtitle=_("Interface language — follows the system by default"),
        )
        self.ui_lang_row = ui_lang
        grp.add(ui_lang)
        lang = self._combo(
            "general",
            "language",
            _("Dictation language"),
            [("auto (detect)", "auto")] + [(c, c) for c in LANGUAGES],
            subtitle=_("Whisper code the recognizer locks to"),
        )
        self.lang_row = lang
        grp.add(lang)
        grp.add(
            self._switch(
                "general",
                "copy_to_clipboard",
                _("Copy transcriptions to clipboard"),
                _("Every dictation also lands in the clipboard"),
            )
        )
        grp.add(
            self._switch(
                "general",
                "tray_enabled",
                _("Tray icon"),
                _("Panel icon while the daemon runs (toggle applies live)"),
            )
        )
        page.add(grp)

        notif = Adw.PreferencesGroup(title=_("Notifications"))
        notif.add(self._switch("notifications", "enabled", _("Notifications")))
        page.add(notif)

        sounds = Adw.PreferencesGroup(title=_("Sounds"))
        sounds.add(self._switch("sounds", "enabled", _("Sounds")))
        sounds.add(self._spin("sounds", "volume", _("Volume"), 0.05, digits=2))
        page.add(sounds)

        page.add(self._save_group())
        self._add_page(page)
