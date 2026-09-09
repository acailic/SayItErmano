"""History window — the main window (macOS app's transcript list counterpart).

Live status header, search, Transcripts / Commands pages (v2: command rows
with collapsible output, Copy and confirm-gated Re-run), entry cards with
copy / delete / inline audio replay, clear-all. Reads history directly
from the JSONL (no daemon needed); status/toggle need the daemon and
degrade to a banner when it is down.
"""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime, timedelta

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import history as history_mod
from ..history import format_today
from .client import Client
from .style import load_style


class AudioReplayer(Gtk.Box):
    """Compact play/pause + position for one retained WAV (Gtk.MediaFile,
    with an open-externally fallback when GStreamer can't handle it)."""

    def __init__(self, path):
        super().__init__(spacing=8)
        self.path = path
        self.media = None
        self._tick = 0
        self.play_btn = Gtk.ToggleButton(icon_name="media-playback-start-symbolic")
        self.play_btn.connect("toggled", self._on_toggle)
        self.pos = Gtk.Label(label="0:00", css_classes=["dim-label"])
        self.append(self.play_btn)
        self.append(self.pos)
        try:
            media = Gtk.MediaFile.for_filename(str(path))
            media.connect("notify::playing", self._sync)
            media.connect("notify::timestamp", lambda *_: self._update_pos())
            media.connect("notify::error", lambda *_: self._fallback())
            self.media = media
        except Exception:
            self._fallback()

    def _fallback(self) -> None:
        """GStreamer missing/broken: offer opening the file instead."""
        if self.media is None and self.get_first_child() is not self.play_btn:
            return  # already fallen back
        self.media = None
        self.play_btn.set_visible(False)
        self.pos.set_visible(False)
        btn = Gtk.Button(label="Open audio", icon_name="folder-open-symbolic",
                         css_classes=["flat"])
        btn.connect("clicked", lambda *_: subprocess.Popen(
            ["xdg-open", str(self.path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.append(btn)

    def _on_toggle(self, btn) -> None:
        if self.media is None:
            return
        if btn.get_active():
            self.media.play()
            if not self._tick:
                self._tick = GLib.timeout_add(300, self._update_pos)
        else:
            self.media.pause()

    def _sync(self, *_) -> None:
        playing = bool(self.media and self.media.get_playing())
        if self.play_btn.get_active() != playing:
            self.play_btn.set_active(playing)
        self.play_btn.set_icon_name(
            "media-playback-pause-symbolic" if playing
            else "media-playback-start-symbolic")
        if not playing and self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _update_pos(self) -> bool:
        if self.media is None:
            return False
        pos = self.media.get_timestamp() / 1e9
        dur = self.media.get_duration() / 1e9
        if dur > 0:
            self.pos.set_text(f"{int(pos // 60)}:{int(pos % 60):02d} / "
                              f"{int(dur // 60)}:{int(dur % 60):02d}")
        return True


def prettify_app(app: str) -> str:
    """"org.gnome.TextEditor" -> "Text Editor", "google-chrome" -> "Google
    Chrome": history stores raw app IDs, rows show a friendly name."""
    take = app[:-8] if app.endswith(".desktop") else app
    take = take.rsplit(".", 1)[-1]  # drop the reverse-DNS prefix
    take = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", take)  # camelCase split
    words = [w for w in re.split(r"[-_ ]+", take) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or app


def _meta_label(text: str = "", css: list[str] | None = None,
                tooltip: str | None = None) -> Gtk.Label:
    """One metadata segment: caption-sized, dimmed by default."""
    lbl = Gtk.Label(label=text, single_line_mode=True, ellipsize=True,
                    css_classes=css or ["caption", "dim-label"])
    if tooltip:
        lbl.set_tooltip_text(tooltip)
    return lbl


def _sep() -> Gtk.Label:
    return Gtk.Label(label="·", css_classes=["caption", "dim-label"],
                     name="meta-separator")


class HistoryEntryRow(Gtk.ListBoxRow):
    """One dictation: meta line, text, actions, optional audio replay,
    plus inline repair (edit + re-insert, research §4: correction must be
    one step away) and honest confidence dots (research §5)."""

    CONFIDENCE_DOTS = {2: "●●●", 1: "●●○", 0: "●○○"}
    CONFIDENCE_WORD = {2: "high", 1: "mixed", 0: "low"}
    MODE_ICONS = {"dictate": "audio-input-microphone-symbolic",
                  "rewrite": "document-edit-symbolic",
                  "command": "utilities-terminal-symbolic"}

    def __init__(self, entry, on_delete, on_copy, on_insert=None,
                 on_edit=None):
        super().__init__(activatable=False, selectable=False,
                         css_classes=["card"])
        self.entry = entry
        self.editor: Gtk.Box | None = None
        self.on_edit = on_edit
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=10, margin_bottom=10,
                      margin_start=14, margin_end=10)
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ts = entry.get("ts") or 0
        mode = str(entry.get("mode") or "dictate")
        if mode in self.MODE_ICONS:  # glanceable mode glyph, not color-only
            meta.append(Gtk.Image(icon_name=self.MODE_ICONS[mode],
                                  tooltip_text=mode.capitalize(),
                                  css_classes=["dim-label"]))
        meta.append(_meta_label(
            datetime.fromtimestamp(ts).strftime("%a %d %b, %H:%M")))
        if entry.get("duration_s"):
            meta.append(_sep())
            meta.append(_meta_label(f"{float(entry['duration_s']):.1f} s"))
        if entry.get("app"):
            meta.append(_sep())
            meta.append(_meta_label(prettify_app(str(entry["app"])),
                                    tooltip=str(entry["app"])))
        meta.append(Gtk.Box(hexpand=True))  # spacer
        conf = entry.get("confidence")
        if conf in self.CONFIDENCE_DOTS:
            meta.append(_meta_label(
                self.CONFIDENCE_DOTS[conf],
                tooltip=f"Recognition confidence: "
                        f"{self.CONFIDENCE_WORD[conf]}"))
        if entry.get("mode") and entry.get("mode") != "dictate":
            meta.append(Gtk.Label(label=mode.capitalize(),
                                  css_classes=["tag", "outline"],
                                  tooltip_text="Dictation mode"))
        if entry.get("ai"):
            meta.append(Gtk.Label(label="AI polished",
                                  css_classes=["tag", "accent"],
                                  tooltip_text="Text after AI polish"))
        if on_insert is not None:
            ins_btn = Gtk.Button(icon_name="edit-paste-symbolic",
                                 css_classes=["flat"],
                                 tooltip_text="Insert at cursor")
            ins_btn.connect("clicked", on_insert, self)
            meta.append(ins_btn)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic",
                              css_classes=["flat"],
                              tooltip_text="Copy text")
        copy_btn.connect("clicked", on_copy, self)
        meta.append(copy_btn)
        edit_btn = Gtk.Button(icon_name="document-edit-symbolic",
                              css_classes=["flat"],
                              tooltip_text="Edit text")
        edit_btn.connect("clicked", self._start_edit)
        meta.append(edit_btn)
        del_btn = Gtk.Button(icon_name="user-trash-symbolic",
                             css_classes=["flat", "destructive-action"],
                             tooltip_text="Delete entry")
        del_btn.connect("clicked", on_delete, self)
        meta.append(del_btn)
        box.append(meta)

        self.text_lbl = Gtk.Label(
            label=str(entry.get("text") or entry.get("raw") or ""),
            wrap=True, xalign=0.0, hexpand=True, css_classes=["body"])
        box.append(self.text_lbl)

        if entry.get("audio"):
            path = entry.get("_audio_path")
            if path:
                box.append(AudioReplayer(path))
        self.set_child(box)

    # -- inline repair ---------------------------------------------------------

    def _start_edit(self, _btn) -> None:
        if self.editor is not None:
            return
        parent = self.get_child()
        self.text_lbl.set_visible(False)
        self.editor = self._build_editor()
        parent.append(self.editor)
        parent.reorder_child_after(self.editor, self.text_lbl)

    def _build_editor(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.set_size_request(-1, 72)
        view.get_buffer().set_text(
            str(self.entry.get("text") or self.entry.get("raw") or ""))
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                       halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel", css_classes=["flat"])
        save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        btns.append(cancel)
        btns.append(save)
        box.append(view)
        box.append(btns)
        cancel.connect("clicked", lambda *_: self._end_edit(False, view))
        save.connect("clicked", lambda *_: self._end_edit(True, view))
        return box

    def _end_edit(self, apply: bool, view: Gtk.TextView) -> None:
        if apply and self.on_edit is not None:
            buf = view.get_buffer()
            new = buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                               False).strip()
            if new and new != str(self.entry.get("text") or ""):
                if not self.on_edit(self, new):
                    return  # persist failed: keep the editor + the user's text
        if self.editor is not None:
            self.get_child().remove(self.editor)
            self.editor = None
        self.text_lbl.set_text(
            str(self.entry.get("text") or self.entry.get("raw") or ""))
        self.text_lbl.set_visible(True)


def date_header_label(ts: float, now: datetime | None = None) -> str:
    """"Today" / "Yesterday" / "%a %d %b" bucket label for a timestamp."""
    day = datetime.fromtimestamp(ts).date()
    now = now or datetime.now()
    if day == now.date():
        return "Today"
    if day == (now - timedelta(days=1)).date():
        return "Yesterday"
    return day.strftime("%a %d %b")


class CommandRow(Gtk.ListBoxRow):
    """One executed command (mode=command history row, v2): monospace
    `$ command`, purpose, exit/duration chips, collapsible output, Copy
    and Re-run (the re-run only re-posts a proposal - the daemon confirms
    via the hotkey, never silently)."""

    def __init__(self, entry, on_copy, on_rerun):
        super().__init__(activatable=False, selectable=False,
                         css_classes=["card"])
        self.entry = entry
        self.command = str(entry.get("command")
                           or str(entry.get("text") or "").lstrip("$ "))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=10, margin_bottom=10,
                      margin_start=14, margin_end=10)

        cmd_lbl = Gtk.Label(label=f"$ {self.command}", wrap=True,
                            xalign=0.0, hexpand=True, selectable=True,
                            css_classes=["monospace", "body"])
        box.append(cmd_lbl)

        purpose = entry.get("purpose")
        if purpose:
            box.append(Gtk.Label(label=str(purpose), wrap=True, xalign=0.0,
                                 css_classes=["caption", "dim-label"]))

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        ts = entry.get("ts") or 0
        meta.append(_meta_label(
            datetime.fromtimestamp(ts).strftime("%a %d %b, %H:%M")))
        success = bool(entry.get("success"))
        exit_code = entry.get("exit_code")
        if exit_code is not None:
            meta.append(Gtk.Label(
                label=("\u2713 " if success else "\u2717 ")
                      + str(int(exit_code)),
                css_classes=["success" if success else "error", "caption"]))
        if entry.get("duration_ms"):
            meta.append(_sep())
            meta.append(_meta_label(f"{float(entry['duration_ms']):.0f} ms"))
        if entry.get("destructive"):
            meta.append(Gtk.Label(label="\u26a0 destructive",
                                  css_classes=["warning", "caption"]))
        meta.append(Gtk.Box(hexpand=True))  # spacer

        output = str(entry.get("output") or "")
        self.output_revealer = None
        if output.strip():
            self.output_btn = Gtk.ToggleButton(label="Output",
                                               css_classes=["flat"])
            self.output_btn.connect("toggled", self._on_toggle_output)
            meta.append(self.output_btn)
            out_lbl = Gtk.Label(label=output, wrap=True, xalign=0.0,
                                selectable=True,
                                css_classes=["monospace", "caption",
                                             "dim-label"])
            self.output_revealer = Gtk.Revealer(
                child=out_lbl, reveal_child=False,
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic",
                              css_classes=["flat"], tooltip_text="Copy command")
        copy_btn.connect("clicked", on_copy, self)
        meta.append(copy_btn)
        self.copy_btn = copy_btn
        rerun_btn = Gtk.Button(icon_name="media-playback-start-symbolic",
                               label="Re-run", css_classes=["flat"],
                               tooltip_text="Propose re-run (confirm with the "
                                            "command hotkey)")
        rerun_btn.connect("clicked", on_rerun, self)
        meta.append(rerun_btn)
        self.rerun_btn = rerun_btn
        box.append(meta)
        if self.output_revealer is not None:
            box.append(self.output_revealer)
        self.set_child(box)

    def _on_toggle_output(self, btn) -> None:
        if self.output_revealer is not None:
            self.output_revealer.set_reveal_child(btn.get_active())


class HistoryWindow(Adw.ApplicationWindow):
    def __init__(self, application=None, client=None):
        load_style()
        super().__init__(application=application, title="SayItErmano",
                         default_width=760, default_height=640)
        self.c = client or Client()
        self._entries: list[dict] = []
        self._query = ""
        self._search_debounce = 0

        # -- header ---------------------------------------------------------
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title="SayItErmano", subtitle="idle")
        header.set_title_widget(self.title_widget)

        self.mic_btn = Gtk.Button(icon_name="audio-input-microphone-symbolic",
                                  css_classes=["suggested-action"],
                                  tooltip_text="Toggle dictation")
        self.mic_btn.connect("clicked", self._on_toggle)
        header.pack_start(self.mic_btn)

        menu = Gio.Menu()
        export_section = Gio.Menu()
        export_section.append("Export as ZIP…", "win.hist.export")
        export_section.append("Export as Text…", "win.hist.export-text")
        danger_section = Gio.Menu()
        danger_section.append("Pause saving", "win.hist.pause")
        danger_section.append("Clear All…", "win.hist.clear")
        menu.append_section(None, export_section)
        menu.append_section(None, danger_section)
        self.menu_model = menu
        self._danger_section = danger_section
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic",
                                       menu_model=menu, tooltip_text="Menu"))
        settings_btn = Gtk.Button(icon_name="preferences-system-symbolic",
                                  tooltip_text="Settings")
        settings_btn.connect("clicked", self._open_settings)
        header.pack_end(settings_btn)

        # -- layout ---------------------------------------------------------
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay = Adw.ToastOverlay(child=vbox)
        self.set_content(self.toast_overlay)
        vbox.append(header)

        self.down_banner = Adw.Banner(
            title="Daemon not running — history works, dictation does not",
            button_label="Retry", revealed=False)
        self.down_banner.connect("button-clicked", lambda *_: self.refresh())
        vbox.append(self.down_banner)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                         margin_top=8, margin_bottom=4,
                         margin_start=12, margin_end=12)
        self.state_dot = Gtk.Image(icon_name="object-select-symbolic",
                                   css_classes=["success"])
        # state word stays plain-size: it doubles as the at-a-glance status
        self.state_lbl = Gtk.Label(label="idle", css_classes=["caption"],
                                   single_line_mode=True)
        self.backend_lbl = _meta_label()
        self.gpu_lbl = _meta_label()
        self.model_lbl = _meta_label()
        self.warmup_spinner = Gtk.Spinner()
        self.warmup_lbl = _meta_label()
        self.today_lbl = _meta_label()
        # update check-and-assist surface (fluidvoice/update.py): hidden
        # until the daemon's status payload reports update_available
        self.update_lbl = Gtk.Label(css_classes=["caption", "dim-label"],
                                    visible=False, ellipsize=True)
        for w in (self.state_dot, self.state_lbl, _sep(), self.backend_lbl,
                  self.gpu_lbl, self.model_lbl,
                  self.warmup_spinner, self.warmup_lbl,
                  Gtk.Box(hexpand=True),  # spacer
                  self.today_lbl, self.update_lbl):
            status.append(w)
        vbox.append(status)

        self.search = Gtk.SearchEntry(placeholder_text="Search transcripts, commands, apps…",
                                      margin_start=12, margin_end=12,
                                      margin_bottom=6)
        self.search.connect("changed", self._on_search_changed)
        vbox.append(self.search)

        # -- Transcripts / Commands pages (v2: command rows get their own
        #    view with output + Copy + confirm-gated Re-run) ------------
        self.view_stack = Adw.ViewStack(vexpand=True)
        self.switcher = Adw.ViewSwitcher(stack=self.view_stack,
                                        policy=Adw.ViewSwitcherPolicy.WIDE,
                                        margin_start=12, margin_end=12)
        vbox.append(self.switcher)

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.listbox = Gtk.ListBox(css_classes=["boxed-list-separate"],
                                   margin_start=12, margin_end=12,
                                   margin_top=2, margin_bottom=12)
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.set_placeholder(Adw.StatusPage(
            title="No dictations yet",
            description="Press your hotkey and speak — transcripts land here.",
            icon_name="sayit-ermano",
            vexpand=True))
        scroll.set_child(self.listbox)
        self.view_stack.add_titled(scroll, "transcripts", "Transcripts")

        cmd_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.cmd_listbox = Gtk.ListBox(css_classes=["boxed-list-separate"],
                                       margin_start=12, margin_end=12,
                                       margin_top=2, margin_bottom=12)
        self.cmd_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.cmd_listbox.set_placeholder(Adw.StatusPage(
            title="No commands yet",
            description="Voice command runs land here — re-run any of them "
                        "(always confirmed) or copy the command line.",
            icon_name="utilities-terminal-symbolic",
            vexpand=True))
        cmd_scroll.set_child(self.cmd_listbox)
        self.view_stack.add_titled(cmd_scroll, "commands", "Commands")

        # -- Stats page (B6, upstream StatsView parity) ---------------------
        stats_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        stats_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                            margin_top=14, margin_bottom=14,
                            margin_start=12, margin_end=12)
        stats_scroll.set_child(stats_box)

        self.streak_lbl = Gtk.Label(css_classes=["title-3"], halign=Gtk.Align.START)
        stats_box.append(self.streak_lbl)

        today_g = Adw.PreferencesGroup(title="Today")
        self._today_vals: dict[str, Gtk.Label] = {}
        for key, title in (("dictations", "Dictations"),
                           ("minutes", "Minutes dictated"),
                           ("words", "Words")):
            row = Adw.ActionRow(title=title)
            val = Gtk.Label(css_classes=["dim-label", "numeric"])
            row.add_suffix(val)
            self._today_vals[key] = val
            today_g.add(row)
        stats_box.append(today_g)

        all_g = Adw.PreferencesGroup(title="All time")
        self._all_vals: dict[str, Gtk.Label] = {}
        for key, title in (("dictations", "Dictations"),
                           ("words", "Words"),
                           ("minutes", "Minutes dictated"),
                           ("avg", "Avg per dictation"),
                           ("best", "Best streak (days)"),
                           ("saved", "Time saved (dictating vs typing)")):
            row = Adw.ActionRow(title=title)
            val = Gtk.Label(css_classes=["dim-label", "numeric"])
            row.add_suffix(val)
            self._all_vals[key] = val
            all_g.add(row)
        stats_box.append(all_g)

        chart_g = Adw.PreferencesGroup(
            title="Activity",
            description="dictations per day")
        chart_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._span_btns: dict[int, Gtk.ToggleButton] = {}
        for span in (7, 30):
            tb = Gtk.ToggleButton(label=f"{span} days",
                                  css_classes=["flat"], valign=Gtk.Align.CENTER)
            tb.set_active(span == 7)
            tb.connect("toggled", lambda b, s=span: self._on_span(b, s))
            self._span_btns[span] = tb
            chart_box.append(tb)
        chart_g.header_suffix = chart_box
        self.chart = Gtk.DrawingArea(vexpand=True, height_request=160)
        self.chart.set_draw_func(self._draw_activity)
        chart_g.add(self.chart)
        stats_box.append(chart_g)

        self.view_stack.add_titled(stats_scroll, "stats", "Stats")
        self.view_stack.connect("notify::visible-child",
                                lambda *_: self._update_count())
        vbox.append(self.view_stack)

        self.count_lbl = Gtk.Label(css_classes=["caption", "dim-label"],
                                   margin_top=6, margin_bottom=10,
                                   margin_start=12, margin_end=12)
        vbox.append(self.count_lbl)

        self.install_action("hist.clear", None, self._on_clear_all)
        self.install_action("hist.export", None, self._on_export)
        self.install_action("hist.export-text", None, self._on_export_text)
        self.install_action("hist.pause", None, self._on_pause_toggle)
        self.install_action("hist.focus-search", None,
                            lambda *_: self.search.grab_focus())
        sc = Gtk.ShortcutController()
        sc.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<primary>f"),
            action=Gtk.NamedAction.new("hist.focus-search")))
        self.add_controller(sc)
        self._exporting = False
        self._export_dlg = None
        self._load_history()
        self.refresh()
        GLib.timeout_add_seconds(2, self._poll)

    # -- data -----------------------------------------------------------------

    _stats_span = 7

    def _on_span(self, btn: Gtk.ToggleButton, span: int) -> None:
        if btn.get_active():
            self._stats_span = span
            for other, b in self._span_btns.items():
                if other != span:
                    b.set_active(False)
            self.chart.queue_draw()

    def _update_stats(self) -> None:
        """Stats page refresh - computed over the WHOLE history, ignoring
        the search filter (which only applies to the Transcripts list)."""
        try:
            entries = self.c.history(q="", limit=5000)
        except Exception:
            entries = self._entries
        s = history_mod.usage_stats(entries)
        self._stats = s
        self.streak_lbl.set_label(
            f"{s['streak']}-day streak" + (" — keep going!" if s["streak"] else ""))
        t = s["today"]
        self._today_vals["dictations"].set_label(str(t["dictations"]))
        self._today_vals["minutes"].set_label(f"{int(t['seconds']) // 60} min")
        self._today_vals["words"].set_label(str(t["words"]))
        self._all_vals["dictations"].set_label(str(s["dictations"]))
        self._all_vals["words"].set_label(str(s["words"]))
        self._all_vals["minutes"].set_label(f"{int(s['seconds']) // 60} min")
        avg = s["avg_seconds"]
        self._all_vals["avg"].set_label(
            f"{int(avg) // 60}:{int(avg) % 60:02d}" if avg else "—")
        self._all_vals["best"].set_label(str(s["best_streak"]))
        self._all_vals["saved"].set_label(f"~{s['minutes_saved']:.0f} min")
        self.chart.queue_draw()

    def _draw_activity(self, _area, cr, width: int, height: int) -> None:
        """One bar per day for the selected span; empty days draw at the
        baseline so gaps stay visible (upstream StatsView activity chart).
        Bars use the theme accent when it resolves, so the chart follows
        GNOME accent colors instead of a hardcoded blue."""
        import time as _time
        s = getattr(self, "_stats", None)
        if not s:
            return
        by_day = s["by_day"]
        span = self._stats_span
        now = _time.time()
        keys = [_time.strftime("%Y-%m-%d", _time.localtime(now - i * 86400))
                for i in range(span - 1, -1, -1)]
        counts = [by_day.get(k, {}).get("dictations", 0) for k in keys]
        labels = [k[5:] for k in keys]
        peak = max(counts, default=0) or 1
        n = len(keys)
        gap = 6.0
        bar_w = max(3.0, (width - gap * (n + 1)) / n)
        top, bottom = 18.0, height - 22.0
        ok, accent = self.chart.lookup_color("accent_bg_color")
        if ok:
            bar_rgba = accent
        else:
            bar_rgba = self._fallback_accent
        # baseline hairline under the bars
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
        cr.set_line_width(1.0)
        cr.move_to(0, bottom + 0.5)
        cr.line_to(width, bottom + 0.5)
        cr.stroke()
        for i, c in enumerate(counts):
            x = gap + i * (bar_w + gap)
            h = (bottom - top) * (c / peak) if c else 2.0
            if c:
                cr.set_source_rgba(bar_rgba.red, bar_rgba.green,
                                   bar_rgba.blue, 0.95)
            else:
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.25)
            cr.rounded_rectangle(x, bottom - h, bar_w, h, 3.0)
            cr.fill()
            if span == 7 or i % 5 == 0:
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
                cr.select_font_face("Sans", 0, 0)
                cr.set_font_size(9.0)
                cr.move_to(x, height - 8)
                cr.show_text(labels[i])
        if max(counts, default=0):
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(9.0)
            cr.move_to(gap, 12)
            cr.show_text(f"peak {peak}/day")

    # calm blue, used only when accent_bg_color fails to resolve
    _fallback_accent = Gdk.RGBA()
    _fallback_accent.parse("rgba(61,104,166,0.95)")

    def _on_search_changed(self, entry) -> None:
        self._query = entry.get_text().strip()
        if self._search_debounce:
            GLib.source_remove(self._search_debounce)
        self._search_debounce = GLib.timeout_add(250, self._debounced_load)

    def _debounced_load(self) -> bool:
        self._search_debounce = 0
        self._load_history()
        return False

    def _load_history(self) -> None:
        try:
            self._entries = self.c.history(q=self._query, limit=200)
        except Exception:
            self._entries = []
        self._render()
        self._update_stats()

    def _render(self) -> None:
        row = self.listbox.get_first_child()
        while row is not None:
            nxt = row.get_next_sibling()
            self.listbox.remove(row)
            row = nxt
        last_day = None
        for e in self._entries:
            ts = e.get("ts") or 0
            day = datetime.fromtimestamp(ts).date()
            if day != last_day:  # date headers: one glance = recency bucket
                last_day = day
                self.listbox.append(self._header_row(date_header_label(ts)))
            if e.get("audio"):
                e["_audio_path"] = history_mod.audio_path_for(e.get("ts", 0))
            self.listbox.append(
                HistoryEntryRow(e, self._on_delete_row, self._on_copy_row,
                                self._on_insert_row, self._on_edit_row))
        self._render_commands()
        self._update_count()
        self._update_today()

    def _render_commands(self) -> None:
        """The Commands page: command-mode rows from the SAME loaded set
        (filtered client-side - history.search has no mode filter and the
        v2 request forbids schema/query changes)."""
        row = self.cmd_listbox.get_first_child()
        while row is not None:
            nxt = row.get_next_sibling()
            self.cmd_listbox.remove(row)
            row = nxt
        cmds = [e for e in self._entries if e.get("mode") == "command"]
        for e in cmds:
            self.cmd_listbox.append(
                CommandRow(e, self._on_copy_command, self._on_rerun_command))

    def _update_count(self) -> None:
        on_commands = (self.view_stack.get_visible_child()
                       is not None and self.view_stack.get_child_by_name(
                           "commands") is self.view_stack.get_visible_child())
        if on_commands:
            n = sum(1 for e in self._entries if e.get("mode") == "command")
            unit = "command" if n == 1 else "commands"
        else:
            n = len(self._entries)
            unit = "ent" + ("ry" if n == 1 else "ries")
        self.count_lbl.set_text(
            f"{n} {unit}"
            + (" (showing latest 200)" if n >= 200 else ""))

    @staticmethod
    def _header_row(label: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow(activatable=False, selectable=False)
        row.set_child(Gtk.Label(label=label, xalign=0.0,
                                css_classes=["section-label", "dim-label"],
                                margin_top=12, margin_bottom=4,
                                margin_start=4))
        return row

    def _update_today(self) -> None:
        try:
            st = self.c.today_stats()
        except Exception:
            return
        self.today_lbl.set_text("today: " + format_today(st))

    # -- actions ----------------------------------------------------------------

    def _on_copy_row(self, _btn, row) -> None:
        text = str(row.entry.get("text") or row.entry.get("raw") or "")
        try:
            Gdk.Display.get_default().get_clipboard().set_text(text)
        except (AttributeError, TypeError):
            cb = Gdk.Display.get_default().get_clipboard()
            cb.set(Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
        self._toast("Copied")

    def _on_copy_command(self, _btn, row) -> None:
        text = row.command
        try:
            Gdk.Display.get_default().get_clipboard().set_text(text)
        except (AttributeError, TypeError):
            cb = Gdk.Display.get_default().get_clipboard()
            cb.set(Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
        self._toast("Command copied")

    def _on_rerun_command(self, _btn, row) -> None:
        """Re-run ONLY re-posts the stored command as a pending proposal
        on the daemon - execution still needs the hotkey confirm (never
        silent, strong-confirm included for destructive commands)."""
        try:
            self.c.command_rerun(row.command, row.entry.get("purpose"))
            self._toast("Re-run proposed — confirm with the command hotkey")
        except Exception as e:  # noqa: BLE001 - daemon down/busy -> toast
            self._toast(f"Re-run failed: {e}")

    def _on_insert_row(self, _btn, row) -> None:
        text = str(row.entry.get("text") or "")
        if not text:
            return
        try:
            self.c.insert_text(text)
            self._toast("Inserted at cursor")
        except Exception as e:  # noqa: BLE001 - daemon down/busy -> toast
            self._toast(f"Insert failed: {e}")

    def _on_edit_row(self, row, new_text: str) -> bool:
        try:
            saved = self.c.history_update_text(row.entry.get("ts", 0),
                                               new_text)
        except Exception:  # noqa: BLE001 - unreadable history -> toast
            saved = False
        if saved:
            row.entry["text"] = new_text
            self._toast("Saved")
        else:
            self._toast("Save failed")
        return bool(saved)

    def _on_delete_row(self, _btn, row) -> None:
        def confirmed(dialog, response):
            if response == "delete":
                self.c.history_delete(row.entry.get("ts", 0))
                self._load_history()
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True, heading="Delete this entry?",
            body="The transcription"
                 + (" and its audio" if row.entry.get("audio") else "")
                 + " will be removed.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", confirmed)
        dlg.present()

    def _on_clear_all(self, *_args) -> None:
        def confirmed(dialog, response):
            if response == "clear":
                removed = self.c.history_clear()
                self._load_history()
                self._toast(f"Removed {removed} entries")
        dlg = Adw.MessageDialog(
            transient_for=self, modal=True, heading="Clear all history?",
            body="Every saved transcription and any retained audio will be "
                 "deleted. This cannot be undone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("clear", "Clear All")
        dlg.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", confirmed)
        dlg.present()

    def _on_toggle(self, _btn) -> None:
        try:
            self.c.toggle()
        except Exception as e:
            self._toast(str(e))
        GLib.timeout_add(150, self.refresh)

    # -- export ------------------------------------------------------------------

    def _on_export(self, *_args) -> None:
        dlg = Gtk.FileChooserNative.new(
            "Export history", self, Gtk.FileChooserAction.SAVE,
            "_Export", "_Cancel")
        dlg.set_current_name(
            f"sayitermano-history-{time.strftime('%Y%m%d-%H%M%S')}.zip")
        dlg.connect("response", self._on_export_response)
        self._export_dlg = dlg  # keep alive while it runs its own loop
        dlg.show()

    def _on_export_response(self, dlg, response) -> None:
        self._export_dlg = None
        if response != Gtk.ResponseType.ACCEPT:
            return
        f = dlg.get_file()
        path = f.get_path() if f is not None else None
        if path:
            self._export_to(path)

    def _export_to(self, path: str) -> None:
        """Enter the busy state, let one frame paint, then write the zip
        from an idle callback (everything stays on the main thread)."""
        self._exporting = True
        self.action_set_enabled("hist.export", False)
        self._toast("Exporting…")
        GLib.idle_add(self._export_now, path)

    def _export_now(self, path: str) -> bool:
        try:
            n, notes = self.c.export_zip(path)
            msg = f"Exported {n} entries"
            if notes:
                msg += f", {len(notes)} audio files skipped"
            self._toast(msg)
        except Exception as e:  # noqa: BLE001 - surfaced as a toast
            self._toast(f"export failed: {e}")
        finally:
            self._exporting = False
            self.action_set_enabled("hist.export", True)
        return False  # GLib.SOURCE_REMOVE - stop the idle source

    # -- export as text (macOS "Save as Text" parity; plain file, offline) --

    def _on_export_text(self, *_args) -> None:
        dlg = Gtk.FileChooserNative.new(
            "Export history as text", self, Gtk.FileChooserAction.SAVE,
            "_Export", "_Cancel")
        dlg.set_current_name(
            f"sayitermano-history-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        dlg.connect("response", self._on_export_text_response)
        self._export_dlg = dlg  # keep alive while it runs its own loop
        dlg.show()

    def _on_export_text_response(self, dlg, response) -> None:
        self._export_dlg = None
        if response != Gtk.ResponseType.ACCEPT:
            return
        f = dlg.get_file()
        path = f.get_path() if f is not None else None
        if not path:
            return
        try:
            entries = self.c.history(q="", limit=5000)
            lines = []
            for e in entries:
                ts = datetime.fromtimestamp(e.get("ts") or 0)
                app = f" ({e['app']})" if e.get("app") else ""
                text = str(e.get("text") or e.get("raw") or "")
                lines.append(f"[{ts.strftime('%a %d %b %Y, %H:%M')}]{app}\n"
                             f"{text}\n")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            self._toast(f"Exported {len(entries)} entries")
        except Exception as e:  # noqa: BLE001 - surfaced as a toast
            self._toast(f"export failed: {e}")

    def _open_settings(self, _btn) -> None:
        app = self.get_application()
        if app is not None:
            app.show_settings()

    # -- pause saving (macOS "Pause History" parity) -----------------------------

    def _on_pause_toggle(self, *_args) -> None:
        if getattr(self, "_history_paused", None) is not None:
            paused = self._history_paused  # last known state (own toggle)
        else:
            try:
                cfg, _ = self.c.get_config()
                paused = not bool(cfg.get("history", {}).get("save", True))
            except Exception:  # noqa: BLE001 - unreadable -> assume saving
                paused = False
        try:
            resume = paused  # toggling from paused means resuming
            self.c.set_config({"history": {"save": resume}})
            self._history_paused = not resume
            self._sync_pause_label(not resume)
            self._toast("History saving resumed" if resume else
                        "History paused — new dictations are not saved")
        except Exception as e:  # noqa: BLE001 - daemon down -> toast
            self._toast(f"Could not toggle (daemon needed): {e}")

    def _sync_pause_label(self, paused: bool) -> None:
        self._danger_section.remove_all()
        self._danger_section.append(
            "Resume saving" if paused else "Pause saving", "win.hist.pause")
        self._danger_section.append("Clear All…", "win.hist.clear")

    def _toast(self, text: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text))

    # -- status polling ---------------------------------------------------------

    def refresh(self) -> bool:
        self._apply_status(self.c.status())
        self._update_today()  # a fresh dictation may have just landed
        return False

    def _poll(self) -> bool:
        GLib.idle_add(self._apply_status, self.c.status())
        return True  # keep polling

    def _apply_status(self, st: dict | None) -> None:
        if st is None:
            self.down_banner.set_revealed(True)
            self.mic_btn.set_sensitive(False)
            self.state_lbl.set_text("daemon offline")
            self.state_dot.set_from_icon_name("dialog-warning-symbolic")
            self.state_dot.set_css_classes(["warning"])
            self.update_lbl.set_visible(False)
            return
        self.down_banner.set_revealed(False)
        self.mic_btn.set_sensitive(True)
        recording = bool(st.get("recording"))
        busy = bool(st.get("busy"))
        if recording:
            self.state_lbl.set_text("recording")
            self.state_dot.set_css_classes(["error"])
            self.state_dot.set_from_icon_name("media-record-symbolic")
            self.mic_btn.set_icon_name("media-playback-stop-symbolic")
            self.mic_btn.add_css_class("destructive-action")
            self.mic_btn.remove_css_class("suggested-action")
        else:
            self.state_lbl.set_text("processing…" if busy else "idle")
            self.state_dot.set_css_classes(["warning"] if busy else ["success"])
            self.state_dot.set_from_icon_name(
                "emblem-synchronizing-symbolic" if busy else "object-select-symbolic")
            self.mic_btn.set_icon_name("audio-input-microphone-symbolic")
            self.mic_btn.add_css_class("suggested-action")
            self.mic_btn.remove_css_class("destructive-action")
        self.backend_lbl.set_text(f"backend {st.get('backend') or '—'}")
        self.gpu_lbl.set_text("GPU yes" if st.get("cuda") else "GPU no")
        model = st.get("active_model") or self._active_model_from_config()
        self.model_lbl.set_text(f"model {model or '—'}")
        warm = st.get("warmup") or {}
        if warm.get("running"):
            self.warmup_spinner.set_visible(True)
            self.warmup_spinner.start()
            self.warmup_lbl.set_text(f"loading {warm.get('model') or ''}…")
        else:
            self.warmup_spinner.stop()
            self.warmup_spinner.set_visible(False)
            self.warmup_lbl.set_text(
                f"model error: {warm['error']}" if warm.get("error") else "")
        upd = st.get("update_available")
        if upd:
            # text + tooltip only; the upgrade command comes from the status
            # payload (no network in the UI)
            self.update_lbl.set_text(f"update available: v{upd}")
            self.update_lbl.set_css_classes(["dim-label", "warning"])
            self.update_lbl.set_tooltip_text(
                (st.get("update") or {}).get("upgrade_command")
                or "run 'sayit-ermano update' for the upgrade command")
            self.update_lbl.set_visible(True)
        else:
            self.update_lbl.set_visible(False)
        self.title_widget.set_subtitle(self.state_lbl.get_text())

    @staticmethod
    def _active_model_from_config() -> str:
        from .. import backends
        from ..config import load_config
        name = str(load_config().get("model", {}).get("name", "auto"))
        if name in ("", "auto"):
            return backends.resolve_model_name(name)
        return backends.ALIASES.get(name.lower(), name.lower())
