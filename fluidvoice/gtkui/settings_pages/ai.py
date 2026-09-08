"""AI page: endpoint, prompt profiles, per-app rules, learned suggestions.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

import threading

from gi.repository import Adw, Gdk, GLib, Gtk

from .common import _InstructionRow, _TextProxy


class AIPageMixin:
    def _build_ai(self) -> None:
        page = Adw.PreferencesPage(
            name="ai", icon_name="fluidvoice-polish-symbolic", title="AI Polish"
        )
        grp = Adw.PreferencesGroup(
            title="AI polish",
            description="Any OpenAI-compatible endpoint — Ollama, LM Studio, "
            "OpenAI, Groq…",
        )
        grp.add(self._switch("ai", "enabled", "Enabled"))
        url_row = self._entry("ai", "base_url", "Base URL — http://localhost:11434/v1")
        self.provider_img = Gtk.Image(pixel_size=18)
        self.provider_img.set_valign(Gtk.Align.CENTER)
        url_row.add_prefix(self.provider_img)
        url_row.connect("changed", lambda *_: self._update_provider_logo())
        grp.add(url_row)
        grp.add(self._entry("ai", "model", "Model — e.g. qwen3:8b"))
        grp.add(self._entry("ai", "api_key_env", "API key env var (preferred)"))
        grp.add(self._spin("ai", "temperature", "Temperature", 0.0, 2.0, 0.1, digits=1))
        grp.add(self._spin("ai", "timeout_seconds", "Timeout (s)", 1, 3600, 5))
        grp.add(self._spin("ai", "max_retries", "Max retries", 0, 10, 1))
        grp.add(
            self._switch(
                "ai",
                "refusal_guard",
                "Refusal guard",
                subtitle="a reply that reads as a model refusal "
                "(\"I'm sorry, I can't assist…\") is "
                "never typed — the raw transcript is "
                "used instead",
            )
        )
        test_row = Adw.ActionRow(title="Test connection")
        test_btn = Gtk.Button(label="Test", css_classes=["suggested-action"])
        test_btn.set_valign(Gtk.Align.CENTER)
        self.test_out = Gtk.Label(
            css_classes=["dim-label"], wrap=True, max_width_chars=28
        )
        test_btn.connect("clicked", self._test_ai)
        test_row.add_suffix(self.test_out)
        test_row.add_suffix(test_btn)
        grp.add(test_row)
        page.add(grp)

        # prompt profiles: the profile bar above the prompt editor (loading
        # copies the text into the editor; config.toml stays the source of
        # truth for what is active)
        prof_grp = Adw.PreferencesGroup(
            title="Prompt profiles",
            description="Named presets of the base prompt - loading copies "
            "the text into the editor",
        )
        self._profile_combo = Adw.ComboRow(
            title="Profile", subtitle="Select to load it into the editor"
        )
        self._profile_combo.connect("notify::selected", self._on_profile_selected)
        prof_grp.add(self._profile_combo)
        self._profile_name_row = Adw.EntryRow(title="Profile name")
        # no _touch: the name feeds profile CRUD (immediate), not the
        # config save flow
        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.connect("clicked", self._profile_save)
        rename_btn = Gtk.Button(label="Rename", css_classes=["flat"])
        rename_btn.set_valign(Gtk.Align.CENTER)
        rename_btn.connect("clicked", self._profile_rename)
        del_btn = Gtk.Button(label="Delete", css_classes=["flat", "destructive-action"])
        del_btn.set_valign(Gtk.Align.CENTER)
        del_btn.connect("clicked", self._confirm_delete_profile)
        self._profile_name_row.add_suffix(del_btn)
        self._profile_name_row.add_suffix(rename_btn)
        self._profile_name_row.add_suffix(save_btn)
        prof_grp.add(self._profile_name_row)
        page.add(prof_grp)

        # custom base prompt editor (empty = the built-in dictation prompt;
        # the Prompt profiles group above saves/loads named presets of it)
        prompt_grp = Adw.PreferencesGroup(
            title="Base prompt",
            description="The system prompt AI polish starts from "
            "(empty = the built-in dictation prompt)",
        )
        tv = Gtk.TextView(hexpand=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        buf.connect("changed", lambda *_: self._touch())
        self._rows[("ai", "base_prompt")] = _TextProxy(buf)
        prompt_grp.add(_InstructionRow(tv, title="Base prompt"))
        builtin_row = Adw.ActionRow(
            title="Built-in template",
            subtitle="Start editing from the shipped dictation prompt",
        )
        builtin_btn = Gtk.Button(label="Insert built-in", css_classes=["flat"])
        builtin_btn.set_valign(Gtk.Align.CENTER)
        builtin_btn.connect("clicked", self._insert_builtin_prompt)
        builtin_row.add_suffix(builtin_btn)
        prompt_grp.add(builtin_row)
        page.add(prompt_grp)

        self.rules_group = Adw.PreferencesGroup(
            title="Per-app prompts",
            description="Extra polish instructions when dictating into a "
            "matching app (first match wins, * = everywhere)",
        )
        add_row = Adw.ActionRow(title="Add rule")
        add_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_btn.connect("clicked", lambda *_: self._add_rule({}))
        add_row.add_suffix(add_btn)
        self.rules_group.add(add_row)
        page.add(self.rules_group)

        cmd = Adw.PreferencesGroup(
            title="Command mode",
            description="Voice → terminal agent. Every command needs confirmation.",
        )
        cmd.add(self._spin("command", "max_turns", "Max agent turns", 1, 20, 1))
        cmd.add(
            self._entry("command", "working_dir", "Working directory (empty = home)")
        )
        cmd.add(
            self._spin(
                "command",
                "timeout_seconds",
                "Command timeout (s)",
                1,
                3600,
                5,
                digits=1,
            )
        )
        cmd.add(
            self._spin(
                "command",
                "confirm_timeout_s",
                "Confirmation timeout (s)",
                5,
                600,
                5,
                digits=1,
            )
        )
        page.add(cmd)
        page.add(self._save_group())
        self.add(page)

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
            except Exception:
                pass
        self.provider_img.set_visible(False)

    def _test_ai(self, _btn) -> None:
        self.test_out.set_text("testing…")
        url = self._get_text("ai", "base_url")
        model = self._get_text("ai", "model")

        def work():
            try:
                resp = self.c.test_ai(url, model)
            except Exception as e:  # surfaced inline
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(
                self.test_out.set_text,
                f"ok: {resp['reply']}"
                if resp.get("ok")
                else str(resp.get("error") or "failed"),
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
        """Rebuild the profile combo from the sidecar; failures degrade to
        an empty combo."""
        self._profiles = self.c.prompt_profiles() or {}
        model = Gtk.StringList()
        names = list(self._profiles)
        if names:
            for n in names:
                model.append(n)
        else:
            model.append("\u2014 none \u2014")
        self._suppress_touch = True  # programmatic rebuild: no load/dirty
        try:
            self._profile_combo.set_model(model)
            self._profile_combo.set_selected(0)
        finally:
            self._suppress_touch = False

    def _selected_profile(self) -> str | None:
        if not self._profiles:
            return None
        item = self._profile_combo.get_selected_item()
        name = item.get_string() if item is not None else None
        return name if name in self._profiles else None

    def _on_profile_selected(self, *_args) -> None:
        """Loading copies the profile text into the editor (dirty: the user
        then Saves to persist it to config)."""
        if self._loading or self._suppress_touch:
            return
        name = self._selected_profile()
        if name is None:
            return
        self._rows[("ai", "base_prompt")].set_value(self._profiles[name])
        self._touch()

    def _profile_save(self, *_args) -> None:
        name = self._profile_name_row.get_text().strip()
        if not name:
            self.toast("Enter a profile name first")
            return
        text = self._rows[("ai", "base_prompt")].get_value()
        resp = self.c.prompt_profile_save(name, text)
        self._after_profile_call(resp, f"Saved profile \u201c{name}\u201d")

    def _profile_rename(self, *_args) -> None:
        old = self._selected_profile()
        if old is None:
            self.toast("Select a profile to rename first")
            return
        new = self._profile_name_row.get_text().strip()
        if not new:
            self.toast("Enter a new name first")
            return
        resp = self.c.prompt_profile_rename(old, new)
        self._after_profile_call(resp, f"Renamed to \u201c{new}\u201d")

    def _confirm_delete_profile(self, _btn) -> None:
        name = self._selected_profile()
        if name is None:
            self.toast("Select a profile to delete first")
            return
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=f"Delete profile \u201c{name}\u201d?",
            body="The preset is removed from disk. The prompt saved in your "
            "config is not touched.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_delete_profile_response, name)
        dlg.present()

    def _on_delete_profile_response(self, _dlg, response, name: str) -> None:
        if response != "delete":
            return
        resp = self.c.prompt_profile_delete(name)
        self._after_profile_call(resp, f"Deleted profile \u201c{name}\u201d")

    def _after_profile_call(self, resp: dict, ok_msg: str) -> None:
        if resp.get("ok"):
            self._profiles = resp.get("profiles") or {}
            self._load_profiles()
            self.toast(ok_msg)
        else:
            self.toast(str(resp.get("error") or "profile operation failed"))

    def _load_rules(self, rules: list) -> None:
        for r in list(self._rule_rows):
            self.rules_group.remove(r["expander"])
        self._rule_rows = []
        for rule in rules:
            self._add_rule(rule)

    def _add_rule(self, rule: dict) -> None:
        exp = Adw.ExpanderRow(title=", ".join(rule.get("apps", [])) or "New rule")
        apps = Adw.EntryRow(title="App patterns (comma-separated)")
        apps.set_text(", ".join(rule.get("apps", [])))

        tv = Gtk.TextView(hexpand=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        buf.set_text(str(rule.get("instructions", "")))

        remove_btn = Gtk.Button(
            icon_name="user-trash-symbolic", css_classes=["flat", "destructive-action"]
        )
        head = Adw.ActionRow(title="Instructions")
        head.add_suffix(remove_btn)
        exp.add_row(apps)
        exp.add_row(head)
        exp.add_row(_InstructionRow(tv))
        row_ref = {"expander": exp, "apps": apps, "buf": buf}
        remove_btn.connect("clicked", self._remove_rule, row_ref)

        def sync_title(*_):
            exp.set_title(apps.get_text().strip() or "New rule")
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
                    "Incomplete per-app rule skipped (needs apps and instructions)"
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
                title=f"{heard} → {corrected}", subtitle=f"seen {s.get('count', 0)}×"
            )
            ref = {"row": row, "heard": heard, "corrected": corrected}
            accept_btn = Gtk.Button(
                label="Accept", css_classes=["flat", "suggested-action"]
            )
            accept_btn.set_valign(Gtk.Align.CENTER)
            dismiss_btn = Gtk.Button(label="Dismiss", css_classes=["flat"])
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
            self.toast("Could not add the word (save failed)")
            return
        if not resp.get("ok"):
            self.toast("Could not add the word (save rejected)")
            return  # keep the row; nothing was recorded
        self._load_dictionary(resp.get("dictionary") or [])
        self.toast("Added to dictionary")
        self._load_suggestions()

    def _on_suggestion_dismiss(self, _btn, ref: dict) -> None:
        """Record the pair as permanently dismissed (never resuggested)."""
        try:
            self.c.dict_suggestion_dismiss(ref["heard"], ref["corrected"])
        except Exception:
            pass  # a failed dismiss leaves the row in place on rebuild
        self._load_suggestions()
