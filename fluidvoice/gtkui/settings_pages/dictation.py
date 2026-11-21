"""Dictation page: hotkeys + capture, mic picker + priority, custom dictionary, formatting triggers.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ...config import KNOWN_LANGUAGES
from ...i18n import _
from .common import _ActionTriggersProxy, _ExtraShortcutsProxy, _ListProxy


class DictationPageMixin:
    def _build_dictation(self) -> None:
        page = Adw.PreferencesPage(
            name="dictation",
            icon_name="fluidvoice-dictation-symbolic",
            title=_("Dictation"),
        )

        # sections (org plan 5.5): one builder per group; this
        # orchestrator only assembles the page
        self._section_hotkeys(page)
        self._section_languages(page)
        self._section_mic(page)
        self._section_preview(page)
        self._section_polish(page)
        self._section_dictionary(page)
        self._section_insertion(page)
        self._section_commands(page)


        page.add(self._save_group())
        self._add_page(page)

    def _fill_mics(self) -> None:
        values: list[tuple[str, str]] = [(_("Auto (system default)"), "")]
        for m in self.c.mics():
            label = m["description"] + (_("  · default") if m.get("default") else "")
            values.append((label, m["name"]))
        current = str(self.cfg.get("recording", {}).get("device", "") or "")
        value_list = [v for _l, v in values]
        if current and current not in value_list:
            values.append((current + _("  · saved"), current))
            value_list.append(current)
        model = Gtk.StringList()
        for label, _v in values:
            model.append(label)
        self.mic_row.set_model(model)
        self._rows[("recording", "device")] = self.mic_row
        self._combo_values[("recording", "device")] = value_list
        self._loading = True
        self.mic_row.set_selected(
            value_list.index(current) if current in value_list else 0
        )
        self._loading = False

    def _load_mic_priority(self, patterns: list) -> None:
        for ref in list(self._mic_prio_rows):
            self.mic_prio_group.remove(ref["row"])
        self._mic_prio_rows = []
        for pattern in patterns:
            self._add_mic_prio(str(pattern))

    def _add_mic_prio(self, value: str) -> None:
        row = Adw.EntryRow(title=_("Pattern"))
        row.set_text(value)
        row.connect("changed", lambda *_: self._touch())
        up = Gtk.Button(
            icon_name="go-up-symbolic", css_classes=["flat"], tooltip_text=_("Move up")
        )
        down = Gtk.Button(
            icon_name="go-down-symbolic", css_classes=["flat"], tooltip_text=_("Move down")
        )
        rm = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            tooltip_text=_("Remove this pattern"),
        )
        ref = {"row": row, "up": up, "down": down}
        up.connect("clicked", lambda *_: self._move_mic_prio(ref, -1))
        down.connect("clicked", lambda *_: self._move_mic_prio(ref, 1))
        rm.connect("clicked", lambda *_: self._remove_mic_prio(ref))
        row.add_suffix(up)
        row.add_suffix(down)
        row.add_suffix(rm)
        self._mic_prio_rows.append(ref)
        self._rebuild_mic_prio()

    def _move_mic_prio(self, ref: dict, delta: int) -> None:
        i = self._mic_prio_rows.index(ref)
        j = i + delta
        if not 0 <= j < len(self._mic_prio_rows):
            return  # already at the edge
        self._mic_prio_rows[i], self._mic_prio_rows[j] = (
            self._mic_prio_rows[j],
            self._mic_prio_rows[i],
        )
        self._rebuild_mic_prio()

    def _rebuild_mic_prio(self) -> None:
        # the same widgets are re-added, so entered text survives
        for ref in list(self._mic_prio_rows):
            self.mic_prio_group.remove(ref["row"])
        self.mic_prio_group.remove(self._mic_prio_add_row)
        self.mic_prio_group.add(self._mic_prio_add_row)
        for ref in self._mic_prio_rows:
            self.mic_prio_group.add(ref["row"])
        last = len(self._mic_prio_rows) - 1
        for i, ref in enumerate(self._mic_prio_rows):
            ref["up"].set_sensitive(i > 0)
            ref["down"].set_sensitive(i < last)

    def _remove_mic_prio(self, ref: dict) -> None:
        self.mic_prio_group.remove(ref["row"])
        self._mic_prio_rows.remove(ref)
        self._rebuild_mic_prio()
        self._touch()

    def _collect_mic_priority(self) -> list[str]:
        return [
            r["row"].get_text().strip()
            for r in self._mic_prio_rows
            if r["row"].get_text().strip()
        ]

    def _load_dictionary(self, entries: list) -> None:
        for r in list(self._dict_rows):
            self.dict_group.remove(r["exp"])
        self._dict_rows = []
        for entry in entries:
            self._add_dict_word(entry)

    def _add_dict_word(self, entry: dict) -> None:
        exp = Adw.ExpanderRow(title=", ".join(entry.get("triggers", []))
                               or _("New word"))
        trig = Adw.EntryRow(title=_("Triggers (comma-separated)"))
        trig.set_text(", ".join(entry.get("triggers", [])))
        repl = Adw.EntryRow(title=_("Replacement"))
        repl.set_text(str(entry.get("replacement", "")))
        rm = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            tooltip_text=_("Remove this word"),
        )
        exp.add_suffix(rm)
        exp.add_row(trig)
        exp.add_row(repl)
        ref = {"exp": exp, "trig": trig, "repl": repl}
        rm.connect("clicked", self._remove_dict_word, ref)

        def sync_title(*_):
            exp.set_title(trig.get_text().strip() or _("New word"))
            self._touch()

        trig.connect("changed", sync_title)
        repl.connect("changed", lambda *_: self._touch())
        self._dict_rows.append(ref)
        self.dict_group.add(exp)
        exp.set_expanded(not entry.get("triggers"))

    def _remove_dict_word(self, _btn, ref: dict) -> None:
        self.dict_group.remove(ref["exp"])
        self._dict_rows.remove(ref)
        self._touch()

    def _collect_dictionary(self) -> list:
        entries = []
        for r in self._dict_rows:
            triggers = [t.strip() for t in r["trig"].get_text().split(",") if t.strip()]
            replacement = r["repl"].get_text().strip()
            if triggers and replacement:
                entries.append({"triggers": triggers, "replacement": replacement})
            elif triggers or replacement:
                self.toast(
                    _("Incomplete dictionary word skipped (needs "
                      "triggers and a replacement)")
                )
        return entries

    # -- language cycle editor (moved from the General page, macOS parity) --

    def _load_language_cycle(self, codes: list) -> None:
        for ref in list(self._cycle_rows):
            self.cycle_group.remove(ref["row"])
        self._cycle_rows = []
        for code in codes:
            self._add_cycle_lang(str(code))

    def _add_cycle_lang(self, value: str) -> None:
        row = Adw.EntryRow(title=_("Code — e.g. auto, en, sl"))
        row.set_text(value)
        row.connect("changed", self._on_cycle_lang_changed)
        up = Gtk.Button(
            icon_name="go-up-symbolic", css_classes=["flat"], tooltip_text=_("Move up")
        )
        down = Gtk.Button(
            icon_name="go-down-symbolic", css_classes=["flat"], tooltip_text=_("Move down")
        )
        rm = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            tooltip_text=_("Remove this language"),
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

    def _section_hotkeys(self, page: Adw.PreferencesPage) -> None:
        """Hotkeys: keys, extra shortcuts + prompt profiles, modifiers."""
        hk = Adw.PreferencesGroup(title=_("Hotkeys"))
        hk.add(
            self._entry(
                "hotkey", "key",
                _("Dictation key — e.g. Right_Control, F9"), capture=True
            )
        )
        hk.add(
            self._combo(
                "hotkey",
                "mode",
                _("Mode"),
                subtitle=_("tap to start/stop · hold = push-to-talk · "
                           "both: quick tap toggles, holding talks · "
                           "modifier-only keys need toggle"),
            )
        )
        hk.add(
            self._entry(
                "hotkey", "cancel_key",
                _("Cancel key — discards a take"), capture=True
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "rewrite_key",
                _("Rewrite key (optional, needs AI)"),
                capture=True,
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "command_key",
                _("Command key (optional, needs AI)"),
                capture=True,
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "paste_key",
                _("Paste-last key (optional) — re-types last text"),
                capture=True,
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "language_key",
                _("Cycle-language key (optional) — steps language_cycle"),
                capture=True,
            )
        )

        # B1: up to two extra dictation shortcuts, each optionally bound
        # to a named prompt profile (the primary hotkey stays above)
        extra_pairs = []
        from ...ai.profiles import load_profiles

        profile_names = [_("(none)")] + sorted(load_profiles())
        for i in (2, 3):
            key_row = Adw.EntryRow(title=_(
                "Extra dictation shortcut {n} (optional)").format(n=i))
            key_row.connect("changed", lambda *_: self._touch())
            btn = Gtk.Button(
                icon_name="media-record-symbolic",
                css_classes=["flat"],
                tooltip_text=_("Press a key…"),
            )
            btn.connect("clicked", self._start_capture, key_row)
            key_row.add_suffix(btn)
            hk.add(key_row)
            combo = Adw.ComboRow(
                title=_("Shortcut {n} prompt profile").format(n=i),
                subtitle=_("polishes takes started by this shortcut"),
            )
            combo.set_model(Gtk.StringList(strings=profile_names))
            combo.connect("notify::selected", lambda *_: self._touch())
            hk.add(combo)
            extra_pairs.append((key_row, combo))
        self._rows[("hotkey", "extra_shortcuts")] = _ExtraShortcutsProxy(extra_pairs)
        mods = Adw.ActionRow(
            title=_("Extra modifiers"),
            subtitle=_("held in addition to the dictation key")
        )
        for mod in ("ctrl", "alt", "shift", "super"):
            tb = Gtk.ToggleButton(label=mod, css_classes=["flat"])
            tb.set_valign(Gtk.Align.CENTER)
            tb.connect("toggled", lambda *_: self._touch())
            self._mod_toggles[mod] = tb
            mods.add_suffix(tb)
        hk.add(mods)
        page.add(hk)

    def _section_languages(self, page: Adw.PreferencesPage) -> None:
        """Language cycle + wrong-language whitelist."""

        # Languages (macOS parity: primary language lives on General; the
        # cycle + wrong-language guard live here, next to the cycle key)
        self.cycle_group = Adw.PreferencesGroup(
            title=_("Languages"),
            description=_("The cycle-language hotkey steps this list — e.g. "
                          "auto, en, sl (runtime state; never persisted; "
                          "empty = cycle key off)"),
        )
        wl = Adw.EntryRow(title=_("Language whitelist — e.g. sl, en"))
        wl.connect("changed", lambda *_: self._touch())
        self._rows[("general", "language_whitelist")] = _ListProxy(wl)
        self.cycle_group.add(wl)
        self._cycle_add_row = Adw.ActionRow(title=_("Add language"))
        add_lang_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_lang_btn.connect("clicked", lambda *_: self._add_cycle_lang(""))
        self._cycle_add_row.add_suffix(add_lang_btn)
        self.cycle_group.add(self._cycle_add_row)
        page.add(self.cycle_group)

    def _section_mic(self, page: Adw.PreferencesPage) -> None:
        """Microphone picker, recorder watchdogs, mic priority fallback."""

        mic = Adw.PreferencesGroup(title=_("Microphone and recording"))
        self.mic_row = Adw.ComboRow(
            title=_("Microphone"), subtitle=_("Auto follows the system default")
        )
        refresh_btn = Gtk.Button(
            icon_name="view-refresh-symbolic",
            css_classes=["flat"],
            tooltip_text=_("Refresh list"),
        )
        refresh_btn.connect("clicked", lambda *_: self._fill_mics())
        self.mic_row.add_suffix(refresh_btn)
        self.mic_row.connect("notify::selected", lambda *_: self._touch())
        mic.add(self.mic_row)
        mic.add(
            self._combo(
                "recording",
                "command",
                _("Recorder"),
            )
        )
        mic.add(self._spin("recording", "max_seconds", _("Max duration (s)"), 5))
        mic.add(
            self._spin(
                "recording",
                "first_pcm_timeout",
                _("No-audio timeout (s)"),
                0.5,
                digits=1,
                subtitle=_("0 = off; stops a muted/wrong mic fast"),
            )
        )
        mic.add(
            self._switch(
                "recording",
                "skip_silent",
                _("Skip silent takes"),
                _("Discard obviously-silent recordings ≤ 4 s"),
            )
        )
        mic.add(
            self._switch(
                "recording",
                "pause_media",
                _("Pause media"),
                _("Pause MPRIS players while dictating"),
            )
        )
        page.add(mic)

        self.mic_prio_group = Adw.PreferencesGroup(
            title=_("Microphone priority"),
            description=_("Ordered name patterns (e.g. bluez for a Bluetooth "
                          "headset). When the chosen microphone disappears, "
                          "the first available match is used."),
        )
        self._mic_prio_add_row = Adw.ActionRow(title=_("Add pattern"))
        add_p_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_p_btn.connect("clicked", lambda *_: self._add_mic_prio(""))
        self._mic_prio_add_row.add_suffix(add_p_btn)
        self.mic_prio_group.add(self._mic_prio_add_row)
        page.add(self.mic_prio_group)

    def _section_preview(self, page: Adw.PreferencesPage) -> None:
        """Live preview (pill overlay)."""

        preview = Adw.PreferencesGroup(
            title=_("Live preview"),
            description=_("The pill overlay with partial text while recording"),
        )
        preview.add(self._switch("recording", "preview_enabled", _("Enabled")))
        preview.add(
            self._combo(
                "recording",
                "preview_mode",
                _("Mode"),
                labels={"notify": _("notifications only")},
            )
        )
        preview.add(
            self._combo(
                "recording",
                "preview_overlay_size",
                _("Size"),
                subtitle=_("macOS size presets"),
            )
        )
        preview.add(
            self._spin(
                "recording", "preview_bottom_offset",
                _("Bottom offset (px)"), 2
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_interval",
                _("Update interval (s)"),
                0.1,
                digits=1,
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_min_audio",
                _("First partial after (s)"),
                0.1,
                digits=1,
            )
        )
        preview.add(
            self._switch(
                "recording",
                "preview_segmented",
                _("Streaming (segmented)"),
                _("constant-cost windows instead of whole-take re-decode; "
                  "all backends"),
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_segment_s",
                _("Segment window (s)"),
                0.5,
                digits=1,
            )
        )
        preview.add(
            self._switch(
                "recording",
                "preview_conf_gate",
                _("Confidence gate"),
                _("hide preview words the model scored shaky "
                  "(wrong-word suppression on hard audio)"),
            )
        )
        preview.add(
            self._switch(
                "recording",
                "overlay_chips",
                _("Hover action chips"),
                _("hover the pill while recording for copy/paste/cancel "
                  "buttons (X11)"),
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_vad_silence_s",
                _("Stop after silence (s)"),
                0.5,
                digits=1,
                subtitle=_("0 keeps the old behavior "
                           "(hotkey stops every take)"),
            )
        )
        page.add(preview)

    def _section_polish(self, page: Adw.PreferencesPage) -> None:
        """Text polish: fillers, spoken triggers, punctuation, GAAV, spoken send."""

        polish = Adw.PreferencesGroup(title=_("Text polish"))
        polish.add(
            self._switch(
                "processing",
                "remove_filler_words",
                _("Remove filler words"),
                _("um, uh, hmm… before punctuation"),
            )
        )
        fillers = Adw.EntryRow(title=_("Filler words (comma-separated)"))
        fillers.connect("changed", lambda *_: self._touch())
        self._rows[("processing", "filler_words")] = _ListProxy(fillers)

        actions_g = Adw.PreferencesGroup(
            title=_("Spoken formatting triggers"),
            description=_("Extra phrases that trigger formatting actions "
                          "(on top of the built-ins; still needs the prefix)"),
        )
        trig_rows = {}
        for action, title in (
            ("new_line", _("New line")),
            ("new_paragraph", _("New paragraph")),
            ("tab", _("Tab")),
            ("space", _("Space")),
        ):
            row = Adw.EntryRow(
                title=_("{action} — extra trigger phrases").format(action=title))
            row.connect("changed", lambda *_: self._touch())
            trig_rows[action] = row
            actions_g.add(row)
        actions_g.add(
            Adw.ActionRow(
                title=_("Example"),
                subtitle=_("nova vrstica, naslednja vrstica — then say "
                           "“literal nova vrstica” to start a new line"),
            )
        )
        polish.add(actions_g)
        self._rows[("processing", "formatting_action_triggers")] = _ActionTriggersProxy(
            trig_rows
        )
        polish.add(fillers)
        polish.add(
            self._switch(
                "processing",
                "punctuation_enabled",
                _("Spoken punctuation"),
                _('"literal comma" → ,'),
            )
        )
        polish.add(
            self._entry("processing", "punctuation_prefix",
                       _("Spoken-command prefix"))
        )
        gaav = Adw.ExpanderRow(
            title=_("Search-field formatting (GAAV)"),
            subtitle=_("lowercase first letter, drop the final period"),
        )
        gaav.add_row(self._plain_switch_row("processing", "gaav_enabled",
                                            _("Enabled")))
        gaav.add_row(
            self._plain_switch_row(
                "processing", "gaav_lowercase_first",
                _("Lowercase first letter")
            )
        )
        gaav.add_row(
            self._plain_switch_row(
                "processing", "gaav_remove_trailing_period",
                _("Remove trailing period")
            )
        )
        polish.add(gaav)

        send = Adw.ExpanderRow(
            title=_("Spoken send"),
            subtitle=_("a trailing phrase strips and presses Enter")
        )
        send.add_row(
            self._plain_switch_row("recording", "spoken_send_enabled",
                                   _("Enabled"))
        )
        phrase = self._entry("recording", "spoken_send_phrase", _("Phrase"))
        send.add_row(phrase)
        send.add_row(
            self._combo(
                "recording",
                "spoken_send_key",
                _("Key"),
            )
        )
        send.add_row(
            self._spin(
                "recording",
                "spoken_send_countdown_s",
                _("Quiet countdown (s)"),
                0.1,
                digits=1,
                subtitle=_("phrase + 0.5 s quiet finishes "
                           "the take by itself (speak to "
                           "cancel); 0 = off"),
            )
        )
        polish.add(send)
        page.add(polish)

    def _section_dictionary(self, page: Adw.PreferencesPage) -> None:
        """Custom dictionary + auto-learned suggestions."""

        self.dict_group = Adw.PreferencesGroup(
            title=_("Custom dictionary"),
            description=_('Phrases replaced on insert — '
                          '"miro board" → "Miro board"'),
        )
        add_w = Adw.ActionRow(title=_("Add word"))
        add_w_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_w_btn.connect("clicked", lambda *_: self._add_dict_word({}))
        add_w.add_suffix(add_w_btn)
        self.dict_group.add(add_w)
        page.add(self.dict_group)

        # auto-learned suggestions (dict_learn), below the hand-curated
        # editor; hidden entirely while nothing is pending
        self.suggest_group = Adw.PreferencesGroup(
            title=_("Suggested words"),
            description=_("Corrections noticed in your history edits — "
                          "accept to teach the dictionary"),
        )
        self.suggest_group.set_visible(False)  # until the first load
        self._suggest_rows: list[dict] = []
        page.add(self.suggest_group)

    def _section_insertion(self, page: Adw.PreferencesPage) -> None:
        """Insertion."""

        ins = Adw.PreferencesGroup(
            title=_("Insertion"),
            description=_("How typed text reaches your apps")
        )
        ins.add(
            self._combo(
                "insertion",
                "mode",
                _("Mode"),
            )
        )
        ins.add(self._spin("insertion", "type_delay_ms",
                           _("Typing delay (ms)"), 1))
        ins.add(
            self._spin(
                "insertion",
                "paste_threshold_chars",
                _("Paste threshold (chars)"),
                50,
            )
        )
        page.add(ins)

    def _section_commands(self, page: Adw.PreferencesPage) -> None:
        """Command mode."""

        # Command mode (moved from the AI page, macOS parity: Commands
        # belong with Dictation)
        cmd = Adw.PreferencesGroup(
            title=_("Commands"),
            description=_("Voice → terminal agent. Every command needs "
                          "confirmation."),
        )
        cmd.add(self._spin("command", "max_turns", _("Max agent turns"), 1))
        cmd.add(
            self._entry("command", "working_dir",
                        _("Working directory (empty = home)"))
        )
        cmd.add(
            self._spin(
                "command",
                "timeout_seconds",
                _("Command timeout (s)"),
                5,
                digits=1,
            )
        )
        cmd.add(
            self._spin(
                "command",
                "confirm_timeout_s",
                _("Confirmation timeout (s)"),
                5,
                digits=1,
            )
        )
        page.add(cmd)
