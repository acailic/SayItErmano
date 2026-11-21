"""Wayland page: session capabilities, DE shortcut assist, typing tools.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gtk

from ...i18n import _


class WaylandPageMixin:
    _DE_SHORTCUT_PANELS = {
        "gnome": ["gnome-control-center", "keyboard"],
        "kde": ["systemsettings", "kcm_keys"],
        "plasma": ["systemsettings", "kcm_keys"],
    }

    def _build_wayland(self) -> None:
        from ... import session as session_mod

        self.wayland_page = Adw.PreferencesPage(
            name="wayland", icon_name="fluidvoice-general-symbolic",
            title=_("Wayland")
        )

        caps = Adw.PreferencesGroup(
            title=_("Session and capabilities"),
            description=_("What the daemon resolved for this session "
                          "(informational; refreshed on open)"),
        )
        self._wayland_cap_rows: dict[str, Adw.ActionRow] = {}
        for cap, label in (
            ("session", _("Session")),
            ("hotkey", _("Hotkey")),
            ("insertion", _("Insertion")),
            ("clipboard", _("Clipboard")),
            ("overlay", _("Live preview")),
            ("app-hint", _("App hints")),
        ):
            row = Adw.ActionRow(title=label, subtitle="\u2014")
            caps.add(row)
            self._wayland_cap_rows[cap] = row
        self.wayland_page.add(caps)

        self.wayland_bind_group = Adw.PreferencesGroup(
            title=_("Bind your hotkey"),
            description=_("No global key grabs exist on Wayland: bind a "
                          "custom command shortcut in your desktop "
                          "environment"),
        )
        self.wayland_script_row = Adw.ActionRow(
            title=_("Command to bind"), subtitle="\u2014"
        )
        copy_btn = Gtk.Button(
            icon_name="edit-copy-symbolic", css_classes=["flat"],
            tooltip_text=_("Copy")
        )
        copy_btn.connect("clicked", self._copy_toggle_script)
        self.wayland_script_row.add_suffix(copy_btn)
        self.wayland_bind_group.add(self.wayland_script_row)
        self._wayland_instruction_rows: list[Adw.ActionRow] = []
        self.wayland_open_row = Adw.ActionRow(
            title=_("Shortcut settings"),
            subtitle=_("Open your desktop environment's panel")
        )
        open_btn = Gtk.Button(label=_("Open\u2026"),
                              css_classes=["suggested-action"])
        open_btn.connect("clicked", self._open_de_shortcut_settings)
        self.wayland_open_row.add_suffix(open_btn)
        self.wayland_bind_group.add(self.wayland_open_row)
        self.wayland_page.add(self.wayland_bind_group)

        tools = Adw.PreferencesGroup(
            title=_("Insertion and push-to-talk"),
            description=_("Wayland typing tool and optional physical "
                          "push-to-talk"),
        )
        tools.add(
            self._combo(
                "insertion",
                "wayland_tool",
                _("Typing tool"),
                subtitle=_("auto: wtype (wlroots/KDE) then ydotool "
                           "(anywhere); GNOME has no virtual-keyboard "
                           "protocol for wtype"),
            )
        )
        tools.add(
            self._switch(
                "hotkey",
                "wayland_evdev",
                _("Evdev push-to-talk"),
                _("Hold a physical key read from /dev/input - privileged "
                  "path (input group + python-evdev)"),
            )
        )
        tools.add(
            self._entry(
                "hotkey",
                "wayland_evdev_device",
                _("Device name pattern \u2014 e.g. AT Translated"),
            )
        )
        tools.add(
            self._entry(
                "hotkey",
                "wayland_evdev_key",
                _("Hold key \u2014 ecodes name (KEY_RIGHTCTRL)"),
            )
        )
        self.wayland_page.add(tools)
        self.wayland_page.add(self._save_group())
        self._add_page(self.wayland_page, section="more")
        _ = session_mod

    def _refresh_wayland_rows(self) -> None:
        """Fill the session/capability rows, the bindable command and the
        per-DE instructions from the live session (called on every load)."""
        from ... import session as session_mod

        info = session_mod.probe()
        caps = session_mod.capabilities(info, cfg=self.cfg)
        self._wayland_cap_rows["session"].set_subtitle(
            info.type + (f" ({info.desktop})" if info.desktop else "")
        )
        for cap in ("hotkey", "insertion", "clipboard", "overlay", "app-hint"):
            self._wayland_cap_rows[cap].set_subtitle(str(caps.get(cap, "?")))
        script = session_mod.ensure_toggle_script()
        command = str(script) if script is not None else session_mod.toggle_command()
        self.wayland_script_row.set_subtitle(command)
        for row in self._wayland_instruction_rows:
            self.wayland_bind_group.remove(row)
        self._wayland_instruction_rows = []
        for line in session_mod.de_shortcut_instructions(info.desktop_all, command):
            row = Adw.ActionRow(title=line.strip() or " ")
            self.wayland_bind_group.add(row)
            self._wayland_instruction_rows.append(row)
        self.wayland_open_row.set_visible(self._de_panel_command() is not None)

    def _de_panel_command(self) -> list[str] | None:
        """The DE shortcut-settings command for this desktop, if known
        (token matching over the FULL XDG_CURRENT_DESKTOP, so ubuntu:GNOME
        still finds the GNOME panel)."""
        from ... import session as session_mod

        desktop = session_mod.probe().desktop_all
        if session_mod.is_gnome_desktop(desktop):
            return self._DE_SHORTCUT_PANELS["gnome"]
        if any(t in ("kde", "plasma") for t in desktop.split(":")):
            return self._DE_SHORTCUT_PANELS["kde"]
        return None

    def _copy_toggle_script(self, *_args) -> None:
        text = self.wayland_script_row.get_subtitle()
        if text and text != "\u2014":
            Gdk.Display.get_default().get_clipboard().set_text(text)
            self.toast(_("Copied the command to bind"))

    def _open_de_shortcut_settings(self, *_args) -> None:
        import subprocess

        panel = self._de_panel_command()
        if panel is None:
            return
        try:
            subprocess.Popen(
                panel, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self.toast(_("Opening shortcut settings"))
        except Exception as e:
            self.toast(_("Could not open: {error}").format(error=e))
