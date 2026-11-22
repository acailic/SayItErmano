"""AI page: endpoint, prompt profiles, per-app rules, learned suggestions.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

import threading

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from ...i18n import _
from .common import _InstructionRow, _TextProxy


class AIPageMixin:
    def _build_ai(self) -> None:
        page = Adw.PreferencesPage(
            name="ai", icon_name="fluidvoice-polish-symbolic", title=_("AI")
        )
        # provider profiles: one click switches the whole endpoint
        # (base_url/model/key-env); values land in [ai] via set-config
        self._prov_group = Adw.PreferencesGroup(
            title=_("Provider profiles"),
            description=_("Named endpoint presets — selecting one switches "
                          "the AI polish provider instantly"),
        )
        self._prov_rows: dict[str, Adw.ActionRow] = {}
        self._prov_checks: dict[str, Gtk.CheckButton] = {}
        self._pending_provider_clear = False
        add_prov_row = Adw.ActionRow(
            title=_("Add profile"),
            subtitle=_("Manual entry, or start from a built-in template"),
        )
        add_btn = Gtk.Button(label=_("Add…"), css_classes=["flat"])
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.connect("clicked", lambda *_: self._provider_add_dialog())
        add_prov_row.add_suffix(add_btn)
        tmpl_btn = Gtk.Button(label=_("From template"), css_classes=["flat"])
        tmpl_btn.set_valign(Gtk.Align.CENTER)
        tmpl_btn.connect("clicked",
                         lambda *_: self._provider_template_dialog())
        add_prov_row.add_suffix(tmpl_btn)
        self._prov_group.add(add_prov_row)
        page.add(self._prov_group)

        grp = Adw.PreferencesGroup(
            title=_("AI polish"),
            description=_("Any OpenAI-compatible endpoint — Ollama, LM Studio, "
                          "OpenAI, Groq…"),
        )
        grp.add(self._switch("ai", "enabled", _("Enabled")))
        url_row = self._entry("ai", "base_url",
                              _("Base URL — http://localhost:11434/v1"))
        self.provider_img = Gtk.Image(pixel_size=18)
        self.provider_img.set_valign(Gtk.Align.CENTER)
        url_row.add_prefix(self.provider_img)
        url_row.connect("changed", lambda *_: self._update_provider_logo())
        url_row.connect("changed",
                        lambda *_: self._provider_queue_pointer_clear())
        grp.add(url_row)
        model_row = self._entry("ai", "model",
                                _("Model — e.g. qwen3:8b"))
        model_row.connect("changed",
                           lambda *_: self._provider_queue_pointer_clear())
        grp.add(model_row)
        grp.add(self._entry("ai", "api_key_env",
                            _("API key env var (preferred)")))
        grp.add(self._spin("ai", "temperature", _("Temperature"), 0.1, digits=1))
        grp.add(self._spin("ai", "timeout_seconds", _("Timeout (s)"), 5))
        grp.add(self._spin("ai", "max_retries", _("Max retries"), 1))
        grp.add(
            self._switch(
                "ai",
                "refusal_guard",
                _("Refusal guard"),
                subtitle=_("a reply that reads as a model refusal "
                           "(\"I'm sorry, I can't assist…\") is "
                           "never typed — the raw transcript is "
                           "used instead"),
            )
        )
        test_row = Adw.ActionRow(title=_("Test connection"))
        test_btn = Gtk.Button(label=_("Test"), css_classes=["suggested-action"])
        test_btn.set_valign(Gtk.Align.CENTER)
        self.test_out = Gtk.Label(
            css_classes=["dim-label"], wrap=True, max_width_chars=28
        )
        test_btn.connect("clicked", self._test_ai)
        test_row.add_suffix(self.test_out)
        test_row.add_suffix(test_btn)
        grp.add(test_row)
        page.add(grp)

        # Local polish (#21): one toggle, works offline. The daemon spawns
        # a managed llama.cpp server on demand and auto-points polish at
        # it while this is on; off = zero footprint (no process, no port).
        local = Adw.PreferencesGroup(
            title=_("Local polish"),
            description=_("Works offline — a bundled llama.cpp server is "
                          "spawned only while polishing, your text never "
                          "leaves this machine"),
        )
        local_switch = self._switch(
            "ai",
            "local_polish",
            _("Local polish (works offline)"),
            subtitle=_("no cloud key or endpoint needed — needs the chat "
                       "model (Settings → Models → AI polish model) and "
                       "an llama-server binary"),
        )
        local_switch.connect("notify::active", self._on_local_polish_toggled)
        local.add(local_switch)
        self._local_polish_status_row = Adw.ActionRow(
            title=_("Status"), subtitle="—", activatable=False, selectable=False
        )
        local.add(self._local_polish_status_row)
        stop_row = Adw.ActionRow(
            title=_("Stop server now"),
            subtitle=_("Frees the RAM the polish model uses; it starts "
                       "again on the next dictation"),
        )
        self._local_polish_stop_btn = Gtk.Button(label=_("Stop"))
        self._local_polish_stop_btn.set_valign(Gtk.Align.CENTER)
        self._local_polish_stop_btn.connect("clicked",
                                            self._on_local_polish_stop)
        stop_row.add_suffix(self._local_polish_stop_btn)
        local.add(stop_row)
        page.add(local)

        # prompt profiles (macOS parity): radio rows + per-row ⋯ menu +
        # Add; "Save" next to the editor below writes to the selection
        self.prof_grp = Adw.PreferencesGroup(
            title=_("Prompt profiles"),
            description=_("Named presets of the base prompt — selecting one "
                          "loads its text into the editor"),
        )
        self._profile_rows: dict[str, Adw.ActionRow] = {}
        self._profile_checks: dict[str, Gtk.CheckButton] = {}
        self._selected_profile_name: str | None = None
        self._profile_none_row = Adw.ActionRow(
            title=_("No profiles yet"),
            subtitle=_("Add one to save preset prompts"),
            activatable=False, selectable=False,
            css_classes=["dim-label"])
        add_prof_row = Adw.ActionRow(title=_("Add profile"))
        add_prof_btn = Gtk.Button(label=_("Add…"), css_classes=["flat"])
        add_prof_btn.set_valign(Gtk.Align.CENTER)
        add_prof_btn.connect("clicked", lambda *_: self._profile_add())
        add_prof_row.add_suffix(add_prof_btn)
        self._profile_add_row = add_prof_row
        self.prof_grp.add(add_prof_row)
        page.add(self.prof_grp)

        # custom base prompt editor (empty = the built-in dictation prompt;
        # the Prompt profiles group above saves/loads named presets of it)
        prompt_grp = Adw.PreferencesGroup(
            title=_("Base prompt"),
            description=_("The system prompt AI polish starts from "
                          "(empty = the built-in dictation prompt)"),
        )
        tv = Gtk.TextView(hexpand=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        buf.connect("changed", lambda *_: self._touch())
        self._rows[("ai", "base_prompt")] = _TextProxy(buf)
        prompt_grp.add(_InstructionRow(tv, title=_("Base prompt")))
        builtin_row = Adw.ActionRow(
            title=_("Built-in template"),
            subtitle=_("Start editing from the shipped dictation prompt"),
        )
        builtin_btn = Gtk.Button(label=_("Insert built-in"),
                                 css_classes=["flat"])
        builtin_btn.set_valign(Gtk.Align.CENTER)
        builtin_btn.connect("clicked", self._insert_builtin_prompt)
        builtin_row.add_suffix(builtin_btn)
        prompt_grp.add(builtin_row)
        save_prof_row = Adw.ActionRow(
            title=_("Save to profile"),
            subtitle=_("Write the editor contents into the selected profile"),
        )
        save_prof_btn = Gtk.Button(label=_("Save"),
                                   css_classes=["suggested-action"])
        save_prof_btn.set_valign(Gtk.Align.CENTER)
        save_prof_btn.connect("clicked", self._profile_save)
        save_prof_row.add_suffix(save_prof_btn)
        prompt_grp.add(save_prof_row)
        page.add(prompt_grp)

        self.rules_group = Adw.PreferencesGroup(
            title=_("Per-app prompts"),
            description=_("Extra polish instructions when dictating into a "
                          "matching app (first match wins, * = everywhere)"),
        )
        add_row = Adw.ActionRow(title=_("Add rule"))
        add_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_btn.connect("clicked", lambda *_: self._add_rule({}))
        add_row.add_suffix(add_btn)
        self.rules_group.add(add_row)
        page.add(self.rules_group)

        page.add(self._save_group())
        self._add_page(page)
        self.install_action("profile.rename", "s", self._profile_rename_action)
        self.install_action("profile.delete", "s", self._profile_delete_action)
        self.install_action("provider.rename", "s",
                            self._provider_rename_action)
        self.install_action("provider.delete", "s",
                            self._provider_delete_action)

    def _update_provider_logo(self) -> None:
        """Show the macOS-style provider logo matching the AI base URL."""
        from ..logos import logo_path, provider_for

        row = self._rows.get(("ai", "base_url"))
        text = row.get_text() if row is not None else ""
        dark = Adw.StyleManager.get_default().get_dark()
        path = logo_path(provider_for(text), dark)
        if path:
            try:
                self.provider_img.set_from_paintable(
                    Gdk.Texture.new_from_filename(path)
                )
                self.provider_img.set_visible(True)
                return
            except Exception:  # noqa: S110 — best-effort path; caller must proceed
                pass
        self.provider_img.set_visible(False)

    # -- local polish (bundled llama.cpp server, #21) -------------------------

    def _on_local_polish_toggled(self, switch: Adw.SwitchRow, _pspec) -> None:
        """One-click contract: enabling local polish also flips the AI
        polish master switch on (it implies polish; disabling local does
        not disable the cloud path)."""
        if not self._loading and switch.get_active():
            master = self._rows.get(("ai", "enabled"))
            if master is not None and isinstance(master, Adw.SwitchRow) \
                    and not master.get_active():
                master.set_active(True)  # fires its own _touch
        self._refresh_local_polish()

    def _on_local_polish_stop(self, _btn) -> None:
        """Stop the managed llama.cpp child now (memory hygiene)."""
        try:
            resp = self.c.local_polish_stop()
        except Exception as e:  # noqa: BLE001 - surfaced as a toast
            self.toast(_("stop failed: {err}").format(err=e))
            return
        state = (resp.get("local_polish") or {}) if resp.get("ok") else {}
        if resp.get("ok") is False:
            self.toast(str(resp.get("error") or _("stop failed")))
            return
        self.toast(_("stopped") if not state.get("running")
                   else _("still running?"))
        self._refresh_local_polish()

    def _refresh_local_polish(self) -> None:
        """Honest one-line state for the Local polish group: readiness
        probes are local (cheap); the running/port state comes from the
        daemon status block when it is up."""
        from ... import local_llm, model_catalog
        row = getattr(self, "_local_polish_status_row", None)
        if row is None:
            return
        ai = self.cfg.get("ai", {}) or {}
        name = str(ai.get("local_model")
                   or model_catalog.CHAT_MODEL_DEFAULT)
        if not ai.get("local_polish"):
            row.set_subtitle(_("off (zero footprint — no server, no port)"))
            return
        if not model_catalog.chat_model_downloaded(name):
            row.set_subtitle(
                _("model {name} not downloaded — Settings → Models → "
                  "AI polish model").format(name=name))
            return
        if not local_llm.find_llama_server(self.cfg):
            row.set_subtitle(
                _("llama-server binary not found — install an llama.cpp "
                  "package or set ai.local_server_binary"))
            return
        running, port = False, None
        try:
            st = (self.c.status() or {}).get("local_polish") or {}
            running, port = bool(st.get("running")), st.get("port")
        except Exception:  # noqa: S110,BLE001 - daemon down: readiness line is enough
            pass  # a status read failure degrades to "ready", never a crash
        if running:
            row.set_subtitle(
                _("running (127.0.0.1:{port}) — stops after {idle} s idle")
                .format(port=port, idle=ai.get("local_idle_s", 300)))
        else:
            row.set_subtitle(_("ready — the server spawns on the next "
                               "dictation"))

    def _test_ai(self, _btn) -> None:
        self.test_out.set_text(_("testing…"))
        url = self._get_text("ai", "base_url")
        model = self._get_text("ai", "model")

        def work():
            try:
                resp = self.c.test_ai(url, model)
            except Exception as e:  # surfaced inline
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(
                self.test_out.set_text,
                _("ok: {reply}").format(reply=resp["reply"])
                if resp.get("ok")
                else str(resp.get("error") or _("failed")),
            )

        threading.Thread(target=work, daemon=True).start()

    def _get_text(self, sec, key) -> str:
        row = self._rows.get((sec, key))
        return row.get_text() if isinstance(row, Adw.EntryRow) else ""

    def _insert_builtin_prompt(self, _btn) -> None:
        """Load the shipped dictation prompt into the editor (editing the
        ~1.5 kB prompt from an empty buffer is hostile)."""
        from ...ai.prompts import default_dictation_prompt

        self._rows[("ai", "base_prompt")].set_value(default_dictation_prompt())
        self._touch()

    def _load_profiles(self) -> None:
        """Rebuild the profile radio rows from the sidecar; failures
        degrade to an empty group."""
        self._profiles = self.c.prompt_profiles() or {}
        self._suppress_touch = True  # programmatic rebuild: no load/dirty
        try:
            for row in self._profile_rows.values():
                self.prof_grp.remove(row)
            self._profile_rows.clear()
            self._profile_checks.clear()
            has = bool(self._profiles)
            self._profile_none_row.set_visible(not has)
            self._profile_add_row.set_visible(True)
            if not has:
                self.prof_grp.add(self._profile_none_row)
            else:
                self.prof_grp.remove(self._profile_none_row)
            group: Gtk.CheckButton | None = None
            for name in self._profiles:
                check = Gtk.CheckButton()
                if group is None:
                    group = check
                else:
                    check.set_group(group)
                check.connect("toggled", self._on_profile_toggled, name)
                row = Adw.ActionRow(title=name)
                row.add_prefix(check)
                row.set_activatable_widget(check)
                menu = Gio.Menu()
                menu.append(_("Rename…"), f"win.profile.rename::{name}")
                menu.append(_("Delete"), f"win.profile.delete::{name}")
                mb = Gtk.MenuButton(icon_name="view-more-symbolic",
                                    css_classes=["flat"], menu_model=menu,
                                    valign=Gtk.Align.CENTER,
                                    tooltip_text=_("Profile actions"))
                row.add_suffix(mb)
                self._profile_rows[name] = row
                self._profile_checks[name] = check
                self.prof_grp.add(row)
            # keep the last selection when it survives, else the first
            sel = self._selected_profile_name
            if sel not in self._profiles:
                sel = next(iter(self._profiles), None)
            self._selected_profile_name = sel
            if sel is not None:
                self._profile_checks[sel].set_active(True)
        finally:
            self._suppress_touch = False

    def _on_profile_toggled(self, check: Gtk.CheckButton, name: str) -> None:
        if not check.get_active():
            return
        self._selected_profile_name = name
        if self._loading or self._suppress_touch:
            return
        # loading copies the profile text into the editor (dirty: the user
        # then Saves to persist it to config)
        self._rows[("ai", "base_prompt")].set_value(self._profiles[name])
        self._touch()

    def _selected_profile(self) -> str | None:
        return self._selected_profile_name

    def _ask_profile_name(self, heading: str, initial: str, done) -> None:
        dlg = Adw.MessageDialog(transient_for=self, modal=True, heading=heading)
        entry = Gtk.Entry(text=initial, hexpand=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("ok", _("OK"))
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def responded(_dlg, response):
            if response == "ok":
                done(entry.get_text().strip())
        dlg.connect("response", responded)
        self._name_dlg, self._name_entry = dlg, entry  # test/driver access
        dlg.present()
        entry.grab_focus()

    def _profile_add(self, *_args) -> None:
        def done(name: str) -> None:
            if not name:
                self.toast(_("Enter a profile name first"))
                return
            text = self._rows[("ai", "base_prompt")].get_value()
            resp = self.c.prompt_profile_save(name, text)
            self._after_profile_call(
                resp, _("Saved profile \u201c{name}\u201d").format(name=name))

        self._ask_profile_name(_("New profile name"), "", done)

    def _profile_save(self, *_args) -> None:
        name = self._selected_profile()
        if name is None:
            self.toast(_("Select a profile first"))
            return
        text = self._rows[("ai", "base_prompt")].get_value()
        resp = self.c.prompt_profile_save(name, text)
        self._after_profile_call(
            resp, _("Saved profile \u201c{name}\u201d").format(name=name))

    def _profile_rename_action(self, _win, _action, param) -> None:
        old = (param.get_string() if param is not None
               else self._selected_profile())
        if old is None or old not in self._profiles:
            return

        def done(new: str) -> None:
            if not new or new == old:
                return
            resp = self.c.prompt_profile_rename(old, new)
            self._selected_profile_name = new if resp.get("ok") else None
            self._after_profile_call(
                resp, _("Renamed to \u201c{name}\u201d").format(name=new))

        self._ask_profile_name(
            _("Rename \u201c{name}\u201d to").format(name=old), old, done)

    def _profile_delete_action(self, _win, _action, param) -> None:
        name = (param.get_string() if param is not None
                else self._selected_profile())
        if name is None or name not in self._profiles:
            return
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=_("Delete profile \u201c{name}\u201d?").format(name=name),
            body=_("The preset is removed from disk. The prompt saved in "
                  "your config is not touched."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("delete", _("Delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_delete_profile_response, name)
        dlg.present()

    def _on_delete_profile_response(self, _dlg, response, name: str) -> None:
        if response != "delete":
            return
        resp = self.c.prompt_profile_delete(name)
        self._after_profile_call(
            resp, _("Deleted profile \u201c{name}\u201d").format(name=name))

    def _after_profile_call(self, resp: dict, ok_msg: str) -> None:
        if resp.get("ok"):
            self._profiles = resp.get("profiles") or {}
            self._load_profiles()
            self.toast(ok_msg)
        else:
            self.toast(str(resp.get("error")
                           or _("profile operation failed")))

    # -- provider profiles ---------------------------------------------------

    def _provider_profiles_refresh(self) -> None:
        """Rebuild the provider radio rows: Custom (pointer "") plus every
        stored name; a broken sidecar degrades to just Custom."""
        self._suppress_touch = True
        try:
            for row in self._prov_rows.values():
                self._prov_group.remove(row)
            self._prov_rows.clear()
            self._prov_checks.clear()
            active = str(self.cfg.get("ai", {}).get("profile") or "")
            group: Gtk.CheckButton | None = None
            for name in ["", *sorted(self.c.provider_profiles())]:
                label = _("Custom (hand-set endpoint)") if not name else name
                check = Gtk.CheckButton()
                if group is None:
                    group = check
                else:
                    check.set_group(group)
                check.set_active(name == active)
                check.connect("toggled", self._on_provider_toggled, name)
                row = Adw.ActionRow(title=label)
                row.add_prefix(check)
                row.set_activatable_widget(check)
                if name:
                    menu = Gio.Menu()
                    menu.append(_("Rename…"), f"win.provider.rename::{name}")
                    menu.append(_("Delete"), f"win.provider.delete::{name}")
                    mb = Gtk.MenuButton(icon_name="view-more-symbolic",
                                        css_classes=["flat"], menu_model=menu,
                                        valign=Gtk.Align.CENTER,
                                        tooltip_text=_("Profile actions"))
                    row.add_suffix(mb)
                self._prov_rows[name] = row
                self._prov_checks[name] = check
                self._prov_group.add(row)
        finally:
            self._suppress_touch = False

    def _on_provider_toggled(self, check: Gtk.CheckButton, name: str) -> None:
        if not check.get_active() or self._loading or self._suppress_touch:
            return
        self._provider_apply(name)

    def _provider_apply(self, name: str) -> None:
        """Instant switch: name == "" only clears the pointer (Custom).
        The patch is built from the CLIENT's store view (the single source
        the UI renders), not the sidecar file directly."""
        try:
            profiles = self.c.provider_profiles()
            if name and name not in profiles:
                raise KeyError(f"no AI provider profile named {name!r}")
            body = ({"ai": {"profile": ""}} if not name
                    else {"ai": {**profiles[name], "profile": name}})
            resp = self.c.set_config(body)
            if not resp.get("ok"):
                raise RuntimeError(
                    "rejected: " + ", ".join(resp.get("rejected") or []))
            self.cfg.setdefault("ai", {}).update(body["ai"])
            self._pending_provider_clear = False
            self.toast(_("AI profile: {name}").format(
                name=name or _("Custom")))
        except Exception as e:
            self.toast(_("Profile switch failed: {error}").format(error=e))
        self._provider_profiles_refresh()

    def _provider_queue_pointer_clear(self) -> None:
        """Hand-edits to the endpoint make the active profile stale; the
        next Save writes ai.profile = "" (back to Custom)."""
        if self.cfg.get("ai", {}).get("profile"):
            self._pending_provider_clear = True

    def _after_provider_call(self, resp: dict, ok_msg: str) -> None:
        if resp.get("ok"):
            self.toast(ok_msg)
        else:
            self.toast(str(resp.get("error")
                           or _("profile operation failed")))
        self._provider_profiles_refresh()

    def _provider_add_dialog(self, *_args) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading=_("New provider profile"),
            body=_("Stored beside the config; only the env-var NAME is kept "
                  "- never the key itself."),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        entries: dict[str, Adw.EntryRow] = {}
        for key, title in (("name", _("Name (e.g. GLM)")),
                           ("base_url", _("Base URL")),
                           ("model", _("Model")),
                           ("api_key_env", _("API key env var"))):
            e = Adw.EntryRow(title=title)
            entries[key] = e
            box.append(e)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("add", _("Add"))
        dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        def responded(_dlg, response):
            if response != "add":
                return
            name = entries["name"].get_text().strip()
            if not name:
                self.toast(_("Enter a profile name first"))
                return
            profile = {
                "base_url": entries["base_url"].get_text().strip(),
                "model": entries["model"].get_text().strip(),
                "api_key_env": entries["api_key_env"].get_text().strip(),
            }
            resp = self.c.provider_profile_save(name, profile)
            self._after_provider_call(
                resp, _("Added profile \u201c{name}\u201d").format(name=name))

        dlg.connect("response", responded)
        dlg.present()

    def _provider_template_dialog(self, *_args) -> None:
        from ...ai.provider_profiles import TEMPLATES
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True, heading=_("Add from template"),
            body=_("Copied into your profiles - edit freely afterwards."),
        )
        names = list(TEMPLATES)
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for name in names:
            t = TEMPLATES[name]
            row = Adw.ActionRow(
                title=name,
                subtitle=f"{t['model'] or _('(pick a model)')} - {t['base_url']}")
            box.append(row)

        def activated(_box, row):
            name = names[row.get_index()]
            dlg.close()
            final = name
            while final in self.c.provider_profiles():
                final += " 2"
            resp = self.c.provider_profile_save(
                final, dict(TEMPLATES[name]))
            self._after_provider_call(
                resp, _("Added profile \u201c{name}\u201d from template")
                .format(name=final))

        box.connect("row-activated", activated)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", _("Close"))
        dlg.present()

    def _provider_rename_action(self, _win, _action, param) -> None:
        old = (param.get_string() if param is not None else "")
        if old not in self.c.provider_profiles():
            return

        def done(new: str) -> None:
            if not new or new == old:
                return
            self._provider_rename_confirmed(old, new)

        self._ask_profile_name(
            _("Rename \u201c{name}\u201d to").format(name=old), old, done)

    def _provider_rename_confirmed(self, old: str, new: str) -> None:
        resp = self.c.provider_profile_rename(old, new)
        if resp.get("ok") and \
                self.cfg.get("ai", {}).get("profile") == old:
            self.c.set_config({"ai": {"profile": new}})
            self.cfg["ai"]["profile"] = new
        self._after_provider_call(
            resp, _("Renamed to \u201c{name}\u201d").format(name=new))

    def _provider_delete_action(self, _win, _action, param) -> None:
        name = (param.get_string() if param is not None else "")
        if name not in self.c.provider_profiles():
            return
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading=_("Delete profile \u201c{name}\u201d?").format(name=name),
            body=_("Removed from the profile store. Settings and the tray "
                  "menu update immediately."),
        )
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("delete", _("Delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_delete_provider_response, name)
        dlg.present()

    def _on_delete_provider_response(self, _dlg, response, name: str) -> None:
        if response != "delete":
            return
        self._provider_delete_confirmed(name)

    def _provider_delete_confirmed(self, name: str) -> None:
        resp = self.c.provider_profile_delete(name)
        if resp.get("ok") and \
                self.cfg.get("ai", {}).get("profile") == name:
            self.c.set_config({"ai": {"profile": ""}})
            self.cfg["ai"]["profile"] = ""
        self._after_provider_call(
            resp, _("Deleted profile \u201c{name}\u201d").format(name=name))

    def _load_rules(self, rules: list) -> None:
        for r in list(self._rule_rows):
            self.rules_group.remove(r["expander"])
        self._rule_rows = []
        for rule in rules:
            self._add_rule(rule)

    def _add_rule(self, rule: dict) -> None:
        exp = Adw.ExpanderRow(title=", ".join(rule.get("apps", []))
                              or _("New rule"))
        apps = Adw.EntryRow(title=_("App patterns (comma-separated)"))
        apps.set_text(", ".join(rule.get("apps", [])))

        tv = Gtk.TextView(hexpand=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        buf.set_text(str(rule.get("instructions", "")))

        remove_btn = Gtk.Button(
            icon_name="user-trash-symbolic", css_classes=["flat", "destructive-action"]
        )
        head = Adw.ActionRow(title=_("Instructions"))
        head.add_suffix(remove_btn)
        exp.add_row(apps)
        exp.add_row(head)
        exp.add_row(_InstructionRow(tv))
        row_ref = {"expander": exp, "apps": apps, "buf": buf}
        remove_btn.connect("clicked", self._remove_rule, row_ref)

        def sync_title(*_):
            exp.set_title(apps.get_text().strip() or _("New rule"))
            self._touch()

        apps.connect("changed", sync_title)
        buf.connect("changed", lambda *_: self._touch())
        self._rule_rows.append(row_ref)
        self.rules_group.add(exp)
        exp.set_expanded(not rule.get("apps"))

    def _remove_rule(self, _btn, row_ref: dict) -> None:
        self.rules_group.remove(row_ref["expander"])
        self._rule_rows.remove(row_ref)
        self._touch()

    def _collect_rules(self):
        rules = []
        for r in self._rule_rows:
            apps = [a.strip() for a in r["apps"].get_text().split(",") if a.strip()]
            text = (
                r["buf"]
                .get_text(r["buf"].get_start_iter(), r["buf"].get_end_iter(), False)
                .strip()
            )
            if apps and text:
                rules.append({"apps": apps, "instructions": text})
            elif apps or text:
                self.toast(
                    _("Incomplete per-app rule skipped "
                      "(needs apps and instructions)")
                )
        return rules

    def _load_suggestions(self) -> None:
        """Rebuild the Suggested words group through the client (direct
        reads: config + history + decision store). Any failure — daemon
        down, unreadable files — just hides the group."""
        try:
            suggestions = self.c.dict_suggestions() or []
        except Exception:
            suggestions = []
        self._rebuild_suggestions(suggestions)

    def _rebuild_suggestions(self, suggestions: list) -> None:
        for ref in list(self._suggest_rows):
            self.suggest_group.remove(ref["row"])
        self._suggest_rows = []
        for s in suggestions:
            heard = str(s.get("heard", ""))
            corrected = str(s.get("corrected", ""))
            row = Adw.ActionRow(
                title=f"{heard} → {corrected}",
                subtitle=_("seen {n}×").format(n=s.get("count", 0))
            )
            ref = {"row": row, "heard": heard, "corrected": corrected}
            accept_btn = Gtk.Button(
                label=_("Accept"), css_classes=["flat", "suggested-action"]
            )
            accept_btn.set_valign(Gtk.Align.CENTER)
            dismiss_btn = Gtk.Button(label=_("Dismiss"), css_classes=["flat"])
            dismiss_btn.set_valign(Gtk.Align.CENTER)
            accept_btn.connect("clicked", self._on_suggestion_accept, ref)
            dismiss_btn.connect("clicked", self._on_suggestion_dismiss, ref)
            row.add_suffix(dismiss_btn)
            row.add_suffix(accept_btn)
            self.suggest_group.add(row)
            self._suggest_rows.append(ref)
        self.suggest_group.set_visible(bool(self._suggest_rows))

    def _on_suggestion_accept(self, _btn, ref: dict) -> None:
        """Merge the pair into processing.dictionary through the client's
        validated save path, then refresh the dictionary editor WITHOUT a
        full _load() (other unsaved edits survive) and re-render the group.
        Not a settings edit — no dirty flag."""
        try:
            resp = self.c.dict_suggestion_accept(ref["heard"], ref["corrected"])
        except Exception:
            self.toast(_("Could not add the word (save failed)"))
            return
        if not resp.get("ok"):
            self.toast(_("Could not add the word (save rejected)"))
            return  # keep the row; nothing was recorded
        self._load_dictionary(resp.get("dictionary") or [])
        self.toast(_("Added to dictionary"))
        self._load_suggestions()

    def _on_suggestion_dismiss(self, _btn, ref: dict) -> None:
        """Record the pair as permanently dismissed (never resuggested)."""
        try:
            self.c.dict_suggestion_dismiss(ref["heard"], ref["corrected"])
        except Exception:  # noqa: S110 — best-effort path; caller must proceed
            pass  # a failed dismiss leaves the row in place on rebuild
        self._load_suggestions()
