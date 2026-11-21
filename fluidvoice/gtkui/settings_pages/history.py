"""History page: retention, audio budget, clear/browse.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ...i18n import _, _n


class HistoryPageMixin:
    def _build_history_page(self) -> None:
        page = Adw.PreferencesPage(
            name="history", icon_name="fluidvoice-history-symbolic",
            title=_("History"),
        )
        grp = Adw.PreferencesGroup(title=_("History"))
        grp.add(self._switch("history", "save", _("Save transcriptions")))
        grp.add(
            self._switch(
                "history",
                "save_audio",
                _("Keep audio"),
                _("Store the recording with each entry"),
            )
        )
        grp.add(
            self._spin(
                "history",
                "audio_budget_gb",
                _("Audio budget (GB)"),
                0.5,
                digits=1,
            )
        )
        clear = Adw.ActionRow(
            title=_("Clear all history"),
            subtitle=_("Delete every entry and retained audio")
        )
        clear_btn = Gtk.Button(label=_("Clear…"),
                               css_classes=["destructive-action"])
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.connect("clicked", self._confirm_clear_history)
        clear.add_suffix(clear_btn)
        grp.add(clear)
        browse = Adw.ActionRow(
            title=_("Browse history"), subtitle=_("Open the History window")
        )
        b_btn = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat"])
        b_btn.connect(
            "clicked",
            lambda *_: self.get_application() and self.get_application().show_history(),
        )
        browse.add_suffix(b_btn)
        grp.add(browse)
        page.add(grp)
        page.add(self._save_group())
        self._add_page(page)

    def _confirm_clear_history(self, _btn) -> None:
        def confirmed(dialog, response):
            if response == "clear":
                removed = self.c.history_clear()
                self.toast(_n("Removed {n} entry", "Removed {n} entries",
                              removed).format(n=removed))

        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=_("Clear all history?"),
            body=_("Every saved transcription and any retained audio will "
                  "be deleted. This cannot be undone."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("clear", _("Clear All"))
        dlg.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", confirmed)
        dlg.present()
