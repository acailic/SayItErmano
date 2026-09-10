"""Settings window — shell + page mixins (audit C5b split).

The shell owns: window scaffolding, the field registry (self._rows) with
its row factories, load/collect/save over the socket client, the dirty
state and the per-page save bar, and key capture. Each page's build and
refresh logic lives in its own module under settings_pages/ and is mixed
into SettingsWindow - this split is structural only; every method and
attribute keeps its name, so tests and call sites are unchanged.

Covers every key the daemon's set-config validates. Saving goes through
the socket (live cfg + hot-apply); with the daemon down it degrades to
file-only mode (the save row and the load toast say so). Unsaved changes
are tracked: the Save row / Ctrl+S apply, closing with pending changes
asks first.
"""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..config import KNOWN_LANGUAGES as LANGUAGES
from ..config import enum_options, ui_range
from .client import Client
from .settings_pages.about import AboutPageMixin
from .settings_pages.ai import AIPageMixin
from .settings_pages.common import (
    _default,
    _keyname,
    _ListProxy,
    _PasswordProxy,
    _SwitchProxy,
    _TextProxy,
)
from .settings_pages.dictation import DictationPageMixin
from .settings_pages.general import GeneralPageMixin
from .settings_pages.history import HistoryPageMixin
from .settings_pages.models import ModelsPageMixin
from .settings_pages.wayland import WaylandPageMixin
from .style import load_style


