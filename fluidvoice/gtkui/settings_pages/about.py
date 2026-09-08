"""About page: version, backend/GPU/update rows, paths.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ... import __version__ as APP_VERSION


class AboutPageMixin:
    def _build_about(self) -> None:
        page = Adw.PreferencesPage(
            name="about", icon_name="fluidvoice-about-symbolic", title="About"
        )
        grp = Adw.PreferencesGroup(title="About")
        from ... import paths

        grp.add(Adw.ActionRow(title="Version", subtitle=APP_VERSION))
        self.about_backend_row = Adw.ActionRow(title="Backend", subtitle="—")
        self.about_gpu_row = Adw.ActionRow(title="GPU (CUDA)", subtitle="—")
        grp.add(self.about_backend_row)
        grp.add(self.about_gpu_row)
        # update check-and-assist surface (fluidvoice/update.py), fed by the
        # same daemon status poll as the backend/GPU rows
        self.about_update_row = Adw.ActionRow(title="Update", subtitle="—")
        grp.add(self.about_update_row)
        for title, value in (
            ("Config file", str(paths.config_file())),
            ("Control socket", str(paths.socket_path())),
            ("History", str(paths.data_dir() / "history.jsonl")),
        ):
            grp.add(Adw.ActionRow(title=title, subtitle=value))
        about_btn_row = Adw.ActionRow(title="About SayItErmano")
        about_btn = Gtk.Button(icon_name="help-about-symbolic", css_classes=["flat"])
        about_btn.connect("clicked", self._show_about_dialog)
        about_btn_row.add_suffix(about_btn)
        grp.add(about_btn_row)
        page.add(grp)
        self._add_page(page)

    def _show_about_dialog(self, *_args) -> None:
        dlg = Adw.AboutDialog(
            application_name="SayItErmano",
            application_icon="sayit-ermano",
            version=APP_VERSION,
            website="https://github.com/acailic/SayItErmano",
            issue_url="https://github.com/acailic/SayItErmano/issues",
            license_type=Gtk.License.GPL_3_0,
        )
        dlg.present(self)
