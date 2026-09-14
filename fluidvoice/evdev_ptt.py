"""Optional evdev push-to-talk for Wayland sessions (hotkey.wayland_evdev).

Wayland has no global key grabs, so the physical push-to-talk fallback is
a raw evdev listener: read /dev/input/event* directly, match the device by
name substring, hold-to-talk on one key.

This is a PRIVILEGED path: reading /dev/input needs the `input` group (or
equivalent udev rules). Doctor checks and says so plainly. python-evdev is
an optional dependency (`pip install 'sayit-ermano[wayland]'`); the class
degrades to a WARN log when it or the device is missing - never fatal.

Auto-repeat filtering mirrors hotkey.py: kernel repeats arrive as
value==2 events and are ignored, so only the first press and the final
release fire the callbacks.
"""
from __future__ import annotations

import glob
import threading
from typing import Callable

# linux/uapi input-event constants (stable ABI; avoids importing evdev
# just to compare the event type)
EV_KEY = 0x01
EV_VALUE_RELEASE = 0
EV_VALUE_PRESS = 1
EV_VALUE_REPEAT = 2

DEFAULT_KEY = "KEY_RIGHTCTRL"


def _list_devices() -> list[str]:
    """Input event device paths (sorted for determinism)."""
    return sorted(glob.glob("/dev/input/event*"))


def _open_device(path: str):
    """Open one InputDevice (monkeypatched by tests)."""
    import evdev
    return evdev.InputDevice(path)


def _resolve_key_code(key_name: str) -> int:
    """KEY_* name -> linux key code (monkeypatched by tests)."""
    import evdev
    return int(evdev.ecodes.ecodes[key_name])


class EvdevPTT:
    """Hold-to-talk on a physical key, read straight from evdev.

    device_substr: case-insensitive substring of the device NAME as
    /dev/input reports it (e.g. "Keyboard" or "AT Translated").
    key_name: evdev/ecodes name of the hold key (KEY_RIGHTCTRL default,
    mirroring the X11 hotkey default).
    """

    def __init__(self, device_substr: str, key_name: str, *,
                 on_press: Callable[[], None], on_release: Callable[[], None],
                 log: Callable[[str], None] = print):
        self.device_substr = (device_substr or "").strip()
        self.key_name = (key_name or DEFAULT_KEY).strip().upper()
        self.on_press = on_press
        self.on_release = on_release
        self.log = log
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._dev = None
        self._key_code: int | None = None
        self._pressed = False
        self.summary: list[str] = []

    # -- setup ---------------------------------------------------------------

    def _find_device(self):
        for path in _list_devices():
            try:
                dev = _open_device(path)
            except Exception:  # unreadable / vanished: try the next one
                continue
            if self.device_substr.lower() in dev.name.lower():
                return dev
            try:
                dev.close()
            except Exception:  # noqa: S110 — best-effort path; caller must proceed
                pass
        return None

    def start(self) -> bool:
        """Best-effort start: WARN + False (never raises) when python-evdev
        is missing, the key name is unknown or no device matches - the
        daemon stays useful without the listener."""
        if self._thread:
            return True
        try:
            import evdev  # noqa: F401 - presence probe
        except ImportError:
            self.log("WARN evdev push-to-talk: python-evdev not installed "
                     "(pip install 'sayit-ermano[wayland]') - disabled")
            return False
        try:
            self._key_code = _resolve_key_code(self.key_name)
        except Exception as e:
            self.log(f"WARN evdev push-to-talk: unknown key "
                     f"{self.key_name!r} ({e}) - disabled")
            return False
        if not self.device_substr:
            self.log("WARN evdev push-to-talk: no device pattern configured "
                     "(hotkey.wayland_evdev_device) - disabled")
            return False
        dev = self._find_device()
        if dev is None:
            self.log(f"WARN evdev push-to-talk: no /dev/input device "
                     f"matching {self.device_substr!r} - disabled "
                     f"(input-group access? sayit-ermano doctor)")
            return False
        self._dev = dev
        self.summary = [f"evdev push-to-talk: {dev.name} ({dev.fn if hasattr(dev, 'fn') else '?'}) "
                        f"hold {self.key_name} to talk"]
        self._stop.clear()
        self._thread = threading.Thread(target=self._run,
                                        name="fluidvoice-evdev-ptt",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        dev, self._dev = self._dev, None
        if dev is not None:
            try:
                dev.close()  # breaks a blocking read_one if any
            except Exception:  # noqa: S110 — teardown/cleanup must not raise
                pass

    # -- loop ------------------------------------------------------------------

    def _run(self) -> None:
        dev = self._dev
        if dev is None:
            return
        while not self._stop.is_set():
            try:
                event = dev.read_one()
            except Exception as e:  # noqa: BLE001 - unplug, permission loss
                self.log(f"WARN evdev push-to-talk: device read failed "
                         f"({e}) - listener stopped")
                return
            if event is None:
                self._stop.wait(0.01)  # poll: responsive to stop()
                continue
            try:
                self._handle(event)
            except Exception as e:  # noqa: BLE001 - callbacks must not kill it
                self.log(f"WARN evdev push-to-talk callback failed: {e}")

    def _handle(self, event) -> None:
        """The state machine, isolated so tests can drive it directly."""
        if getattr(event, "type", None) != EV_KEY:
            return
        if self._key_code is None or getattr(event, "code", None) != self._key_code:
            return
        value = getattr(event, "value", None)
        if value == EV_VALUE_REPEAT:
            return  # kernel auto-repeat: only edges matter
        if value == EV_VALUE_PRESS and not self._pressed:
            self._pressed = True
            self._safe(self.on_press)
        elif value == EV_VALUE_RELEASE and self._pressed:
            self._pressed = False
            self._safe(self.on_release)

    def _safe(self, cb) -> None:
        try:
            cb()
        except Exception as e:  # noqa: BLE001 - degrade like hotkey.py
            self.log(f"WARN evdev push-to-talk: {e}")

    # -- introspection ----------------------------------------------------------

    @property
    def pressed(self) -> bool:
        return self._pressed
