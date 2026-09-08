"""History page: retention, audio budget, clear/browse.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gtk


class HistoryPageMixin:
    def _build_history_page(self) -> None:
        page = Adw.PreferencesPage(
            name="history", icon_name="fluidvoice-history-symbolic", title="History"
        )
        grp = Adw.PreferencesGroup(title="History")
        grp.add(self._switch("history", "save", "Save transcriptions"))
        grp.add(
            self._switch(
                "history",
                "save_audio",
                "Keep audio",
                "Store the recording with each entry",
            )
        )
        grp.add(
            self._spin(
                "history",
                "audio_budget_gb",
                "Audio budget (GB)",
                0.0,
                1024.0,
                0.5,
                digits=1,
            )
        )
        clear = Adw.ActionRow(
            title="Clear all history", subtitle="Delete every entry and retained audio"
        )
        clear_btn = Gtk.Button(label="Clear…", css_classes=["destructive-action"])
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.connect("clicked", self._confirm_clear_history)
        clear.add_suffix(clear_btn)
        grp.add(clear)
        browse = Adw.ActionRow(
            title="Browse history", subtitle="Open the History window"
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
        self.add(page)

    def _confirm_clear_history(self, _btn) -> None:
        def confirmed(dialog, response):
            if response == "clear":
                removed = self.c.history_clear()
                self.toast(f"Removed {removed} entries")

        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading="Clear all history?",
            body="Every saved transcription and any retained audio will be "
            "deleted. This cannot be undone.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("clear", "Clear All")
        dlg.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", confirmed)
        dlg.present()