class SettingsWindow(
    Adw.ApplicationWindow,
    GeneralPageMixin,
    ModelsPageMixin,
    AIPageMixin,
    DictationPageMixin,
    WaylandPageMixin,
    HistoryPageMixin,
    AboutPageMixin,
):
    _loading_first_done = False

    def __init__(self, application=None, client=None):
        super().__init__(
            application=application,
            title="Settings",
            default_width=840,
            default_height=700,
        )
        load_style()  # after super(): display is open, icons resolve
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
        self._save_btns: list[Gtk.Button] = []
        self._discard_btns: list[Gtk.Button] = []
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

        self._build_chrome()
        # macOS-parity sidebar order (upstream Settings screenshot set,
        # 2026-09-09): Settings section first, Linux-specific + About under
        # "More"
        self._build_general()
        self._build_dictation()
        self._build_models()
        self._build_ai()
        self._build_history_page()
        self._build_wayland()
        self._build_about()
        self._select_first_page()

        self.install_action("settings.save", None, lambda w, _n, _p: w.save())
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_: self._update_provider_logo()
        )
        ctrl = Gtk.ShortcutController()
        ctrl.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("<primary>s"),
                action=Gtk.NamedAction.new("settings.save"),
            )
        )
        self.add_controller(ctrl)
        self.connect("close-request", self._on_close_request)

        self._load()

    # -- sidebar navigation (audit C5b/2: NavigationSplitView replaces the
    #    PreferencesWindow bottom tab switcher - one page at a time, the
    #    standard GNOME settings pattern; collapses to push navigation on
    #    narrow windows) ------------------------------------------------------

    def _build_chrome(self) -> None:
        self._page_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
            hexpand=True,
        )
        self._sidebar_rows: dict[str, Gtk.ListBoxRow] = {}
        self._sidebar_sections: set[str] = set()
        self._sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar.connect("row-activated", self._on_sidebar_activated)
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_child(self._sidebar)
        sidebar_header = Adw.HeaderBar(
            title_widget=Adw.WindowTitle(title="Settings"),
            show_back_button=False,
        )
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_toolbar.add_top_bar(sidebar_header)
        sidebar_toolbar.set_content(sidebar_scroll)
        sidebar_page = Adw.NavigationPage(title="Settings", child=sidebar_toolbar)

        self._content_title = Adw.WindowTitle(title="Settings")
        content_header = Adw.HeaderBar(title_widget=self._content_title)
        content_toolbar = Adw.ToolbarView()
        content_toolbar.add_top_bar(content_header)
        content_toolbar.set_content(self._page_stack)
        content_page = Adw.NavigationPage(title="", child=content_toolbar)

        self._split = Adw.NavigationSplitView(
            sidebar=sidebar_page, content=content_page
        )
        self._toast_overlay = Adw.ToastOverlay(child=self._split)
        self.set_content(self._toast_overlay)

    def _add_page(self, page: Adw.PreferencesPage,
                  section: str = "settings") -> None:
        """Register a built page: content stack + one sidebar row, under a
        macOS-parity section header ("Settings" / "More")."""
        self._page_stack.add_named(page, page.get_name())
        if section not in self._sidebar_sections:
            self._sidebar_sections.add(section)
            self._sidebar.append(self._section_header(
                "Settings" if section == "settings" else "More"))
        hbox = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            margin_top=6,
            margin_bottom=6,
            margin_start=8,
            margin_end=8,
        )
        hbox.append(Gtk.Image(icon_name=page.get_icon_name()))
        hbox.append(Gtk.Label(label=page.get_title(), halign=Gtk.Align.START))
        row = Gtk.ListBoxRow(child=hbox)
        self._sidebar_rows[page.get_name()] = row
        self._sidebar.append(row)

    @staticmethod
    def _section_header(label: str) -> Gtk.ListBoxRow:
        return Gtk.ListBoxRow(
            activatable=False, selectable=False,
            child=Gtk.Label(label=label, xalign=0.0,
                            css_classes=["caption", "dim-label"],
                            margin_top=10, margin_bottom=2,
                            margin_start=12))

    def _select_first_page(self) -> None:
        row = self._sidebar.get_first_child()
        while row is not None and not row.get_activatable():
            row = row.get_next_sibling()
        if row is not None:
            self._sidebar.select_row(row)
            self._show_selected_page(row)

    def _on_sidebar_activated(self, _list, row) -> None:
        self._show_selected_page(row)
        self._split.set_show_content(True)  # collapsed: push the page

    def _show_selected_page(self, row) -> None:
        for name, r in self._sidebar_rows.items():
            if r is row:
                self._page_stack.set_visible_child_name(name)
                self._content_title.set_title(
                    next(
                        (
                            p.get_title()
                            for p in self._iter_pages()
                            if p.get_name() == name
                        ),
                        "Settings",
                    )
                )
                break

    def _iter_pages(self):
        child = self._page_stack.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    def _show_page(self, name: str) -> None:
        """Programmatic page select (tests, drivers)."""
        row = self._sidebar_rows.get(name)
        if row is not None:
            self._sidebar.select_row(row)
            self._show_selected_page(row)

    def _save_group(self) -> Adw.PreferencesGroup:
        grp = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=self._save_title())
        self._save_rows.append(row)
        discard_btn = Gtk.Button(label="Discard", css_classes=["flat"])
        discard_btn.set_valign(Gtk.Align.CENTER)
        discard_btn.connect("clicked", lambda *_: self._load())
        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.connect("clicked", lambda *_: self.save())
        self._save_btns.append(save_btn)
        self._discard_btns.append(discard_btn)
        row.add_suffix(discard_btn)
        row.add_suffix(save_btn)
        grp.add(row)
        self._save_groups.append(grp)
        return grp

    def _save_title(self) -> str:
        base = "Unsaved changes" if self._dirty else "All changes saved"
        suffix = "" if self._from_daemon else " — daemon offline, saving to file"
        return base + suffix

    def _sync_save_rows(self) -> None:
        """Reflect dirty/offline state: row title + warning tint, and the
        buttons only respond when there is something to apply."""
        title = self._save_title()
        for row in self._save_rows:
            row.set_title(title)
            if self._dirty:
                row.add_css_class("warning")
            else:
                row.remove_css_class("warning")
        for btn in (*self._save_btns, *self._discard_btns):
            btn.set_sensitive(self._dirty)

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
            btn = Gtk.Button(
                icon_name="media-record-symbolic",
                css_classes=["flat"],
                tooltip_text="Press a key…",
            )
            btn.connect("clicked", self._start_capture, row)
            row.add_suffix(btn)
        return row

    def _combo(self, section, key, title, values=None, subtitle="", labels=None):
        """values: list of (label, config_value). None -> the config
        registry's enum options for the key (the value doubles as the
        label); `labels` overrides the display text for chosen values."""
        if values is None:
            values = [(v, v) for v in enum_options(section, key) or ()]
        if labels:
            values = [(labels.get(v, label), v) for label, v in values]
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

    def _spin(
        self, section, key, title, step=1, digits=0, subtitle="", lo=None, hi=None
    ) -> Adw.SpinRow:
        """Numeric row. Bounds come from the config registry's validation
        range (single source of truth); explicit lo/hi only override a
        registry-less, UI-only number."""
        bounds = ui_range(section, key)
        if bounds is not None:
            lo = bounds[0] if lo is None else lo
            hi = bounds[1] if hi is None else hi
        lo = 0 if lo is None else lo
        hi = lo if hi is None else hi
        adj = Gtk.Adjustment(value=lo, lower=lo, upper=hi, step_increment=step)
        row = Adw.SpinRow(title=title, subtitle=subtitle, adjustment=adj, digits=digits)
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

    def _load(self) -> None:
        self._loading = True
        self.cfg, self._from_daemon = self.c.get_config()
        if not self._from_daemon and not self._loading_first_done:
            self._loading_first_done = True
            GLib.idle_add(
                self.toast,
                "Daemon offline — file-only mode; changes apply on next daemon start",
                6,
            )
        self._fill_mics()  # NOTE: resets _loading to False at its end
        self._loading = True  # keep the guard up through the final refresh
        lang = str(self.cfg.get("general", {}).get("language", "auto"))
        if lang != "auto" and lang not in LANGUAGES:
            # a saved code outside the common list stays selectable
            self._refill_combo(
                "general",
                "language",
                [("auto (detect)", "auto")]
                + [(c, c) for c in LANGUAGES]
                + [(f"{lang} (saved)", lang)],
            )
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
            0 if idle_s <= 0 else max(1, round(idle_s / 60))
        )
        for mod, tb in self._mod_toggles.items():
            tb.set_active(mod in (self.cfg.get("hotkey", {}).get("modifiers") or []))
        self._load_rules(self.cfg.get("ai", {}).get("per_app_prompts") or [])
        self._load_dictionary(self.cfg.get("processing", {}).get("dictionary") or [])
        self._load_suggestions()
        self._load_mic_priority(
            list(self.cfg.get("recording", {}).get("mic_priority") or [])
        )
        self._load_language_cycle(
            list(self.cfg.get("general", {}).get("language_cycle") or [])
        )
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
                val = (
                    int(row.get_value())
                    if row.get_digits() == 0
                    else round(row.get_value(), 4)
                )
            else:
                continue
            body.setdefault(sec, {})[key] = val
        body.setdefault("hotkey", {})["modifiers"] = [
            m for m, tb in self._mod_toggles.items() if tb.get_active()
        ]
        rules = self._collect_rules()
        if rules is not None:
            body.setdefault("ai", {})["per_app_prompts"] = rules
        body.setdefault("processing", {})["dictionary"] = self._collect_dictionary()
        body.setdefault("recording", {})["mic_priority"] = (
            self._collect_mic_priority()
        )  # empty list is meaningful: removals
        body.setdefault("general", {})["language_cycle"] = (
            self._collect_language_cycle()
        )  # empty list is meaningful here too
        body.setdefault("model", {})["languages"] = (
            self._collect_model_languages()
        )  # empty dict is meaningful too
        # idle unload: UI whole minutes -> config seconds (0 = off)
        body.setdefault("model", {})["idle_unload_s"] = (
            int(self._idle_unload_row.get_value()) * 60
        )
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
        self._toast_overlay.add_toast(Adw.Toast(title=text, timeout=timeout))

    def _on_close_request(self, *_args) -> bool:
        if not self._dirty:
            return False  # allow close
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading="Discard unsaved changes?",
            body="Changes that were not saved will be lost.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("discard", "Discard")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)

        def responded(_dlg, response):
            if response == "discard":
                self._dirty = False
                self.close()

        dlg.connect("response", responded)
        dlg.present()
        return True

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
