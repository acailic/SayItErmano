"""About page: version, backend/GPU/update rows, paths.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

import subprocess
import threading

from gi.repository import Adw, GLib, Gtk

from ... import __version__ as APP_VERSION
from ...i18n import _


class AboutPageMixin:
    def _build_about(self) -> None:
        page = Adw.PreferencesPage(
            name="about", icon_name="fluidvoice-about-symbolic",
            title=_("About"),
        )
        grp = Adw.PreferencesGroup(title=_("About"))
        from ... import paths

        grp.add(Adw.ActionRow(title=_("Version"), subtitle=APP_VERSION))
        self.about_backend_row = Adw.ActionRow(title=_("Backend"), subtitle="—")
        self.about_gpu_row = Adw.ActionRow(title=_("GPU (CUDA)"), subtitle="—")
        grp.add(self.about_backend_row)
        grp.add(self.about_gpu_row)
        # update check-and-assist surface (fluidvoice/update.py), fed by the
        # same daemon status poll as the backend/GPU rows. The refresh
        # button is the MANUAL check (no-telemetry ADR: the daemon probes
        # GitHub only when the user asks - it works even with
        # updates.check = false); the guide button deep-links the update
        # route guide when a newer release is reported.
        self.about_update_row = Adw.ActionRow(title=_("Update"), subtitle="—")
        self.about_update_check_btn = Gtk.Button(
            icon_name="view-refresh-symbolic", css_classes=["flat"],
            tooltip_text=_("Check for updates now"))
        self.about_update_check_btn.connect("clicked", self._on_check_updates)
        self.about_update_row.add_suffix(self.about_update_check_btn)
        self.about_update_guide_btn = Gtk.Button(
            label=_("How to update"), css_classes=["flat"],
            tooltip_text=_("Open the update route guide"))
        self.about_update_guide_btn.connect("clicked", self._open_route_guide)
        self.about_update_guide_btn.set_visible(False)
        self.about_update_row.add_suffix(self.about_update_guide_btn)
        grp.add(self.about_update_row)
        # offline license entitlement (ADR-0005): display-only, never gates
        # anything; refreshed by the same status poll so a CLI import while
        # the app is open shows up without a restart
        from ... import license as license_mod
        self.about_license_row = Adw.ActionRow(
            title=_("License"),
            subtitle=license_mod.status_text(license_mod.status()))
        grp.add(self.about_license_row)
        for title, value in (
            (_("Config file"), str(paths.config_file())),
            (_("Control socket"), str(paths.socket_path())),
            (_("History"), str(paths.data_dir() / "history.jsonl")),
        ):
            grp.add(Adw.ActionRow(title=title, subtitle=value))
        about_btn_row = Adw.ActionRow(title=_("About SayItErmano"))
        about_btn = Gtk.Button(icon_name="help-about-symbolic", css_classes=["flat"])
        about_btn.connect("clicked", self._show_about_dialog)
        about_btn_row.add_suffix(about_btn)
        grp.add(about_btn_row)
        page.add(grp)
        self._add_page(page, section="more")

    def refresh_license_row(self) -> None:
        """Re-read the stored key into the About → License subtitle."""
        from ... import license as license_mod
        self.about_license_row.set_subtitle(
            license_mod.status_text(license_mod.status()))

    # -- manual update check (control route "update-check") -------------------

    def _on_check_updates(self, *_args) -> None:
        """User asked for a check: fire the socket request off-thread (the
        daemon may spend up to 10 s on the GitHub fetch - the UI must not
        freeze) and apply the result on the main loop."""
        self.about_update_row.set_subtitle(_("checking…"))
        self.about_update_guide_btn.set_visible(False)

        def work() -> None:
            try:
                resp = self.c.update_check()
            except Exception as e:  # noqa: BLE001 - daemon down etc.
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(self._after_check_updates, resp)

        threading.Thread(target=work, daemon=True).start()

    def _after_check_updates(self, resp: dict) -> bool:
        """Main-loop half: resp is the route payload or an error stub."""
        if resp.get("ok") is False:
            self.about_update_row.set_subtitle(
                _("check failed — {error}").format(
                    error=resp.get("error")
                    or _("daemon unreachable")))
            return False
        self._apply_about_update(resp)
        upd = resp.get("update") or {}
        if resp.get("update_available"):
            self.toast(_("update available: v{version} — "
                         "run 'sayit-ermano update'").format(
                version=resp["update_available"]))
        elif upd.get("error"):
            self.toast(_("update check failed ({error})").format(
                error=upd["error"]))
        return False

    def _open_route_guide(self, *_args) -> None:
        """Deep-link the update route guide (docs/guides/install.md#updates)
        with the session opener - never a browser import in-process."""
        from ... import update as update_mod
        subprocess.Popen(
            ["xdg-open", update_mod.ROUTE_GUIDE_URL],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
