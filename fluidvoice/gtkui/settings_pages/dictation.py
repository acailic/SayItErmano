"""Dictation page: hotkeys + capture, mic picker + priority, custom dictionary, formatting triggers.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from .common import _ActionTriggersProxy, _ExtraShortcutsProxy, _ListProxy


class DictationPageMixin:
    def _build_dictation(self) -> None:
        page = Adw.PreferencesPage(
            name="dictation",
            icon_name="fluidvoice-dictation-symbolic",
            title="Dictation",
        )

        hk = Adw.PreferencesGroup(title="Hotkeys")
        hk.add(
            self._entry(
                "hotkey", "key", "Dictation key — e.g. Right_Control, F9", capture=True
            )
        )
        hk.add(
            self._combo(
                "hotkey",
                "mode",
                "Mode",
                [("toggle", "toggle"), ("hold", "hold"), ("both", "both")],
                subtitle="tap to start/stop · hold = push-to-talk · "
                "both: quick tap toggles, holding talks · "
                "modifier-only keys need toggle",
            )
        )
        hk.add(
            self._entry(
                "hotkey", "cancel_key", "Cancel key — discards a take", capture=True
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "rewrite_key",
                "Rewrite key (optional, needs AI)",
                capture=True,
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "command_key",
                "Command key (optional, needs AI)",
                capture=True,
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "paste_key",
                "Paste-last key (optional) — re-types last text",
                capture=True,
            )
        )
        hk.add(
            self._entry(
                "hotkey",
                "language_key",
                "Cycle-language key (optional) — steps language_cycle",
                capture=True,
            )
        )

        # B1: up to two extra dictation shortcuts, each optionally bound
        # to a named prompt profile (the primary hotkey stays above)
        extra_pairs = []
        from ...ai.profiles import load_profiles

        profile_names = ["(none)"] + sorted(load_profiles())
        for i in (2, 3):
            key_row = Adw.EntryRow(title=f"Extra dictation shortcut {i} (optional)")
            key_row.connect("changed", lambda *_: self._touch())
            btn = Gtk.Button(
                icon_name="media-record-symbolic",
                css_classes=["flat"],
                tooltip_text="Press a key…",
            )
            btn.connect("clicked", self._start_capture, key_row)
            key_row.add_suffix(btn)
            hk.add(key_row)
            combo = Adw.ComboRow(
                title=f"Shortcut {i} prompt profile",
                subtitle="polishes takes started by this shortcut",
            )
            combo.set_model(Gtk.StringList(strings=profile_names))
            combo.connect("notify::selected", lambda *_: self._touch())
            hk.add(combo)
            extra_pairs.append((key_row, combo))
        self._rows[("hotkey", "extra_shortcuts")] = _ExtraShortcutsProxy(extra_pairs)
        mods = Adw.ActionRow(
            title="Extra modifiers", subtitle="held in addition to the dictation key"
        )
        for mod in ("ctrl", "alt", "shift", "super"):
            tb = Gtk.ToggleButton(label=mod, css_classes=["flat"])
            tb.set_valign(Gtk.Align.CENTER)
            tb.connect("toggled", lambda *_: self._touch())
            self._mod_toggles[mod] = tb
            mods.add_suffix(tb)
        hk.add(mods)
        page.add(hk)

        mic = Adw.PreferencesGroup(title="Microphone and recording")
        self.mic_row = Adw.ComboRow(
            title="Microphone", subtitle="Auto follows the system default"
        )
        refresh_btn = Gtk.Button(
            icon_name="view-refresh-symbolic",
            css_classes=["flat"],
            tooltip_text="Refresh list",
        )
        refresh_btn.connect("clicked", lambda *_: self._fill_mics())
        self.mic_row.add_suffix(refresh_btn)
        self.mic_row.connect("notify::selected", lambda *_: self._touch())
        mic.add(self.mic_row)
        mic.add(
            self._combo(
                "recording",
                "command",
                "Recorder",
                [
                    ("auto", "auto"),
                    ("pw-record", "pw-record"),
                    ("parecord", "parecord"),
                ],
            )
        )
        mic.add(self._spin("recording", "max_seconds", "Max duration (s)", 5, 3600, 5))
        mic.add(
            self._spin(
                "recording",
                "first_pcm_timeout",
                "No-audio timeout (s)",
                0,
                60,
                0.5,
                digits=1,
                subtitle="0 = off; stops a muted/wrong mic fast",
            )
        )
        mic.add(
            self._switch(
                "recording",
                "skip_silent",
                "Skip silent takes",
                "Discard obviously-silent recordings ≤ 4 s",
            )
        )
        mic.add(
            self._switch(
                "recording",
                "pause_media",
                "Pause media",
                "Pause MPRIS players while dictating",
            )
        )
        page.add(mic)

        self.mic_prio_group = Adw.PreferencesGroup(
            title="Microphone priority",
            description="Ordered name patterns (e.g. bluez for a Bluetooth "
            "headset). When the chosen microphone disappears, "
            "the first available match is used.",
        )
        self._mic_prio_add_row = Adw.ActionRow(title="Add pattern")
        add_p_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_p_btn.connect("clicked", lambda *_: self._add_mic_prio(""))
        self._mic_prio_add_row.add_suffix(add_p_btn)
        self.mic_prio_group.add(self._mic_prio_add_row)
        page.add(self.mic_prio_group)

        preview = Adw.PreferencesGroup(
            title="Live preview",
            description="The pill overlay with partial text while recording",
        )
        preview.add(self._switch("recording", "preview_enabled", "Enabled"))
        preview.add(
            self._combo(
                "recording",
                "preview_mode",
                "Mode",
                [
                    ("auto", "auto"),
                    ("overlay", "overlay"),
                    ("notify", "notifications only"),
                ],
            )
        )
        preview.add(
            self._combo(
                "recording",
                "preview_overlay_size",
                "Size",
                [
                    ("pill", "pill"),
                    ("small", "small"),
                    ("medium", "medium"),
                    ("large", "large"),
                ],
                subtitle="macOS size presets",
            )
        )
        preview.add(
            self._spin(
                "recording", "preview_bottom_offset", "Bottom offset (px)", 0, 400, 2
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_interval",
                "Update interval (s)",
                0.3,
                10.0,
                0.1,
                digits=1,
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_min_audio",
                "First partial after (s)",
                0.3,
                10.0,
                0.1,
                digits=1,
            )
        )
        preview.add(
            self._switch(
                "recording",
                "preview_segmented",
                "Streaming (segmented)",
                "constant-cost windows instead of whole-take re-decode; all backends",
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_segment_s",
                "Segment window (s)",
                1.0,
                6.0,
                0.5,
                digits=1,
            )
        )
        preview.add(
            self._switch(
                "recording",
                "overlay_chips",
                "Hover action chips",
                "hover the pill while recording for copy/paste/cancel buttons (X11)",
            )
        )
        preview.add(
            self._spin(
                "recording",
                "preview_vad_silence_s",
                "Stop after silence (s)",
                0.0,
                10.0,
                0.5,
                digits=1,
                subtitle="0 keeps the old behavior (hotkey stops every take)",
            )
        )
        page.add(preview)

        polish = Adw.PreferencesGroup(title="Text polish")
        polish.add(
            self._switch(
                "processing",
                "remove_filler_words",
                "Remove filler words",
                "um, uh, hmm… before punctuation",
            )
        )
        fillers = Adw.EntryRow(title="Filler words (comma-separated)")
        fillers.connect("changed", lambda *_: self._touch())
        self._rows[("processing", "filler_words")] = _ListProxy(fillers)

        actions_g = Adw.PreferencesGroup(
            title="Spoken formatting triggers",
            description="Extra phrases that trigger formatting actions "
            "(on top of the built-ins; still needs the prefix)",
        )
        trig_rows = {}
        for action, title in (
            ("new_line", "New line"),
            ("new_paragraph", "New paragraph"),
            ("tab", "Tab"),
            ("space", "Space"),
        ):
            row = Adw.EntryRow(title=f"{title} — extra trigger phrases")
            row.connect("changed", lambda *_: self._touch())
            trig_rows[action] = row
            actions_g.add(row)
        actions_g.add(
            Adw.ActionRow(
                title="Example",
                subtitle="nova vrstica, naslednja vrstica — then say "
                "“literal nova vrstica” to start a new line",
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
                "Spoken punctuation",
                '"literal comma" → ,',
            )
        )
        polish.add(
            self._entry("processing", "punctuation_prefix", "Spoken-command prefix")
        )
        gaav = Adw.ExpanderRow(
            title="Search-field formatting (GAAV)",
            subtitle="lowercase first letter, drop the final period",
        )
        gaav.add_row(self._plain_switch_row("processing", "gaav_enabled", "Enabled"))
        gaav.add_row(
            self._plain_switch_row(
                "processing", "gaav_lowercase_first", "Lowercase first letter"
            )
        )
        gaav.add_row(
            self._plain_switch_row(
                "processing", "gaav_remove_trailing_period", "Remove trailing period"
            )
        )
        polish.add(gaav)

        send = Adw.ExpanderRow(
            title="Spoken send", subtitle="a trailing phrase strips and presses Enter"
        )
        send.add_row(
            self._plain_switch_row("recording", "spoken_send_enabled", "Enabled")
        )
        phrase = self._entry("recording", "spoken_send_phrase", "Phrase")
        send.add_row(phrase)
        send.add_row(
            self._combo(
                "recording",
                "spoken_send_key",
                "Key",
                [
                    ("enter", "enter"),
                    ("shift+enter", "shift+enter"),
                    ("ctrl+enter", "ctrl+enter"),
                ],
            )
        )
        send.add_row(
            self._spin(
                "recording",
                "spoken_send_countdown_s",
                "Quiet countdown (s)",
                0.0,
                5.0,
                0.1,
                digits=1,
                subtitle="phrase + 0.5 s quiet finishes "
                "the take by itself (speak to "
                "cancel); 0 = off",
            )
        )
        polish.add(send)
        page.add(polish)

        self.dict_group = Adw.PreferencesGroup(
            title="Custom dictionary",
            description='Phrases replaced on insert — "miro board" → "Miro board"',
        )
        add_w = Adw.ActionRow(title="Add word")
        add_w_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_w_btn.connect("clicked", lambda *_: self._add_dict_word({}))
        add_w.add_suffix(add_w_btn)
        self.dict_group.add(add_w)
        page.add(self.dict_group)

        # auto-learned suggestions (dict_learn), below the hand-curated
        # editor; hidden entirely while nothing is pending
        self.suggest_group = Adw.PreferencesGroup(
            title="Suggested words",
            description="Corrections noticed in your history edits — "
            "accept to teach the dictionary",
        )
        self.suggest_group.set_visible(False)  # until the first load
        self._suggest_rows: list[dict] = []
        page.add(self.suggest_group)

        ins = Adw.PreferencesGroup(
            title="Insertion", description="How typed text reaches your apps"
        )
        ins.add(
            self._combo(
                "insertion",
                "mode",
                "Mode",
                [("auto", "auto"), ("typed", "typed"), ("paste", "paste")],
            )
        )
        ins.add(
            self._spin("insertion", "type_delay_ms", "Typing delay (ms)", 0, 1000, 1)
        )
        ins.add(
            self._spin(
                "insertion",
                "paste_threshold_chars",
                "Paste threshold (chars)",
                1,
                100000,
                50,
            )
        )
        page.add(ins)
        page.add(self._save_group())
        self._add_page(page)

    def _fill_mics(self) -> None:
        values: list[tuple[str, str]] = [("Auto (system default)", "")]
        for m in self.c.mics():
            label = m["description"] + ("  · default" if m.get("default") else "")
            values.append((label, m["name"]))
        current = str(self.cfg.get("recording", {}).get("device", "") or "")
        value_list = [v for _l, v in values]
        if current and current not in value_list:
            values.append((current + "  · saved", current))
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
        row = Adw.EntryRow(title="Pattern")
        row.set_text(value)
        row.connect("changed", lambda *_: self._touch())
        up = Gtk.Button(
            icon_name="go-up-symbolic", css_classes=["flat"], tooltip_text="Move up"
        )
        down = Gtk.Button(
            icon_name="go-down-symbolic", css_classes=["flat"], tooltip_text="Move down"
        )
        rm = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            tooltip_text="Remove this pattern",
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
        exp = Adw.ExpanderRow(title=", ".join(entry.get("triggers", [])) or "New word")
        trig = Adw.EntryRow(title="Triggers (comma-separated)")
        trig.set_text(", ".join(entry.get("triggers", [])))
        repl = Adw.EntryRow(title="Replacement")
        repl.set_text(str(entry.get("replacement", "")))
        rm = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat", "destructive-action"],
            tooltip_text="Remove this word",
        )
        exp.add_suffix(rm)
        exp.add_row(trig)
        exp.add_row(repl)
        ref = {"exp": exp, "trig": trig, "repl": repl}
        rm.connect("clicked", self._remove_dict_word, ref)

        def sync_title(*_):
            exp.set_title(trig.get_text().strip() or "New word")
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
                    "Incomplete dictionary word skipped (needs "
                    "triggers and a replacement)"
                )
        return entries
