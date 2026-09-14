"""System tray icon (StatusNotifierItem / AppIndicator) - the Linux
equivalent of the macOS menu bar icon, dropdown menu included.

While the daemon runs, the SayItErmano icon sits in the panel (GNOME with
the AppIndicator extension, KDE, XFCE, Budgie, ...). The tooltip reflects
state and the configured hotkey; while recording the icon gets a red
badge. Left click toggles dictation; right click opens a native dropdown
menu (com.canonical.dbusmenu) mirroring the macOS menu bar menu: status,
cancel, copy last transcript, settings, microphone picker, quit.

Everything is best-effort: without D-Bus, python-dbus, or a tray host the
daemon just runs headless as before.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

WATCHER = "org.kde.StatusNotifierWatcher"
SNI = "org.kde.StatusNotifierItem"
PROPS = "org.freedesktop.DBus.Properties"
DBUSMENU = "com.canonical.dbusmenu"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

SNI_ID = "sayitermano"
SNI_TITLE = "SayItErmano"

TRAY_SIZE = 64  # px; hosts scale down to their panel size

# menu item kinds understood by build_menu() models
KIND_ITEM = "item"
KIND_SEPARATOR = "separator"
KIND_CHECK = "check"

_MIC_CACHE_TTL = 5.0
_mic_cache: tuple[float, list[dict]] = (0.0, [])


def list_microphones(refresh: bool = False) -> list[dict]:
    """Input sources via pactl (PipeWire/PulseAudio): [{"name",
    "description", "default": bool}]. Monitor sources are excluded.
    Cached briefly - it is called at menu-open time."""
    global _mic_cache
    now = time.monotonic()
    if not refresh and _mic_cache[0] and now - _mic_cache[0] < _MIC_CACHE_TTL:
        return _mic_cache[1]
    mics: list[dict] = []
    try:
        out = subprocess.run(["pactl", "list", "sources"],
                             capture_output=True, text=True, timeout=3).stdout
        block: dict = {}
        for line in out.splitlines():
            line = line.strip()
            if m := re.match(r"Source #\d+", line):
                if block.get("name") and not block["name"].endswith(".monitor"):
                    mics.append(block)
                block = {}
            elif line.startswith("Name: "):
                block["name"] = line[6:]
            elif line.startswith("Description: "):
                block["description"] = line[13:]
        if block.get("name") and not block["name"].endswith(".monitor"):
            mics.append(block)
        default = ""
        try:
            default = subprocess.run(["pactl", "get-default-source"],
                                     capture_output=True, text=True,
                                     timeout=3).stdout.strip()
        except Exception:  # noqa: S110 — best-effort path; caller must proceed
            pass
        for m in mics:
            m["default"] = m["name"] == default
            m.setdefault("description", m["name"])
    except Exception:
        mics = []
    _mic_cache = (now, mics)
    return mics


def _bus_name() -> str:
    return f"{SNI}-{os.getpid()}-1"


def _default_icon_path() -> str | None:
    try:
        from importlib import resources
        ref = resources.files("fluidvoice.assets").joinpath("icon.png")
        with resources.as_file(ref) as p:
            return str(p)
    except Exception:
        return None


def render_pixmaps(icon_path: str | Path | None = None,
                   size: int = TRAY_SIZE) -> dict[str, tuple[int, int, bytes]]:
    """Idle + recording tray icons as ARGB32 big-endian pixmaps.

    Returns {"idle": (w, h, bytes), "recording": (w, h, bytes)}. The
    recording variant carries a red badge (the classic "recording" dot)
    so the state reads even at 22 px.
    """
    from PIL import Image, ImageDraw

    icon_path = icon_path or _default_icon_path()
    try:
        base = Image.open(icon_path).convert("RGBA")
    except Exception:
        base = None

    if base is None:
        # brand-less fallback: a simple mic-ish glyph on a dark tile
        base = Image.new("RGBA", (128, 128), (18, 20, 28, 255))
        d = ImageDraw.Draw(base)
        d.rounded_rectangle((44, 24, 84, 74), 20, fill=(235, 240, 250, 255))
        d.rounded_rectangle((40, 62, 88, 70), 4, fill=(235, 240, 250, 255))
        d.rectangle((61, 70, 67, 92), fill=(235, 240, 250, 255))
        d.ellipse((44, 88, 84, 100), outline=(235, 240, 250, 255), width=5)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), int(size * 0.22), fill=255)
    idle = base.resize((size, size), Image.LANCZOS)
    idle.putalpha(Image.composite(idle.getchannel("A"),
                                  Image.new("L", idle.size, 0), mask))

    rec = idle.copy()
    d = ImageDraw.Draw(rec)
    r = size // 5
    cx, cy = size - r - 2, r + 2
    d.ellipse((cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2),
              fill=(255, 255, 255, 235))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(232, 49, 63, 255))

    def argb32(img: Image.Image) -> tuple[int, int, bytes]:
        out = bytearray()
        for r_, g_, b_, a_ in img.getdata():
            out += bytes((a_, r_, g_, b_))  # SNI pixmaps are ARGB32 big-endian
        return img.width, img.height, bytes(out)

    return {"idle": argb32(idle), "recording": argb32(rec)}


def _build_item_class(dbus, owner: "TrayIcon"):
    """The SNI D-Bus object. Defined inside a factory so dbus.service is
    only imported where D-Bus actually exists."""
    import dbus.service

    class Item(dbus.service.Object):
        def __init__(self, bus, owner: TrayIcon):
            self._dbus = dbus
            self._owner = owner
            self._pixmaps = owner._pixmaps
            self._recording = False
            self._tip = SNI_TITLE
            super().__init__(bus, ITEM_PATH)

        def refresh(self, recording: bool, tip: str) -> None:
            self._recording = recording
            self._tip = tip
            self.NewIcon()
            self.NewToolTip()

        # -- SNI methods ---------------------------------------------------

        @dbus.service.method(SNI, in_signature="ii", out_signature="")
        def Activate(self, x, y):
            self._owner.on_activate()

        @dbus.service.method(SNI, in_signature="ii", out_signature="")
        def SecondaryActivate(self, x, y):
            self._owner.on_secondary()

        @dbus.service.method(SNI, in_signature="ii", out_signature="")
        def ContextMenu(self, x, y):
            self._owner.on_secondary()

        @dbus.service.method(SNI, in_signature="is", out_signature="")
        def Scroll(self, delta, orientation):
            pass

        # -- D-Bus properties ------------------------------------------------

        @dbus.service.method(PROPS, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            try:
                return self._props()[prop]
            except KeyError:
                raise dbus.DBusException(
                    f"no such property {prop}",
                    name=f"{PROPS}.Error.InvalidArgs")

        @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self._props()

        @dbus.service.method(PROPS, in_signature="ssv", out_signature="")
        def Set(self, interface, prop, value):
            pass

        @dbus.service.signal(SNI, signature="")
        def NewTitle(self):
            pass

        @dbus.service.signal(SNI, signature="")
        def NewIcon(self):
            pass

        @dbus.service.signal(SNI, signature="")
        def NewToolTip(self):
            pass

        @dbus.service.signal(SNI, signature="s")
        def NewStatus(self, status):
            pass

        def _props(self) -> dict:
            w, h, data = self._pixmaps[
                "recording" if self._recording else "idle"]
            pixmap = dbus.Array(
                [dbus.Struct((dbus.Int32(w), dbus.Int32(h),
                              dbus.ByteArray(data)), signature="iiay")],
                signature="(iiay)")
            empty = dbus.Array([], signature="(iiay)")
            tip = dbus.Struct(("", empty, SNI_TITLE, self._tip),
                              signature="(sa(iiay)ss)")
            return {
                "Category": dbus.String("ApplicationStatus"),
                "Id": dbus.String(SNI_ID),
                "Title": dbus.String(SNI_TITLE),
                "Status": dbus.String("Active"),
                "WindowId": dbus.Int32(0),
                "IconName": dbus.String(""),
                "IconPixmap": pixmap,
                "AttentionIconName": dbus.String(""),
                "AttentionIconPixmap": pixmap,
                "OverlayIconName": dbus.String(""),
                "OverlayIconPixmap": empty,
                "ToolTip": tip,
                "ItemIsMenu": dbus.Boolean(False),
                "Menu": dbus.ObjectPath(MENU_PATH),
            }

    return Item


def _build_menu_class(dbus, owner: "TrayIcon"):
    """Native dropdown menu (com.canonical.dbusmenu) mirroring the macOS
    menu bar menu. The model is rebuilt on every GetLayout/AboutToShow, so
    it always reflects live state; ids are stable within a layout only."""
    import dbus.service

    class Menu(dbus.service.Object):
        def __init__(self, bus, owner: TrayIcon):
            self._dbus = dbus
            self._owner = owner
            self._revision = 1
            self._id_counter = 0
            self._actions: dict[int, Callable] = {}
            super().__init__(bus, MENU_PATH)

        # -- model building -----------------------------------------------

        def _node(self, item: dict):
            self._id_counter += 1
            iid = self._id_counter
            kind = item.get("kind", KIND_ITEM)
            props: dict = {}
            if kind == KIND_SEPARATOR:
                props["type"] = dbus.String("separator")
                props["visible"] = dbus.Boolean(item.get("visible", True))
            else:
                props["label"] = dbus.String(item.get("label", ""))
                props["visible"] = dbus.Boolean(item.get("visible", True))
                props["enabled"] = dbus.Boolean(item.get("enabled", True))
                if item.get("checked"):
                    props["toggle-type"] = dbus.String("checkmark")
                    props["toggle-state"] = dbus.Int32(1)
                elif kind == KIND_CHECK:
                    props["toggle-type"] = dbus.String("checkmark")
                    props["toggle-state"] = dbus.Int32(0)
            if item.get("action"):
                self._actions[iid] = item["action"]
            children = item.get("children") or []
            kids = dbus.Array([self._node(c) for c in children], signature="v")
            return dbus.Struct(
                (dbus.Int32(iid), dbus.Dictionary(props, signature="sv"), kids),
                signature="(ia{sv}av)")

        def _root(self) -> tuple:
            self._id_counter = 0
            self._actions = {}
            root_props = dbus.Dictionary(
                {"children-display": dbus.String("submenu")}, signature="sv")
            kids = dbus.Array([self._node(it) for it in self._owner.build_menu()],
                              signature="v")
            return dbus.Struct((dbus.Int32(0), root_props, kids),
                               signature="(ia{sv}av)")

        # -- com.canonical.dbusmenu ------------------------------------------

        @dbus.service.method(DBUSMENU, in_signature="iias",
                             out_signature="u(ia{sv}av)")
        def GetLayout(self, parent_id, recursion_depth, property_list):
            try:
                return (dbus.UInt32(self._revision), self._root())
            except Exception as e:  # always answer; a silent failure hangs hosts
                self._owner._log(f"menu layout error: {e.__class__.__name__}: {e}")
                raise dbus.DBusException(f"layout error: {e}")

        @dbus.service.method(DBUSMENU, in_signature="i", out_signature="b")
        def AboutToShow(self, item_id):
            return True  # always re-pull: fresh state at every open

        @dbus.service.method(DBUSMENU, in_signature="isvu", out_signature="")
        def Event(self, item_id, event_id, data, timestamp):
            if event_id != "clicked":
                return
            action = self._actions.get(int(item_id))
            if action:
                def _run():
                    action()
                    return False  # one-shot idle callback
                self._owner._glib.idle_add(_run)

        @dbus.service.method(DBUSMENU, in_signature="a(isvu)", out_signature="i")
        def EventGroup(self, events):
            for item_id, event_id, data, ts in events:
                self.Event(item_id, event_id, data, ts)
            return dbus.Int32(len(events))

        @dbus.service.method(DBUSMENU, in_signature="aias", out_signature="a(ia{sv})")
        def GetGroupProperties(self, ids, property_names):
            # hosts only need this for push-updated sections; we always
            # serve a fresh full layout instead
            return dbus.Array([], signature="(ia{sv})")

        @dbus.service.method(DBUSMENU, in_signature="is", out_signature="v")
        def GetProperty(self, item_id, name):
            return dbus.String("")

        @dbus.service.method(PROPS, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            if prop == "Version":
                return dbus.UInt32(3)
            raise dbus.DBusException(f"no such property {prop}",
                                     name=f"{PROPS}.Error.InvalidArgs")

        @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return {"Version": dbus.UInt32(3)}

        @dbus.service.signal(DBUSMENU, signature="ui")
        def LayoutUpdated(self, revision, parent_id):
            pass

        def changed(self) -> None:
            self._revision += 1
            self.LayoutUpdated(self._revision, 0)

    return Menu


class TrayIcon:
    """Owns the SNI bus name and serves the item on a GLib loop thread.
    start() reports whether the icon actually came up."""

    def __init__(self, on_activate: Callable[[], None] | None = None,
                 on_secondary: Callable[[], None] | None = None,
                 tooltip: Callable[[], str] | None = None,
                 build_menu: Callable[[], list] | None = None,
                 log: Callable[[str], None] = (lambda m: None)):
        self.on_activate = on_activate or (lambda: None)
        self.on_secondary = on_secondary or (lambda: None)
        self.tooltip = tooltip or (lambda: SNI_TITLE)
        self.build_menu = build_menu or (lambda: [])
        self._log = log
        self.active = False
        self._recording = False
        self._lock = threading.Lock()
        self._loop = None
        self._item = None
        self._menu = None
        self._bus = None
        self._pixmaps = render_pixmaps()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> bool:
        try:
            import dbus  # noqa: F401
            import dbus.service  # noqa: F401
            from dbus.mainloop.glib import DBusGMainLoop  # noqa: F401
            from gi.repository import GLib  # noqa: F401
        except Exception as e:
            self._log(f"tray unavailable ({e.__class__.__name__}: {e})")
            return False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fluidvoice-tray",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        return self.active

    def _run(self) -> None:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
        self._dbus = dbus
        self._glib = GLib
        try:
            DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            self._bus = bus
            bus.request_name(_bus_name())
            item_cls = _build_item_class(dbus, self)
            self._item = item_cls(bus, self)
            menu_cls = _build_menu_class(dbus, self)
            self._menu = menu_cls(bus, self)
            # register with the host; re-register whenever a (new) watcher
            # appears - the shell may reload and drop us at any time
            self._register()
            bus.add_signal_receiver(
                self._watcher_changed, signal_name="NameOwnerChanged",
                dbus_interface="org.freedesktop.DBus", arg0=WATCHER)
            self._loop = GLib.MainLoop()
            self.active = True
        except Exception as e:
            self._log(f"tray unavailable ({e.__class__.__name__}: {e})")
            self.active = False
        finally:
            self._ready.set()
        if self._loop is not None:
            self._loop.run()

    def _register(self) -> bool:
        try:
            watcher = self._dbus.Interface(
                self._bus.get_object(WATCHER, "/StatusNotifierWatcher"), WATCHER)
            watcher.RegisterStatusNotifierItem(_bus_name(), timeout=5)
            self._log("tray icon registered")
            self._glib.idle_add(self._apply_state)  # real icon+tooltip at once
            return True
        except Exception as e:
            self._log(f"no tray host yet ({e.__class__.__name__}); will retry "
                      "when a StatusNotifierWatcher appears")
            return False

    def _watcher_changed(self, name, old_owner, new_owner) -> None:
        if new_owner and name == WATCHER:
            self._glib.idle_add(self._register)

    def stop(self) -> None:
        self.active = False
        loop, self._loop = self._loop, None
        if loop is not None:
            try:
                loop.quit()
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                pass

    def refresh(self) -> None:
        """Re-push icon/tooltip/menu from the current state, callable from
        any thread (applied on the loop thread). Used when hotkey grab
        health flips so the tooltip's blocked suffix follows recovery live."""
        if self.active and self._loop is not None:
            try:
                self._glib.idle_add(self._apply_state)
            except Exception:  # noqa: S110 — cosmetic surface; degrade silently
                pass

    # -- state (thread-safe; applied on the loop thread) ----------------------

    def set_recording(self, recording: bool) -> None:
        with self._lock:
            changed = recording != self._recording
            self._recording = recording
        if changed and self.active and self._loop is not None:
            try:
                self._glib.idle_add(self._apply_state)
            except Exception:  # noqa: S110 — best-effort path; caller must proceed
                pass

    def _apply_state(self) -> bool:  # on the GLib loop; False = run once
        with self._lock:
            recording = self._recording
        if self._item is not None:
            self._item.refresh(recording, self.tooltip())
        if self._menu is not None:
            self._menu.changed()  # status line follows recording state
        return False
