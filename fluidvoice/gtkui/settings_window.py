"""Settings window — Adw.PreferencesWindow with one page per section
(the macOS app's Settings counterpart).

Covers every key the daemon's set-config validates. Saving goes through
the socket (live cfg + hot-apply); with the daemon down it degrades to
file-only mode (the save row and the load toast say so). Unsaved changes
are tracked: the Save row / Ctrl+S apply, closing with pending changes
asks first.
"""
from __future__ import annotations

import threading

from gi.repository import Adw, Gdk, GLib, Gtk

from .. import __version__ as APP_VERSION
from .. import backends, model_catalog, model_download
from ..config import DEFAULTS, KNOWN_LANGUAGES
from ..config import KNOWN_LANGUAGES as LANGUAGES
from .client import Client, ClientError

# GDK keyval name -> friendly keysym the config expects (where they differ)
_KEY_REMAP = {"Control_L": "Left_Control", "Control_R": "Right_Control",
              "Alt_L": "Left_Alt", "Alt_R": "Right_Alt",
              "Shift_L": "Left_Shift", "Shift_R": "Right_Shift",
              "Super_L": "Left_Super", "Super_R": "Right_Super"}

# Whisper language codes come from config.KNOWN_LANGUAGES (single source
# of truth shared with the cycle/whitelist validation; validation accepts
# any code grammar for general.language - a saved code not in this list is
# appended as an extra option on load)


class _SwitchProxy:
    """Adapter so plain Gtk.Switch rows (inside expanders) load/collect like
    Adw.SwitchRow through the same field registry."""

    def __init__(self, switch: Gtk.Switch, title: str, subtitle: str = ""):
        self.switch = switch
        self.title = title
        self.subtitle = subtitle

    def set_active(self, v: bool) -> None:
        self.switch.set_active(v)

    def get_active(self) -> bool:
        return self.switch.get_active()


class _ListProxy:
    """EntryRow-backed list<string> (comma-separated) for the field registry."""

    def __init__(self, row: Adw.EntryRow):
        self.row = row

    def set_value(self, values) -> None:
        self.row.set_text(", ".join(values or []))

    def get_value(self) -> list:
        return [v.strip() for v in self.row.get_text().split(",")
                if v.strip()]


class _ExtraShortcutsProxy:
    """Two (key EntryRow, profile ComboRow) pairs backing the
    hotkey.extra_shortcuts list (B1). Index 0 of the combo is '(none)'."""

    def __init__(self, pairs):
        self.pairs = pairs
        model = pairs[0][1].get_model()
        self.names = [model.get_string(i) for i in range(model.get_n_items())]

    def set_value(self, value) -> None:
        value = list(value or [])
        for i, (key_row, combo) in enumerate(self.pairs):
            spec = value[i] if i < len(value) else {}
            key_row.set_text(spec.get("key", ""))
            name = spec.get("profile", "")
            idx = self.names.index(name) if name in self.names else 0
            combo.set_selected(idx)

    def get_value(self) -> list:
        out = []
        for key_row, combo in self.pairs:
            key = key_row.get_text().strip()
            if not key:
                continue
            idx = combo.get_selected()
            name = self.names[idx] if 0 <= idx < len(self.names) else ""
            entry = {"key": key}
            if name and name != "(none)":
                entry["profile"] = name
            out.append(entry)
        return out[:2]


class _ActionTriggersProxy:
    """Four EntryRows (one per action) backing the dict-valued
    processing.formatting_action_triggers; aliases are comma-separated."""

    _ACTIONS = ("new_line", "new_paragraph", "tab", "space")

    def __init__(self, rows: dict):
        self.rows = rows  # action -> Adw.EntryRow

    def set_value(self, value) -> None:
        value = value or {}
        for action in self._ACTIONS:
            aliases = value.get(action) or []
            self.rows[action].set_text(", ".join(aliases))

    def get_value(self) -> dict:
        out = {}
        for action in self._ACTIONS:
            aliases = [v.strip() for v in self.rows[action].get_text()
                       .split(",") if v.strip()]
            if aliases:
                out[action] = aliases
        return out


class _TextProxy:
    """TextBuffer-backed multi-line string for the field registry (the
    base-prompt editor). Empty is a MEANINGFUL value ("use the built-in
    prompt"), unlike EntryRow's keep-the-saved-value rule."""

    def __init__(self, buffer: Gtk.TextBuffer):
        self.buffer = buffer

    def set_value(self, text) -> None:
        self.buffer.set_text(str(text or ""))

    def get_value(self) -> str:
        start, end = self.buffer.get_start_iter(), self.buffer.get_end_iter()
        return self.buffer.get_text(start, end, False)


class _PasswordProxy:
    """Gtk.Entry (visibility=False) inside an Adw.ActionRow - the field
    registry adapter for model.remote_api_key. Never RENDERS a stored
    value (set_value only ever clears + annotates the row: the value may
    be a masked bool from the daemon or a real secret in file-only mode);
    _collect skips it while empty so the saved key carries over."""

    def __init__(self, entry: Gtk.Entry, row: Adw.ActionRow):
        self.entry = entry
        self.row = row

    def set_value(self, value) -> None:
        # value True (masked, daemon up) or a non-empty string (file-only):
        # show the "saved" hint, keep the field empty either way
        if value:
            self.entry.set_text("")
            self.row.set_subtitle("key saved — type to replace")
        else:
            self.entry.set_text("")
            self.row.set_subtitle("optional bearer token (never shown)")

    def get_text(self) -> str:
        return self.entry.get_text()


