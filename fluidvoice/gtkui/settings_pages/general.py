"""General page: UI language + app behaviour basics (macOS-parity
groups: General / Notifications / Sounds; the dictation language cycle
+ whitelist live on the Dictation page). Mixin methods are defined on
SettingsWindow; this split is structural only.

Flatpak only: the Startup group (Background-portal autostart —
ADR-0006: XDG autostart replaces the systemd user unit inside the
sandbox; on host installs the deb's /etc/xdg/autostart entry + unit
own this, so the row is not shown).
"""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib

from ... import i18n
from ...config import KNOWN_LANGUAGES as LANGUAGES
from ...i18n import _
from ...portal_background import load_autostart_state, save_autostart_state


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

        from ...daemon_owner import in_flatpak
        if in_flatpak():
            page.add(self._build_startup_group())

        notif = Adw.PreferencesGroup(title=_("Notifications"))
        notif.add(self._switch("notifications", "enabled", _("Notifications")))
        page.add(notif)

        sounds = Adw.PreferencesGroup(title=_("Sounds"))
        sounds.add(self._switch("sounds", "enabled", _("Sounds")))
        sounds.add(self._spin("sounds", "volume", _("Volume"), 0.05, digits=2))
        page.add(sounds)

        page.add(self._save_group())
        self._add_page(page)

    # -- Flatpak autostart (Background portal) ------------------------------

    def _build_startup_group(self) -> Adw.PreferencesGroup:
        grp = Adw.PreferencesGroup(
            title=_("Startup"),
            description=_("Set through the freedesktop Background "
                          "portal; your desktop writes the login entry"),
        )
        self._autostart_row = Adw.SwitchRow(
            title=_("Start dictation at login"),
            subtitle=_("Runs the daemon in the background when you log in"),
        )
        self._autostart_suppress = False
        self._autostart_row.connect("notify::active",
                                    self._on_autostart_toggled)
        self._refresh_autostart_row()
        grp.add(self._autostart_row)
        return grp

    def _refresh_autostart_row(self) -> None:
        """Reflect the persisted portal intent (the portal has no query
        API; the sidecar records what we last requested)."""
        row = getattr(self, "_autostart_row", None)
        if row is None:
            return
        self._autostart_suppress = True
        try:
            row.set_active(load_autostart_state())
        finally:
            self._autostart_suppress = False

    def _on_autostart_toggled(self, row, _pspec) -> None:
        if getattr(self, "_autostart_suppress", False):
            return
        enabled = row.get_active()

        def _apply() -> None:
            from ...portal_background import set_autostart
            ok, detail = set_autostart(enabled)

            def _back() -> bool:
                if ok:
                    save_autostart_state(enabled)
                    self.toast(_("Starts at login") if enabled
                               else _("Autostart off"))
                else:
                    self._refresh_autostart_row()  # revert to the file
                    self.toast(_("Could not set autostart: {detail} "
                                 "— create the login entry manually with "
                                 "your desktop's autostart "
                                 "settings").format(detail=detail), 8)
                return False  # one-shot idle

            GLib.idle_add(_back)

        threading.Thread(target=_apply, name="fluidvoice-autostart",
                         daemon=True).start()