class _InstructionRow(Adw.PreferencesRow):
    """A preferences-compatible row that hosts the instructions TextView."""

    def __init__(self, text_view: Gtk.TextView, title: str = "Instructions"):
        super().__init__(title=title)
        sw = Gtk.ScrolledWindow(child=text_view, hexpand=True,
                                height_request=90,
                                propagate_natural_height=True)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      margin_start=14, margin_end=14, margin_bottom=8)
        box.append(sw)
        self.set_child(box)


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, application=None, client=None):
        super().__init__(application=application, title="Settings",
                         default_width=680, default_height=680)
        self.c = client or Client()
        self.cfg: dict = {}
        self._from_daemon = False
        self._loading = False
        self._dirty = False
        self._rows: dict[tuple[str, str], object] = {}
        self._combo_values: dict[tuple[str, str], list] = {}
        self._rule_rows: list[dict] = []  # per-app prompt editors
        self._dict_rows: list[dict] = []  # custom-dictionary editors
        self._suggest_rows: list[dict] = []  # learned-suggestion rows
        self._mic_prio_rows: list[dict] = []  # mic-priority pattern editors
        self._cycle_rows: list[dict] = []  # language-cycle code editors
        self._save_groups: list[Adw.PreferencesGroup] = []
        self._save_rows: list[Adw.ActionRow] = []
        self._model_rows: list[Adw.ActionRow] = []
        self._gguf_rows: list[Adw.ActionRow] = []
        self._gguf_dl: dict[str, dict] = {}  # name -> {bytes, total, done, error}
        self._parakeet_rows: list[Adw.ActionRow] = []
        self._parakeet_dl: dict[str, dict] = {}
        self._mod_toggles: dict[str, Gtk.ToggleButton] = {}
        self._profiles: dict[str, str] = {}  # prompt profiles (sidecar file)
        self._model_lang_rows: dict[str, Adw.ComboRow] = {}  # name -> row
        self._model_lang_values_map: dict[str, list] = {}  # name -> values
        self._suppress_touch = False  # programmatic combo rebuilds

        self._build_general()
        self._build_models()
        self._build_ai()
        self._build_dictation()
        self._build_wayland()
        self._build_history_page()
        self._build_about()

        self.install_action("settings.save", None,
                            lambda w, _n, _p: w.save())
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_: self._update_provider_logo())
        ctrl = Gtk.ShortcutController()
        ctrl.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<primary>s"),
            action=Gtk.NamedAction.new("settings.save")))
        self.add_controller(ctrl)
        self.connect("close-request", self._on_close_request)

        self._load()

    # -- save / discard bar (bottom of every content page) ----------------------

    def _save_group(self) -> Adw.PreferencesGroup:
        grp = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=self._save_title("Changes are saved"),
                            subtitle="")
        self._save_rows.append(row)
        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.connect("clicked", lambda *_: self.save())
        discard_btn = Gtk.Button(label="Discard", css_classes=["flat"])
        discard_btn.set_valign(Gtk.Align.CENTER)
        discard_btn.connect("clicked", lambda *_: self._load())
        row.add_suffix(discard_btn)
        row.add_suffix(save_btn)
        grp.add(row)
        self._save_groups.append(grp)
        return grp

    def _save_title(self, base: str) -> str:
        suffix = "" if self._from_daemon else " — daemon offline, saving to file"
        return base + (" · UNSAVED" if self._dirty else "") + suffix

    def _sync_save_rows(self) -> None:
        for row in self._save_rows:
            row.set_title(self._save_title("Changes are saved"))

    # -- field registry -----------------------------------------------------------

    def _touch(self) -> None:
        if not self._loading and not self._dirty:
            self._dirty = True
            self._sync_save_rows()

    def _switch(self, section, key, title, subtitle="") -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.connect("notify::active", lambda *_: self._touch())
        self._rows[(section, key)] = row
        return row

    def _entry(self, section, key, title, capture=False) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title)
        row.connect("changed", lambda *_: self._touch())
        self._rows[(section, key)] = row
        if capture:
            btn = Gtk.Button(icon_name="media-record-symbolic",
                             css_classes=["flat"], tooltip_text="Press a key…")
            btn.connect("clicked", self._start_capture, row)
            row.add_suffix(btn)
        return row

    def _combo(self, section, key, title, values,
               subtitle="") -> Adw.ComboRow:
        """values: list of (label, config_value)."""
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        model = Gtk.StringList()
        for label, _v in values:
            model.append(label)
        row.set_model(model)
        row.connect("notify::selected", lambda *_: self._touch())
        self._rows[(section, key)] = row
        self._combo_values[(section, key)] = [v for _l, v in values]
        return row

    def _refill_combo(self, section: str, key: str, values) -> None:
        """Rebuild a combo's options (e.g. to append a saved unknown value)."""
        model = Gtk.StringList()
        for label, _v in values:
            model.append(label)
        self._rows[(section, key)].set_model(model)
        self._combo_values[(section, key)] = [v for _l, v in values]

    def _spin(self, section, key, title, lo, hi, step, digits=0,
              subtitle="") -> Adw.SpinRow:
        adj = Gtk.Adjustment(value=lo, lower=lo, upper=hi, step_increment=step)
        row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adj,
                          digits=digits)
        row.connect("notify::value", lambda *_: self._touch())
        self._rows[(section, key)] = row
        return row

    def _plain_switch_row(self, section, key, title) -> Adw.ActionRow:
        """Gtk.Switch inside an ActionRow (for use inside ExpanderRows)."""
        row = Adw.ActionRow(title=title)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        switch.connect("notify::active", lambda *_: self._touch())
        self._rows[(section, key)] = _SwitchProxy(switch, title)
        row.add_suffix(switch)
        return row

    # -- load / collect / save ------------------------------------------------------

    def _load(self) -> None:
        self._loading = True
        self.cfg, self._from_daemon = self.c.get_config()
        if not self._from_daemon and not self._loading_first_done:
            self._loading_first_done = True
            GLib.idle_add(self.toast,
                          "Daemon offline — file-only mode; changes apply on "
                          "next daemon start", 6)
        self._fill_mics()  # NOTE: resets _loading to False at its end
        self._loading = True  # keep the guard up through the final refresh
        lang = str(self.cfg.get("general", {}).get("language", "auto"))
        if lang != "auto" and lang not in LANGUAGES:
            # a saved code outside the common list stays selectable
            self._refill_combo("general", "language",
                               [("auto (detect)", "auto")]
                               + [(c, c) for c in LANGUAGES]
                               + [(f"{lang} (saved)", lang)])
        for (sec, key), row in self._rows.items():
            val = self.cfg.get(sec, {}).get(key, _default(sec, key))
            if isinstance(row, _SwitchProxy):
                row.set_active(bool(val))
            elif isinstance(row, Adw.SwitchRow):
                row.set_active(bool(val))
            elif isinstance(row, _ListProxy):
                row.set_value(val)
            elif isinstance(row, _TextProxy):
                row.set_value(val)
            elif isinstance(row, _PasswordProxy):
                row.set_value(val)  # never renders a stored/masked key
            elif isinstance(row, Adw.EntryRow):
                row.set_text("" if val is None else str(val))
            elif isinstance(row, Adw.ComboRow):
                values = self._combo_values[(sec, key)]
                idx = values.index(val) if val in values else 0
                row.set_selected(idx)
            elif isinstance(row, Adw.SpinRow):
                row.set_value(float(val))
        # idle unload: config seconds -> UI whole minutes (0 = off); a
        # hand-edited sub-minute value (30-89 s) rounds up to 1 minute
        idle_s = int(self.cfg.get("model", {}).get("idle_unload_s", 0) or 0)
        self._idle_unload_row.set_value(
            0 if idle_s <= 0 else max(1, round(idle_s / 60)))
        for mod, tb in self._mod_toggles.items():
            tb.set_active(mod in (self.cfg.get("hotkey", {})
                                  .get("modifiers") or []))
        self._load_rules(self.cfg.get("ai", {}).get("per_app_prompts") or [])
        self._load_dictionary(self.cfg.get("processing", {}).get("dictionary")
                              or [])
        self._load_suggestions()
        self._load_mic_priority(
            list(self.cfg.get("recording", {}).get("mic_priority") or []))
        self._load_language_cycle(
            list(self.cfg.get("general", {}).get("language_cycle") or []))
        self._load_profiles()
        self._dirty = False
        self._sync_save_rows()
        self._refresh_models()
        self._refresh_wayland_rows()
        # re-sync language-row selections from cfg (Discard semantics);
        # still under the _loading guard so no _touch fires
        self._refresh_model_language_rows(reset=True)
        self._loading = False
        self._update_provider_logo()

    _loading_first_done = False

    def _collect(self) -> dict:
        body: dict = {}
        for (sec, key), row in self._rows.items():
            if isinstance(row, _SwitchProxy):
                val = row.get_active()
            elif isinstance(row, Adw.SwitchRow):
                val = row.get_active()
            elif isinstance(row, _ListProxy):
                val = row.get_value()
            elif isinstance(row, _TextProxy):
                # base_prompt POSTs even when empty: "" is meaningful
                # (reset to the built-in prompt), unlike EntryRow
                val = row.get_value().strip()
                if not val and (sec, key) != ("ai", "base_prompt"):
                    continue
            elif isinstance(row, _PasswordProxy):
                val = row.get_text().strip()
                if not val:
                    continue  # empty keeps the saved key (file carry-over)
            elif isinstance(row, Adw.EntryRow):
                val = row.get_text().strip()
                if not val and (sec, key) != ("model", "remote_url"):
                    continue  # empty optional strings keep the saved value;
                    # remote_url is the exception: "" = remote OFF
            elif isinstance(row, Adw.ComboRow):
                val = self._combo_values[(sec, key)][row.get_selected()]
            elif isinstance(row, Adw.SpinRow):
                val = (int(row.get_value()) if row.get_digits() == 0
                       else round(row.get_value(), 4))
            else:
                continue
            body.setdefault(sec, {})[key] = val
        body.setdefault("hotkey", {})["modifiers"] = [
            m for m, tb in self._mod_toggles.items() if tb.get_active()]
        rules = self._collect_rules()
        if rules is not None:
            body.setdefault("ai", {})["per_app_prompts"] = rules
        body.setdefault("processing", {})['dictionary'] = \
            self._collect_dictionary()
        body.setdefault("recording", {})["mic_priority"] = \
            self._collect_mic_priority()  # empty list is meaningful: removals
        body.setdefault("general", {})["language_cycle"] = \
            self._collect_language_cycle()  # empty list is meaningful here too
        body.setdefault("model", {})["languages"] = \
            self._collect_model_languages()  # empty dict is meaningful too
        # idle unload: UI whole minutes -> config seconds (0 = off)
        body.setdefault("model", {})["idle_unload_s"] = \
            int(self._idle_unload_row.get_value()) * 60
        return body

    def save(self) -> bool:
        body = self._collect()
        resp = self.c.set_config(body)
        changed = resp.get("changed") or []
        rejected = resp.get("rejected") or []
        errors = resp.get("errors") or []
        restart = resp.get("restart_required") or []
        if rejected:
            self.toast(f"Rejected (bad values): {', '.join(rejected)}")
        elif errors:
            self.toast(f"Not fully applied: {'; '.join(errors)}")
        elif changed:
            msg = f"Saved {len(changed)} change{'' if len(changed) == 1 else 's'}"
            if restart:
                msg += f" — restart daemon for: {', '.join(restart)}"
            self.toast(msg)
        else:
            self.toast("No changes")
        self._load()  # resync from the daemon/file
        return True

    def toast(self, text: str, timeout: int = 5) -> None:
        super().add_toast(Adw.Toast(title=text, timeout=timeout))

    def _on_close_request(self, *_args) -> bool:
        if not self._dirty:
            return False  # allow close
        dlg = Adw.MessageDialog(transient_for=self, modal=True,
                                heading="Discard unsaved changes?",
                                body="Changes that were not saved will be lost.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("discard", "Discard")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)

        def responded(_dlg, response):
            if response == "discard":
                self._dirty = False
                self.close()
        dlg.connect("response", responded)
        dlg.present()
        return True  # block close until answered

    # -- key capture ------------------------------------------------------------------

    def _start_capture(self, btn, row: Adw.EntryRow) -> None:
        ctrl = Gtk.EventControllerKey()
        self.add_controller(ctrl)
        btn.add_css_class("recording")
        self.toast("Press the key to use (Esc cancels)", 4)

        def key_pressed(_ctrl, keyval, _keycode, _state):
            name = _keyname(keyval)
            GLib.idle_add(self.remove_controller, ctrl)
            btn.remove_css_class("recording")
            if name != "Escape":
                row.set_text(name)
            return True

        ctrl.connect("key-pressed", key_pressed)

    # -- pages -------------------------------------------------------------------------

    def _build_general(self) -> None:
        page = Adw.PreferencesPage(name="general", icon_name="fluidvoice-general-symbolic",
                                   title="General")
        grp = Adw.PreferencesGroup(title="General")
        lang = self._combo("general", "language", "Language",
                           [("auto (detect)", "auto")]
                           + [(c, c) for c in LANGUAGES],
                           subtitle="Whisper code the recognizer locks to")
        self.lang_row = lang
        grp.add(lang)
        grp.add(self._switch("general", "copy_to_clipboard",
                             "Copy transcriptions to clipboard",
                             "Every dictation also lands in the clipboard"))
        grp.add(self._switch("general", "tray_enabled", "Tray icon",
                             "Panel icon while the daemon runs "
                             "(toggle applies live)"))
        grp.add(self._switch("notifications", "enabled", "Notifications"))
        grp.add(self._switch("sounds", "enabled", "Sounds"))
        grp.add(self._spin("sounds", "volume", "Volume", 0.0, 1.0, 0.05,
                           digits=2))
        page.add(grp)

        # Language cycle (mic-priority editor pattern): the ordered list
        # hotkey.language_key steps through. Runtime daemon state - the
        # list is saved, the cycle POSITION never is.
        self.cycle_group = Adw.PreferencesGroup(
            title="Language cycle",
            description="Languages the cycle key steps through — e.g. "
                        "auto, en, sl (runtime state; never persisted; "
                        "empty = cycle key off)")
        self._cycle_add_row = Adw.ActionRow(title="Add language")
        add_lang_btn = Gtk.Button(icon_name="list-add-symbolic",
                                  css_classes=["flat"])
        add_lang_btn.connect("clicked", lambda *_: self._add_cycle_lang(""))
        self._cycle_add_row.add_suffix(add_lang_btn)
        self.cycle_group.add(self._cycle_add_row)
        page.add(self.cycle_group)

        wl = Adw.EntryRow(title="Language whitelist — e.g. sl, en")
        wl.connect("changed", lambda *_: self._touch())
        self._rows[("general", "language_whitelist")] = _ListProxy(wl)
        wgrp = Adw.PreferencesGroup(
            title="Wrong-language guard",
            description="Comma-separated codes; when auto-detection lands "
                        "outside them the take is re-decoded once with "
                        "the first entry. Whisper multilingual backends "
                        "only; parakeet (English-only) and whisper.cpp "
                        "auto are unaffected")
        wgrp.add(wl)
        page.add(wgrp)

        page.add(self._save_group())
        self.add(page)

    def _build_models(self) -> None:
        page = Adw.PreferencesPage(name="models", icon_name="fluidvoice-models-symbolic",
                                   title="Models")
        self.models_group = Adw.PreferencesGroup(
            title="Speech models",
            description="faster-whisper models (downloaded on first use)")
        page.add(self.models_group)

        self.gguf_group = Adw.PreferencesGroup(
            title="whisper.cpp GGUF",
            description="Direct ggml models for the whisper.cpp backend")
        page.add(self.gguf_group)

        self.parakeet_group = Adw.PreferencesGroup(
            title="Parakeet (ONNX)",
            description="NVIDIA Parakeet TDT via ONNX Runtime — explicit backend")
        page.add(self.parakeet_group)

        warm = Adw.PreferencesGroup(title="State")
        self.warmup_row = Adw.ActionRow(title="Model", subtitle="—")
        self.warmup_spinner = Gtk.Spinner()
        self.warmup_row.add_suffix(self.warmup_spinner)
        warm.add(self.warmup_row)
        page.add(warm)

        # Memory: idle model unload (model.idle_unload_s). Whole minutes in
        # the UI (0 = off); the config stores seconds - see _load/_collect.
        mem = Adw.PreferencesGroup(
            title="Memory",
            description="Free RAM/VRAM between dictations (applies live)")
        adj = Gtk.Adjustment(value=0, lower=0, upper=1440, step_increment=1)
        self._idle_unload_row = Adw.SpinRow(
            title="Unload model after idle (minutes)",
            subtitle="0 = keep the model loaded (fastest first word); "
                     "higher frees RAM/VRAM after inactivity — the next "
                     "dictation pays the model load time",
            adjustment=adj, digits=0)
        self._idle_unload_row.connect("notify::value", lambda *_: self._touch())
        mem.add(self._idle_unload_row)
        page.add(mem)

        engine = Adw.PreferencesGroup(title="Engine options",
                                      description="Changing these reloads the model")
        engine.add(self._combo(
            "model", "backend", "Backend",
            [("auto", "auto"), ("faster-whisper", "faster-whisper"),
             ("whisper-torch", "whisper-torch"), ("whisper.cpp", "whisper.cpp"),
             ("parakeet", "parakeet"), ("remote", "remote")]))
        engine.add(self._combo(
            "model", "device", "Device",
            [("auto", "auto"), ("cuda", "cuda"), ("cpu", "cpu")]))
        engine.add(self._combo(
            "model", "compute", "Compute",
            [("auto", "auto"), ("float16", "float16"), ("int8", "int8")]))
        engine.add(self._entry("model", "whispercpp_model",
                               "whisper.cpp model — name like ggml-base.bin, or a path"))
        engine.add(self._switch("model", "eager_warmup", "Load at startup",
                                "Warm the model when the daemon starts "
                                "(needs a daemon restart)"))
        page.add(engine)

        # Remote OpenAI-compatible STT server: local-first — everything is
        # off while the URL is empty; audio goes only to the URL you set.
        remote = Adw.PreferencesGroup(
            title="Remote (OpenAI-compatible)",
            description="Dictation POSTs the recorded WAV to "
                        "<url>/v1/audio/transcriptions — e.g. "
                        "http://lan-box:8000 (vLLM / whisper.cpp server / "
                        "NIM / DGX Spark). Empty URL = local models only; "
                        "audio leaves this machine only toward this URL")
        remote.add(self._entry("model", "remote_url", "Server URL"))
        remote.add(self._entry("model", "remote_model", "Model name"))
        key_row = Adw.ActionRow(
            title="API key",
            subtitle="optional bearer token (never shown)")
        key_entry = Gtk.Entry(visibility=False, hexpand=True,
                              valign=Gtk.Align.CENTER,
                              input_purpose=Gtk.InputPurpose.PASSWORD)
        key_entry.connect("changed", lambda *_: self._touch())
        key_row.add_suffix(key_entry)
        remote.add(key_row)
        self._rows[("model", "remote_api_key")] = _PasswordProxy(key_entry,
                                                                 key_row)
        remote.add(self._spin("model", "remote_timeout_s", "Timeout (seconds)",
                              5, 600, 1, digits=0,
                              subtitle="per-request; retry once on transient "
                                       "network errors"))
        page.add(remote)

        self.lang_overrides_group = Adw.PreferencesGroup(
            title="Per-model language",
            description="Overrides general.language per model - "
                        "empty = inherit, auto = always detect")
        page.add(self.lang_overrides_group)

        self.disk_group = Adw.PreferencesGroup(
            title="Disk usage",
            description="Cached models under ~/.cache/sayit-ermano/models "
                        "(deletion needs the daemon)")
        self.disk_total_row = Adw.ActionRow(title="Total", subtitle="—")
        self.disk_group.add(self.disk_total_row)
        self._disk_rows: list[Adw.ActionRow] = []
        page.add(self.disk_group)
        page.add(self._save_group())
        self.add(page)

    def _refresh_models(self) -> None:
        for row in self._model_rows:
            self.models_group.remove(row)
        self._model_rows = []
        active = self._active_model()
        for name, info in model_catalog.MODEL_CATALOG.items():
            row = Adw.ActionRow(
                title=name,
                subtitle=(f"{info['size']} · {info['langs']} languages · "
                          f"{info['note']}"))
            if name == active:
                row.add_suffix(Gtk.Label(label="Active",
                                         css_classes=["success", "caption"]))
            else:
                downloaded = model_catalog.model_downloaded(name)
                btn = Gtk.Button(label="Use" if downloaded else "Download & use",
                                 css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._pick_model, name)
                row.add_suffix(btn)
            self.models_group.add(row)
            self._model_rows.append(row)
        self._refresh_gguf_rows()
        self._refresh_parakeet_rows()
        self._refresh_model_language_rows()
        self._refresh_disk_rows()
        st = self.c.status() or {}
        warm = st.get("warmup") or {}
        if warm.get("running"):
            self.warmup_spinner.start()
            self.warmup_row.set_subtitle(f"loading {warm.get('model') or ''}…")
        elif warm.get("error"):
            self.warmup_row.set_subtitle(f"error: {warm['error']}")
        else:
            self.warmup_spinner.stop()
            rurl = str((self.cfg.get("model", {}) or {})
                       .get("remote_url") or "").strip()
            if rurl:
                from urllib.parse import urlparse
                rmodel = str((self.cfg.get("model", {}) or {})
                             .get("remote_model") or "-")
                host = urlparse(rurl).netloc or rurl
                self.warmup_row.set_subtitle(
                    f"active: remote ({rmodel} @ {host})")
            else:
                self.warmup_row.set_subtitle(f"active: {active or '—'}")
        if not (st.get("model_state") or {}).get("loaded", True):
            self.warmup_row.set_subtitle(
                "unloaded (idle — reloads on the next dictation)")
        if st:  # about rows (daemon offline keeps the placeholder em-dash)
            self.about_backend_row.set_subtitle(st.get("backend") or "—")
            self.about_gpu_row.set_subtitle("yes" if st.get("cuda") else "no")
            self._apply_about_update(st)

    def _active_model(self) -> str:
        name = str(self.cfg.get("model", {}).get("name", "auto"))
        if name in ("", "auto"):
            return backends.resolve_model_name(name)
        return backends.ALIASES.get(name.lower(), name.lower())

    def _apply_about_update(self, st: dict) -> None:
        """About → Update row from the daemon status payload (no network
        here - the daemon's checker owns the probe; see update.py)."""
        u = st.get("update") or {}
        if st.get("update_available"):
            self.about_update_row.set_subtitle(
                f"v{st['update_available']} available — "
                "run 'sayit-ermano update'")
        elif not u.get("enabled"):
            self.about_update_row.set_subtitle("checks disabled")
        elif u.get("checked"):
            self.about_update_row.set_subtitle("up to date")
        else:
            self.about_update_row.set_subtitle("checking…")

    def _pick_model(self, btn, name: str) -> None:
        try:
            resp = self.c.select_model(name)
        except Exception as e:
            self.toast(str(e))
            return
        if resp.get("ok") is False:
            self.toast(str(resp.get("error") or "failed"))
            return
        self.toast(f"Switching to {name}…")
        GLib.timeout_add_seconds(1, self._poll_model)

    def _poll_model(self) -> bool:
        self.cfg, self._from_daemon = self.c.get_config()
        self._refresh_models()
        warm = (self.c.status() or {}).get("warmup") or {}
        if warm.get("running"):
            return True
        if warm.get("error"):
            self.toast(f"model error: {warm['error']}")
        return False

    # -- per-model language overrides (model.languages) -----------------------

    def _downloaded_model_names(self) -> list[str]:
        """Every downloaded model across the three catalogs. Parakeet v2
        (English-only) has no language to constrain and is skipped."""
        names = [n for n in model_catalog.MODEL_CATALOG
                 if model_catalog.model_downloaded(n)]
        names += [n for n in model_catalog.GGUF_CATALOG
                  if model_catalog.gguf_downloaded(n)]
        names += [n for n in model_catalog.PARAKEET_CATALOG
                  if n != "parakeet-tdt-0.6b-v2"
                  and model_catalog.parakeet_downloaded(n)]
        return names

    def _model_lang_values(self, saved: str) -> list[tuple[str, str]]:
        values = [("inherit (general)", ""), ("auto (detect)", "auto")] \
            + [(c, c) for c in LANGUAGES]
        if saved and saved != "auto" and saved not in LANGUAGES:
            values.append((f"{saved} (saved)", saved))  # unknown saved code
        return values

    def _set_model_lang_selection(self, name: str) -> None:
        saved = str((self.cfg.get("model", {}).get("languages") or {})
                    .get(name, ""))
        values = self._model_lang_values(saved)
        model = Gtk.StringList()
        for label, _v in values:
            model.append(label)
        row = self._model_lang_rows[name]
        self._suppress_touch = True  # programmatic rebuild: no dirty
        try:
            row.set_model(model)
            row.set_title(name)
            row.set_selected([v for _l, v in values].index(saved))
        finally:
            self._suppress_touch = False
        self._model_lang_values_map[name] = values

    def _refresh_model_language_rows(self, reset: bool = False) -> None:
        """Diff the downloaded set against the rows and add/remove only the
        diff (entered selections survive refreshes; reset re-syncs them
        from cfg, e.g. on Discard). Mic-priority discipline."""
        wanted = self._downloaded_model_names()
        for name in [n for n in list(self._model_lang_rows) if n not in wanted]:
            self.lang_overrides_group.remove(self._model_lang_rows.pop(name))
            self._model_lang_values_map.pop(name, None)
        for name in wanted:
            if name not in self._model_lang_rows:
                row = Adw.ComboRow(title=name)
                row.connect("notify::selected", self._lang_row_touch)
                self._model_lang_rows[name] = row
                self._model_lang_values_map[name] = []
                self._set_model_lang_selection(name)
                self.lang_overrides_group.add(row)
            elif reset:
                self._set_model_lang_selection(name)
        self.lang_overrides_group.set_visible(bool(self._model_lang_rows))

    def _lang_row_touch(self, *_args) -> None:
        if not self._suppress_touch:
            self._touch()

    def _collect_model_languages(self) -> dict[str, str]:
        """model.languages from the shown rows; entries for models WITHOUT
        rows (no longer downloaded) are carried over from cfg unchanged."""
        out: dict[str, str] = {}
        for name, row in self._model_lang_rows.items():
            values = self._model_lang_values_map.get(name) or []
            idx = row.get_selected()
            code = values[idx][1] if 0 <= idx < len(values) else ""
            if code:  # ""/inherit drops the key
                out[name] = code
        shown = set(self._model_lang_rows)
        for k, v in (self.cfg.get("model", {}).get("languages") or {}).items():
            if k not in shown and v:
                out[str(k)] = str(v)
        return out

    # -- disk usage / model pruning (socket-only deletion) -------------------

    def _refresh_disk_rows(self) -> None:
        """Per-cached-model size rows + total (direct read, dict_suggestions
        precedent); deletion itself goes through the daemon."""
        for row in self._disk_rows:
            self.disk_group.remove(row)
        self._disk_rows = []
        try:
            entries = model_catalog.cached_models()
        except Exception:
            entries = []  # a cache listing failure must not break the page
        total = sum(e["bytes"] for e in entries)
        self.disk_total_row.set_subtitle(
            f"{len(entries)} model{'' if len(entries) == 1 else 's'} · "
            f"{model_catalog.human_bytes(total)}" if entries else "empty")
        active = (self._active_model() or self._active_gguf()
                  or self._active_parakeet())
        for e in entries:
            row = Adw.ActionRow(
                title=e["name"],
                subtitle=f"{e['kind']} · "
                         f"{model_catalog.human_bytes(e['bytes'])} · {e['path']}")
            btn = Gtk.Button(label="Delete",
                             css_classes=["flat", "destructive-action"])
            btn.set_valign(Gtk.Align.CENTER)
            if e["name"] == active:
                btn.set_sensitive(False)
                btn.set_tooltip_text("This is the active model")
            else:
                btn.connect("clicked", self._confirm_delete_model, e)
            row.add_suffix(btn)
            self.disk_group.add(row)
            self._disk_rows.append(row)

    def _confirm_delete_model(self, _btn, entry: dict) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True,
            heading=f"Delete {entry['name']}?",
            body=f"Frees {model_catalog.human_bytes(entry['bytes'])} from "
                 "the models cache.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_delete_model_response, entry)
        dlg.present()

    def _on_delete_model_response(self, _dlg, response, entry: dict) -> None:
        if response != "delete":
            return
        kind, name = entry["kind"], entry["name"]

        def work():
            try:
                resp = self.c.model_delete(kind, name)
            except ClientError:
                resp = {"ok": False,
                        "error": "daemon not running - start it to delete "
                                 "models"}
            except Exception as e:  # noqa: BLE001 - surfaced as a toast
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(self._after_model_delete, name, resp)

        threading.Thread(target=work, daemon=True).start()

    def _after_model_delete(self, name: str, resp: dict) -> None:
        if resp.get("ok"):
            freed = model_catalog.human_bytes(int(resp.get("bytes") or 0))
            self.toast(f"Deleted {name} (freed {freed})")
        else:
            self.toast(str(resp.get("error") or "delete failed"))
        self._refresh_models()

    # -- whisper.cpp GGUF models (download + use) ---------------------------------

    def _active_gguf(self) -> str | None:
        m = self.cfg.get("model", {})
        if str(m.get("backend", "")) != "whisper.cpp":
            return None
        val = str(m.get("whispercpp_model", "")).strip()
        return val if val in model_catalog.GGUF_CATALOG else None

    def _dl_subtitle(self, name: str) -> str:
        st = self._gguf_dl.get(name) or {}
        b, t = st.get("bytes", 0), st.get("total")

        def mb(n):
            return f"{n / 1_000_000:.0f} MB"

        return f"downloading… {mb(b)} / {mb(t)}" if t else f"downloading… {mb(b)}"

    def _refresh_gguf_rows(self) -> None:
        for row in self._gguf_rows:
            self.gguf_group.remove(row)
        self._gguf_rows = []
        active = self._active_gguf()
        for name, info in model_catalog.GGUF_CATALOG.items():
            row = Adw.ActionRow(
                title=name,
                subtitle=(f"{info['size']} · {info['langs']} languages · "
                          f"{info['note']}"))
            if name == active:
                row.add_suffix(Gtk.Label(label="Active",
                                         css_classes=["success", "caption"]))
            elif (name in self._gguf_dl and not self._gguf_dl[name].get("done")
                    and not self._gguf_dl[name].get("error")):
                row.set_subtitle(self._dl_subtitle(name))
                row.add_suffix(Gtk.Spinner(spinning=True))
            elif model_catalog.gguf_downloaded(name):
                btn = Gtk.Button(label="Use", css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._use_gguf, name)
                row.add_suffix(btn)
            else:
                btn = Gtk.Button(label="Download & use",
                                 css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._download_gguf, name)
                row.add_suffix(btn)
            self.gguf_group.add(row)
            self._gguf_rows.append(row)

    def _download_gguf(self, _btn, name: str) -> None:
        if name in self._gguf_dl and not (self._gguf_dl[name].get("done")
                                          or self._gguf_dl[name].get("error")):
            return  # already running
        self._gguf_dl[name] = {"bytes": 0, "total": None, "done": False,
                               "error": None}
        self._refresh_models()

        def work():
            st = self._gguf_dl[name]
            try:
                model_download.download_gguf(
                    name, progress=lambda b, t: st.update(bytes=b, total=t))
                st["done"] = True
            except Exception as e:  # noqa: BLE001 - surfaced as a toast
                st["error"] = str(e)[:300]

        threading.Thread(target=work, daemon=True).start()
        GLib.timeout_add(400, self._poll_gguf_dl, name)

    def _poll_gguf_dl(self, name: str) -> bool:
        st = self._gguf_dl.get(name)
        if st is None or not (st.get("done") or st.get("error")):
            # still running: rebuild rows for a live subtitle (download state
            # survives because it lives in self._gguf_dl, not the rows)
            self._refresh_models()
            return True
        self._refresh_models()
        if st.get("error"):
            self.toast(f"download failed: {st['error']}")
        else:
            self.toast(f"{name} downloaded — click Use to switch")
        return False  # stop the timer

    def _use_gguf(self, _btn, name: str) -> None:
        if not model_catalog.gguf_downloaded(name):
            self.toast(f"{name} is not downloaded yet")
            return
        try:
            resp = self.c.set_config({"model": {
                "backend": "whisper.cpp", "whispercpp_model": name}})
        except Exception as e:
            self.toast(str(e))
            return
        if resp.get("rejected") or resp.get("errors"):
            self.toast("Rejected: " + ", ".join(
                (resp.get("rejected") or []) + (resp.get("errors") or [])))
            return
        self.toast(f"Switching to whisper.cpp ({name})…")
        self._load()  # resync cfg + rows
        GLib.timeout_add_seconds(1, self._poll_model)  # existing warmup poll

    # -- Parakeet ONNX models (download + use) ------------------------------------

    def _active_parakeet(self) -> str | None:
        m = self.cfg.get("model", {})
        if str(m.get("backend", "")) != "parakeet":
            return None
        name = str(m.get("name", "")).strip()
        return name if name in model_catalog.PARAKEET_CATALOG else None

    def _parakeet_dl_subtitle(self, name: str) -> str:
        st = self._parakeet_dl.get(name) or {}
        b, t = st.get("bytes", 0), st.get("total")

        def mb(n):
            return f"{n / 1_000_000:.0f} MB"

        return f"downloading… {mb(b)} / {mb(t)}" if t else f"downloading… {mb(b)}"

    def _refresh_parakeet_rows(self) -> None:
        for row in self._parakeet_rows:
            self.parakeet_group.remove(row)
        self._parakeet_rows = []
        active = self._active_parakeet()
        for name, info in model_catalog.PARAKEET_CATALOG.items():
            row = Adw.ActionRow(
                title=name,
                subtitle=f"{info['size']} · {info['langs']} · {info['note']}")
            if name == active:
                row.add_suffix(Gtk.Label(label="Active",
                                         css_classes=["success", "caption"]))
            elif (name in self._parakeet_dl
                    and not self._parakeet_dl[name].get("done")
                    and not self._parakeet_dl[name].get("error")):
                row.set_subtitle(self._parakeet_dl_subtitle(name))
                row.add_suffix(Gtk.Spinner(spinning=True))
            elif model_catalog.parakeet_downloaded(name):
                btn = Gtk.Button(label="Use", css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._use_parakeet, name)
                row.add_suffix(btn)
            else:
                btn = Gtk.Button(label="Download & use",
                                 css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._download_parakeet, name)
                row.add_suffix(btn)
            self.parakeet_group.add(row)
            self._parakeet_rows.append(row)

    def _download_parakeet(self, _btn, name: str) -> None:
        if name in self._parakeet_dl and not (self._parakeet_dl[name].get("done")
                                              or self._parakeet_dl[name].get("error")):
            return  # already running
        self._parakeet_dl[name] = {"bytes": 0, "total": None, "done": False,
                                   "error": None}
        self._refresh_models()

        def work():
            st = self._parakeet_dl[name]
            try:
                model_download.download_parakeet(
                    name, progress=lambda b, t: st.update(bytes=b, total=t))
                st["done"] = True
            except Exception as e:  # noqa: BLE001 - surfaced as a toast
                st["error"] = str(e)[:300]

        threading.Thread(target=work, daemon=True).start()
        GLib.timeout_add(400, self._poll_parakeet_dl, name)

    def _poll_parakeet_dl(self, name: str) -> bool:
        st = self._parakeet_dl.get(name)
        if st is None or not (st.get("done") or st.get("error")):
            # still running: rebuild rows for a live subtitle
            self._refresh_models()
            return True
        self._refresh_models()
        if st.get("error"):
            self.toast(f"download failed: {st['error']}")
        else:
            self.toast(f"{name} downloaded — click Use to switch")
        return False  # stop the timer

    def _use_parakeet(self, _btn, name: str) -> None:
        if not model_catalog.parakeet_downloaded(name):
            self.toast(f"{name} is not downloaded yet")
            return
        try:
            resp = self.c.set_config(
                {"model": {"backend": "parakeet", "name": name}})
        except Exception as e:
            self.toast(str(e))
            return
        if resp.get("rejected") or resp.get("errors"):
            self.toast("Rejected: " + ", ".join(
                (resp.get("rejected") or []) + (resp.get("errors") or [])))
            return
        self.toast(f"Switching to parakeet ({name})…")
        self._load()  # resync cfg + rows
        GLib.timeout_add_seconds(1, self._poll_model)  # existing warmup poll

    # -- AI page -------------------------------------------------------------------------

    def _build_ai(self) -> None:
        page = Adw.PreferencesPage(name="ai", icon_name="fluidvoice-polish-symbolic",
                                   title="AI Polish")
        grp = Adw.PreferencesGroup(
            title="AI polish",
            description="Any OpenAI-compatible endpoint — Ollama, LM Studio, "
                        "OpenAI, Groq…")
        grp.add(self._switch("ai", "enabled", "Enabled"))
        url_row = self._entry("ai", "base_url",
                              "Base URL — http://localhost:11434/v1")
        self.provider_img = Gtk.Image(pixel_size=18)
        self.provider_img.set_valign(Gtk.Align.CENTER)
        url_row.add_prefix(self.provider_img)
        url_row.connect("changed", lambda *_: self._update_provider_logo())
        grp.add(url_row)
        grp.add(self._entry("ai", "model", "Model — e.g. qwen3:8b"))
        grp.add(self._entry("ai", "api_key_env", "API key env var (preferred)"))
        grp.add(self._spin("ai", "temperature", "Temperature", 0.0, 2.0, 0.1,
                           digits=1))
        grp.add(self._spin("ai", "timeout_seconds", "Timeout (s)", 1, 3600, 5))
        grp.add(self._spin("ai", "max_retries", "Max retries", 0, 10, 1))
        grp.add(self._switch("ai", "refusal_guard", "Refusal guard",
                             subtitle="a reply that reads as a model refusal "
                                      "(\"I'm sorry, I can't assist…\") is "
                                      "never typed — the raw transcript is "
                                      "used instead"))
        test_row = Adw.ActionRow(title="Test connection")
        test_btn = Gtk.Button(label="Test", css_classes=["suggested-action"])
        test_btn.set_valign(Gtk.Align.CENTER)
        self.test_out = Gtk.Label(css_classes=["dim-label"], wrap=True,
                                  max_width_chars=28)
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
                        "the text into the editor")
        self._profile_combo = Adw.ComboRow(
            title="Profile", subtitle="Select to load it into the editor")
        self._profile_combo.connect("notify::selected",
                                    self._on_profile_selected)
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
        del_btn = Gtk.Button(label="Delete",
                             css_classes=["flat", "destructive-action"])
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
                        "(empty = the built-in dictation prompt)")
        tv = Gtk.TextView(hexpand=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        buf.connect("changed", lambda *_: self._touch())
        self._rows[("ai", "base_prompt")] = _TextProxy(buf)
        prompt_grp.add(_InstructionRow(tv, title="Base prompt"))
        builtin_row = Adw.ActionRow(
            title="Built-in template",
            subtitle="Start editing from the shipped dictation prompt")
        builtin_btn = Gtk.Button(label="Insert built-in", css_classes=["flat"])
        builtin_btn.set_valign(Gtk.Align.CENTER)
        builtin_btn.connect("clicked", self._insert_builtin_prompt)
        builtin_row.add_suffix(builtin_btn)
        prompt_grp.add(builtin_row)
        page.add(prompt_grp)

        self.rules_group = Adw.PreferencesGroup(
            title="Per-app prompts",
            description="Extra polish instructions when dictating into a "
                        "matching app (first match wins, * = everywhere)")
        add_row = Adw.ActionRow(title="Add rule")
        add_btn = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"])
        add_btn.connect("clicked", lambda *_: self._add_rule({}))
        add_row.add_suffix(add_btn)
        self.rules_group.add(add_row)
        page.add(self.rules_group)

        cmd = Adw.PreferencesGroup(
            title="Command mode",
            description="Voice → terminal agent. Every command needs confirmation.")
        cmd.add(self._spin("command", "max_turns", "Max agent turns", 1, 20, 1))
        cmd.add(self._entry("command", "working_dir",
                            "Working directory (empty = home)"))
        cmd.add(self._spin("command", "timeout_seconds", "Command timeout (s)",
                           1, 3600, 5, digits=1))
        cmd.add(self._spin("command", "confirm_timeout_s",
                           "Confirmation timeout (s)", 5, 600, 5, digits=1))
        page.add(cmd)
        page.add(self._save_group())
        self.add(page)

    def _update_provider_logo(self) -> None:
        """Show the macOS-style provider logo matching the AI base URL."""
        from .logos import logo_path, provider_for
        row = self._rows.get(("ai", "base_url"))
        text = row.get_text() if row is not None else ""
        dark = Adw.StyleManager.get_default().get_dark()
        path = logo_path(provider_for(text), dark)
        if path:
            try:
                self.provider_img.set_from_paintable(
                    Gdk.Texture.new_from_filename(path))
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
            GLib.idle_add(self.test_out.set_text,
                          f"ok: {resp['reply']}" if resp.get("ok")
                          else str(resp.get("error") or "failed"))
        threading.Thread(target=work, daemon=True).start()

    def _get_text(self, sec, key) -> str:
        row = self._rows.get((sec, key))
        return row.get_text() if isinstance(row, Adw.EntryRow) else ""

    def _insert_builtin_prompt(self, _btn) -> None:
        """Load the shipped dictation prompt into the editor (editing the
        ~1.5 kB prompt from an empty buffer is hostile)."""
        from ..ai.prompts import default_dictation_prompt
        self._rows[("ai", "base_prompt")].set_value(default_dictation_prompt())
        self._touch()

    # -- prompt profiles ------------------------------------------------------

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
            transient_for=self, modal=True,
            heading=f"Delete profile \u201c{name}\u201d?",
            body="The preset is removed from disk. The prompt saved in your "
                 "config is not touched.")
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

    # -- per-app prompt rules --------------------------------------------------------

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

        remove_btn = Gtk.Button(icon_name="user-trash-symbolic",
                                css_classes=["flat", "destructive-action"])
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
            apps = [a.strip() for a in r["apps"].get_text().split(",")
                    if a.strip()]
            text = r["buf"].get_text(r["buf"].get_start_iter(),
                                     r["buf"].get_end_iter(), False).strip()
            if apps and text:
                rules.append({"apps": apps, "instructions": text})
            elif apps or text:
                self.toast("Incomplete per-app rule skipped (needs apps "
                           "and instructions)")
        return rules

    # -- microphone priority list (auto-switch patterns) ----------------------

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
        up = Gtk.Button(icon_name="go-up-symbolic", css_classes=["flat"],
                        tooltip_text="Move up")
        down = Gtk.Button(icon_name="go-down-symbolic", css_classes=["flat"],
                          tooltip_text="Move down")
        rm = Gtk.Button(icon_name="user-trash-symbolic",
                        css_classes=["flat", "destructive-action"],
                        tooltip_text="Remove this pattern")
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
        self._mic_prio_rows[i], self._mic_prio_rows[j] = \
            self._mic_prio_rows[j], self._mic_prio_rows[i]
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
        return [r["row"].get_text().strip() for r in self._mic_prio_rows
                if r["row"].get_text().strip()]

    # -- language cycle list (hotkey.language_key steps it) ----------------------

    def _load_language_cycle(self, codes: list) -> None:
        for ref in list(self._cycle_rows):
            self.cycle_group.remove(ref["row"])
        self._cycle_rows = []
        for code in codes:
            self._add_cycle_lang(str(code))

    def _add_cycle_lang(self, value: str) -> None:
        row = Adw.EntryRow(title="Code — e.g. auto, en, sl")
        row.set_text(value)
        row.connect("changed", self._on_cycle_lang_changed)
        up = Gtk.Button(icon_name="go-up-symbolic", css_classes=["flat"],
                        tooltip_text="Move up")
        down = Gtk.Button(icon_name="go-down-symbolic", css_classes=["flat"],
                          tooltip_text="Move down")
        rm = Gtk.Button(icon_name="user-trash-symbolic",
                        css_classes=["flat", "destructive-action"],
                        tooltip_text="Remove this language")
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
        self._cycle_rows[i], self._cycle_rows[j] = \
            self._cycle_rows[j], self._cycle_rows[i]
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
        return [r["row"].get_text().strip().lower()
                for r in self._cycle_rows if r["row"].get_text().strip()]

    # -- custom dictionary (upstream Custom Dictionary) ---------------------------

    def _load_dictionary(self, entries: list) -> None:
        for r in list(self._dict_rows):
            self.dict_group.remove(r["exp"])
        self._dict_rows = []
        for entry in entries:
            self._add_dict_word(entry)

    def _add_dict_word(self, entry: dict) -> None:
        exp = Adw.ExpanderRow(
            title=", ".join(entry.get("triggers", [])) or "New word")
        trig = Adw.EntryRow(title="Triggers (comma-separated)")
        trig.set_text(", ".join(entry.get("triggers", [])))
        repl = Adw.EntryRow(title="Replacement")
        repl.set_text(str(entry.get("replacement", "")))
        rm = Gtk.Button(icon_name="user-trash-symbolic",
                        css_classes=["flat", "destructive-action"],
                        tooltip_text="Remove this word")
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
            triggers = [t.strip() for t in r["trig"].get_text().split(",")
                        if t.strip()]
            replacement = r["repl"].get_text().strip()
            if triggers and replacement:
                entries.append({"triggers": triggers,
                                "replacement": replacement})
            elif triggers or replacement:
                self.toast("Incomplete dictionary word skipped (needs "
                           "triggers and a replacement)")
        return entries

    # -- dictionary suggestions (auto-learned from history edits) ------------------

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
            row = Adw.ActionRow(title=f"{heard} → {corrected}",
                                subtitle=f"seen {s.get('count', 0)}×")
            ref = {"row": row, "heard": heard, "corrected": corrected}
            accept_btn = Gtk.Button(label="Accept",
                                    css_classes=["flat", "suggested-action"])
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
            resp = self.c.dict_suggestion_accept(ref["heard"],
                                                 ref["corrected"])
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

    # -- dictation page -----------------------------------------------------------------

    def _build_dictation(self) -> None:
        page = Adw.PreferencesPage(name="dictation",
            icon_name="fluidvoice-dictation-symbolic", title="Dictation")

        hk = Adw.PreferencesGroup(title="Hotkeys")
        hk.add(self._entry("hotkey", "key", "Dictation key — e.g. Right_Control, F9",
                           capture=True))
        hk.add(self._combo("hotkey", "mode", "Mode",
                           [("toggle", "toggle"), ("hold", "hold"),
                            ("both", "both")],
                           subtitle="tap to start/stop · hold = push-to-talk · "
                                    "both: quick tap toggles, holding talks · "
                                    "modifier-only keys need toggle"))
        hk.add(self._entry("hotkey", "cancel_key", "Cancel key — discards a take",
                           capture=True))
        hk.add(self._entry("hotkey", "rewrite_key", "Rewrite key (optional, needs AI)",
                           capture=True))
        hk.add(self._entry("hotkey", "command_key",
                           "Command key (optional, needs AI)", capture=True))
        hk.add(self._entry("hotkey", "paste_key",
                           "Paste-last key (optional) — re-types last text",
                           capture=True))
        hk.add(self._entry("hotkey", "language_key",
                           "Cycle-language key (optional) — steps "
                           "language_cycle", capture=True))

        # B1: up to two extra dictation shortcuts, each optionally bound
        # to a named prompt profile (the primary hotkey stays above)
        extra_pairs = []
        from ..ai.profiles import load_profiles
        profile_names = ["(none)"] + sorted(load_profiles())
        for i in (2, 3):
            key_row = Adw.EntryRow(
                title=f"Extra dictation shortcut {i} (optional)")
            key_row.connect("changed", lambda *_: self._touch())
            btn = Gtk.Button(icon_name="media-record-symbolic",
                             css_classes=["flat"], tooltip_text="Press a key…")
            btn.connect("clicked", self._start_capture, key_row)
            key_row.add_suffix(btn)
            hk.add(key_row)
            combo = Adw.ComboRow(
                title=f"Shortcut {i} prompt profile",
                subtitle="polishes takes started by this shortcut")
            combo.set_model(Gtk.StringList(strings=profile_names))
            combo.connect("notify::selected", lambda *_: self._touch())
            hk.add(combo)
            extra_pairs.append((key_row, combo))
        self._rows[("hotkey", "extra_shortcuts")] = \
            _ExtraShortcutsProxy(extra_pairs)
        mods = Adw.ActionRow(title="Extra modifiers",
                             subtitle="held in addition to the dictation key")
        for mod in ("ctrl", "alt", "shift", "super"):
            tb = Gtk.ToggleButton(label=mod, css_classes=["flat"])
            tb.set_valign(Gtk.Align.CENTER)
            tb.connect("toggled", lambda *_: self._touch())
            self._mod_toggles[mod] = tb
            mods.add_suffix(tb)
        hk.add(mods)
        page.add(hk)

        mic = Adw.PreferencesGroup(title="Microphone and recording")
        self.mic_row = Adw.ComboRow(title="Microphone",
                                    subtitle="Auto follows the system default")
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                 css_classes=["flat"], tooltip_text="Refresh list")
        refresh_btn.connect("clicked", lambda *_: self._fill_mics())
        self.mic_row.add_suffix(refresh_btn)
        self.mic_row.connect("notify::selected", lambda *_: self._touch())
        mic.add(self.mic_row)
        mic.add(self._combo("recording", "command", "Recorder",
                            [("auto", "auto"), ("pw-record", "pw-record"),
                             ("parecord", "parecord")]))
        mic.add(self._spin("recording", "max_seconds", "Max duration (s)",
                           5, 3600, 5))
        mic.add(self._spin("recording", "first_pcm_timeout", "No-audio timeout (s)",
                           0, 60, 0.5, digits=1,
                           subtitle="0 = off; stops a muted/wrong mic fast"))
        mic.add(self._switch("recording", "skip_silent", "Skip silent takes",
                             "Discard obviously-silent recordings ≤ 4 s"))
        mic.add(self._switch("recording", "pause_media", "Pause media",
                             "Pause MPRIS players while dictating"))
        page.add(mic)

        self.mic_prio_group = Adw.PreferencesGroup(
            title="Microphone priority",
            description="Ordered name patterns (e.g. bluez for a Bluetooth "
                        "headset). When the chosen microphone disappears, "
                        "the first available match is used.")
        self._mic_prio_add_row = Adw.ActionRow(title="Add pattern")
        add_p_btn = Gtk.Button(icon_name="list-add-symbolic",
                               css_classes=["flat"])
        add_p_btn.connect("clicked", lambda *_: self._add_mic_prio(""))
        self._mic_prio_add_row.add_suffix(add_p_btn)
        self.mic_prio_group.add(self._mic_prio_add_row)
        page.add(self.mic_prio_group)

        preview = Adw.PreferencesGroup(
            title="Live preview",
            description="The pill overlay with partial text while recording")
        preview.add(self._switch("recording", "preview_enabled", "Enabled"))
        preview.add(self._combo("recording", "preview_mode", "Mode",
                                [("auto", "auto"), ("overlay", "overlay"),
                                 ("notify", "notifications only")]))
        preview.add(self._combo("recording", "preview_overlay_size", "Size",
                                [("pill", "pill"), ("small", "small"),
                                 ("medium", "medium"), ("large", "large")],
                                subtitle="macOS size presets"))
        preview.add(self._spin("recording", "preview_bottom_offset",
                               "Bottom offset (px)", 0, 400, 2))
        preview.add(self._spin("recording", "preview_interval",
                               "Update interval (s)", 0.3, 10.0, 0.1, digits=1))
        preview.add(self._spin("recording", "preview_min_audio",
                               "First partial after (s)", 0.3, 10.0, 0.1,
                               digits=1))
        preview.add(self._switch("recording", "preview_segmented",
                                 "Streaming (segmented)",
                                 "constant-cost windows instead of whole-take "
                                 "re-decode; all backends"))
        preview.add(self._spin("recording", "preview_segment_s",
                               "Segment window (s)", 1.0, 6.0, 0.5, digits=1))
        preview.add(self._switch("recording", "overlay_chips",
                                 "Hover action chips",
                                 "hover the pill while recording for "
                                 "copy/paste/cancel buttons (X11)"))
        preview.add(self._spin("recording", "preview_vad_silence_s",
                               "Stop after silence (s)", 0.0, 10.0, 0.5,
                               digits=1,
                               subtitle="0 keeps the old behavior (hotkey "
                               "stops every take)"))
        page.add(preview)

        polish = Adw.PreferencesGroup(title="Text polish")
        polish.add(self._switch("processing", "remove_filler_words",
                                "Remove filler words",
                                "um, uh, hmm… before punctuation"))
        fillers = Adw.EntryRow(title="Filler words (comma-separated)")
        fillers.connect("changed", lambda *_: self._touch())
        self._rows[("processing", "filler_words")] = _ListProxy(fillers)

        actions_g = Adw.PreferencesGroup(
            title="Spoken formatting triggers",
            description="Extra phrases that trigger formatting actions "
                        "(on top of the built-ins; still needs the prefix)")
        trig_rows = {}
        for action, title in (("new_line", "New line"),
                              ("new_paragraph", "New paragraph"),
                              ("tab", "Tab"),
                              ("space", "Space")):
            row = Adw.EntryRow(title=f"{title} — extra trigger phrases")
            row.connect("changed", lambda *_: self._touch())
            trig_rows[action] = row
            actions_g.add(row)
        actions_g.add(Adw.ActionRow(
            title="Example",
            subtitle="nova vrstica, naslednja vrstica — then say "
                     "“literal nova vrstica” to start a new line"))
        polish.add(actions_g)
        self._rows[("processing", "formatting_action_triggers")] = \
            _ActionTriggersProxy(trig_rows)
        polish.add(fillers)
        polish.add(self._switch("processing", "punctuation_enabled",
                                "Spoken punctuation", '"literal comma" → ,'))
        polish.add(self._entry("processing", "punctuation_prefix",
                               "Spoken-command prefix"))
        gaav = Adw.ExpanderRow(
            title="Search-field formatting (GAAV)",
            subtitle="lowercase first letter, drop the final period")
        gaav.add_row(self._plain_switch_row("processing", "gaav_enabled",
                                            "Enabled"))
        gaav.add_row(self._plain_switch_row("processing", "gaav_lowercase_first",
                                            "Lowercase first letter"))
        gaav.add_row(self._plain_switch_row(
            "processing", "gaav_remove_trailing_period", "Remove trailing period"))
        polish.add(gaav)

        send = Adw.ExpanderRow(
            title="Spoken send",
            subtitle='a trailing phrase strips and presses Enter')
        send.add_row(self._plain_switch_row("recording", "spoken_send_enabled",
                                            "Enabled"))
        phrase = self._entry("recording", "spoken_send_phrase", "Phrase")
        send.add_row(phrase)
        send.add_row(self._combo("recording", "spoken_send_key", "Key",
                                 [("enter", "enter"),
                                  ("shift+enter", "shift+enter"),
                                  ("ctrl+enter", "ctrl+enter")]))
        polish.add(send)
        page.add(polish)

        self.dict_group = Adw.PreferencesGroup(
            title="Custom dictionary",
            description='Phrases replaced on insert — "miro board" → "Miro board"')
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
                        "accept to teach the dictionary")
        self.suggest_group.set_visible(False)  # until the first load
        self._suggest_rows: list[dict] = []
        page.add(self.suggest_group)

        ins = Adw.PreferencesGroup(title="Insertion",
                                   description="How typed text reaches your apps")
        ins.add(self._combo("insertion", "mode", "Mode",
                            [("auto", "auto"), ("typed", "typed"),
                             ("paste", "paste")]))
        ins.add(self._spin("insertion", "type_delay_ms", "Typing delay (ms)",
                           0, 1000, 1))
        ins.add(self._spin("insertion", "paste_threshold_chars",
                           "Paste threshold (chars)", 1, 100000, 50))
        page.add(ins)
        page.add(self._save_group())
        self.add(page)

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
        self.mic_row.set_selected(value_list.index(current)
                                  if current in value_list else 0)
        self._loading = False

    # -- wayland page (session/capabilities, DE-shortcut assist, tools) --------

    _DE_SHORTCUT_PANELS = {"gnome": ["gnome-control-center", "keyboard"],
                           "kde": ["systemsettings", "kcm_keys"],
                           "plasma": ["systemsettings", "kcm_keys"]}

    def _build_wayland(self) -> None:
        from .. import session as session_mod
        self.wayland_page = Adw.PreferencesPage(
            name="wayland", icon_name="fluidvoice-general-symbolic",
            title="Wayland")

        caps = Adw.PreferencesGroup(
            title="Session and capabilities",
            description="What the daemon resolved for this session "
                        "(informational; refreshed on open)")
        self._wayland_cap_rows: dict[str, Adw.ActionRow] = {}
        for cap, label in (("session", "Session"), ("hotkey", "Hotkey"),
                           ("insertion", "Insertion"),
                           ("clipboard", "Clipboard"),
                           ("overlay", "Live preview"),
                           ("app-hint", "App hints")):
            row = Adw.ActionRow(title=label, subtitle="\u2014")
            caps.add(row)
            self._wayland_cap_rows[cap] = row
        self.wayland_page.add(caps)

        self.wayland_bind_group = Adw.PreferencesGroup(
            title="Bind your hotkey",
            description="No global key grabs exist on Wayland: bind a custom "
                        "command shortcut in your desktop environment")
        self.wayland_script_row = Adw.ActionRow(
            title="Command to bind",
            subtitle="\u2014")
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic",
                              css_classes=["flat"], tooltip_text="Copy")
        copy_btn.connect("clicked", self._copy_toggle_script)
        self.wayland_script_row.add_suffix(copy_btn)
        self.wayland_bind_group.add(self.wayland_script_row)
        self._wayland_instruction_rows: list[Adw.ActionRow] = []
        self.wayland_open_row = Adw.ActionRow(
            title="Shortcut settings",
            subtitle="Open your desktop environment's panel")
        open_btn = Gtk.Button(label="Open\u2026",
                              css_classes=["suggested-action"])
        open_btn.connect("clicked", self._open_de_shortcut_settings)
        self.wayland_open_row.add_suffix(open_btn)
        self.wayland_bind_group.add(self.wayland_open_row)
        self.wayland_page.add(self.wayland_bind_group)

        tools = Adw.PreferencesGroup(
            title="Insertion and push-to-talk",
            description="Wayland typing tool and optional physical "
                        "push-to-talk")
        tools.add(self._combo(
            "insertion", "wayland_tool", "Typing tool",
            [("auto", "auto"), ("wtype", "wtype"), ("ydotool", "ydotool")],
            subtitle="auto: wtype (wlroots/KDE) then ydotool (anywhere); "
                     "GNOME has no virtual-keyboard protocol for wtype"))
        tools.add(self._switch(
            "hotkey", "wayland_evdev", "Evdev push-to-talk",
            "Hold a physical key read from /dev/input - privileged path "
            "(input group + python-evdev)"))
        tools.add(self._entry("hotkey", "wayland_evdev_device",
                              "Device name pattern \u2014 e.g. AT Translated"))
        tools.add(self._entry("hotkey", "wayland_evdev_key",
                              "Hold key \u2014 ecodes name (KEY_RIGHTCTRL)"))
        self.wayland_page.add(tools)
        self.wayland_page.add(self._save_group())
        self.add(self.wayland_page)
        _ = session_mod  # (used by the refresh below, imported here once)

    def _refresh_wayland_rows(self) -> None:
        """Fill the session/capability rows, the bindable command and the
        per-DE instructions from the live session (called on every load)."""
        from .. import session as session_mod
        info = session_mod.probe()
        caps = session_mod.capabilities(info, cfg=self.cfg)
        self._wayland_cap_rows["session"].set_subtitle(
            info.type + (f" ({info.desktop})" if info.desktop else ""))
        for cap in ("hotkey", "insertion", "clipboard", "overlay",
                    "app-hint"):
            self._wayland_cap_rows[cap].set_subtitle(str(caps.get(cap, "?")))
        script = session_mod.ensure_toggle_script()
        command = str(script) if script is not None \
            else session_mod.toggle_command()
        self.wayland_script_row.set_subtitle(command)
        for row in self._wayland_instruction_rows:
            self.wayland_bind_group.remove(row)
        self._wayland_instruction_rows = []
        for line in session_mod.de_shortcut_instructions(info.desktop_all,
                                                         command):
            row = Adw.ActionRow(title=line.strip() or " ")
            self.wayland_bind_group.add(row)
            self._wayland_instruction_rows.append(row)
        self.wayland_open_row.set_visible(self._de_panel_command() is not None)

    def _de_panel_command(self) -> list[str] | None:
        """The DE shortcut-settings command for this desktop, if known
        (token matching over the FULL XDG_CURRENT_DESKTOP, so ubuntu:GNOME
        still finds the GNOME panel)."""
        from .. import session as session_mod
        desktop = session_mod.probe().desktop_all
        if session_mod.is_gnome_desktop(desktop):
            return self._DE_SHORTCUT_PANELS["gnome"]
        if any(t in ("kde", "plasma")
               for t in desktop.split(":")):
            return self._DE_SHORTCUT_PANELS["kde"]
        return None

    def _copy_toggle_script(self, *_args) -> None:
        text = self.wayland_script_row.get_subtitle()
        if text and text != "\u2014":
            Gdk.Display.get_default().get_clipboard().set_text(text)
            self.toast("Copied the command to bind")

    def _open_de_shortcut_settings(self, *_args) -> None:
        import subprocess
        panel = self._de_panel_command()
        if panel is None:
            return
        try:
            subprocess.Popen(panel, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            self.toast("Opening shortcut settings")
        except Exception as e:
            self.toast(f"Could not open: {e}")

    # -- history page -----------------------------------------------------------------

    def _build_history_page(self) -> None:
        page = Adw.PreferencesPage(name="history", icon_name="fluidvoice-history-symbolic",
                                   title="History")
        grp = Adw.PreferencesGroup(title="History")
        grp.add(self._switch("history", "save", "Save transcriptions"))
        grp.add(self._switch("history", "save_audio", "Keep audio",
                             "Store the recording with each entry"))
        grp.add(self._spin("history", "audio_budget_gb", "Audio budget (GB)",
                           0.0, 1024.0, 0.5, digits=1))
        clear = Adw.ActionRow(title="Clear all history",
                              subtitle="Delete every entry and retained audio")
        clear_btn = Gtk.Button(label="Clear…", css_classes=["destructive-action"])
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.connect("clicked", self._confirm_clear_history)
        clear.add_suffix(clear_btn)
        grp.add(clear)
        browse = Adw.ActionRow(title="Browse history",
                               subtitle="Open the History window")
        b_btn = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat"])
        b_btn.connect("clicked", lambda *_: (self.get_application()
                                             and self.get_application()
                                             .show_history()))
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
            transient_for=self, modal=True, heading="Clear all history?",
            body="Every saved transcription and any retained audio will be "
                 "deleted. This cannot be undone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("clear", "Clear All")
        dlg.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", confirmed)
        dlg.present()

    # -- about -----------------------------------------------------------------------

    def _build_about(self) -> None:
        page = Adw.PreferencesPage(name="about", icon_name="fluidvoice-about-symbolic", title="About")
        grp = Adw.PreferencesGroup(title="About")
        from .. import paths
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
                ("History", str(paths.data_dir() / "history.jsonl"))):
            grp.add(Adw.ActionRow(title=title, subtitle=value))
        about_btn_row = Adw.ActionRow(title="About SayItErmano")
        about_btn = Gtk.Button(icon_name="help-about-symbolic",
                               css_classes=["flat"])
        about_btn.connect("clicked", self._show_about_dialog)
        about_btn_row.add_suffix(about_btn)
        grp.add(about_btn_row)
        page.add(grp)
        self.add(page)

    def _show_about_dialog(self, *_args) -> None:
        dlg = Adw.AboutDialog(
            application_name="SayItErmano",
            application_icon="sayit-ermano",
            version=APP_VERSION,
            website="https://github.com/acailic/SayItErmano",
            issue_url="https://github.com/acailic/SayItErmano/issues",
            license_type=Gtk.License.GPL_3_0)
        dlg.present(self)


def _default(section: str, key: str):
    return DEFAULTS.get(section, {}).get(key)


def _keyname(keyval) -> str:
    name = Gdk.keyval_name(keyval) or ""
    if len(name) == 1 and name.isalpha():
        return name.lower()
    return _KEY_REMAP.get(name, name)
