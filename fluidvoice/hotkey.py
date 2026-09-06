"""Global hotkey on X11 via XGrabKey (python-xlib).

- "toggle" mode: every KeyPress of the hotkey fires the callback.
  Works with modifier-only keys (Right_Control, Right_Alt, ...).
- "hold" mode (push-to-talk): KeyPress starts, KeyRelease stops. Requires
  a non-modifier key. Falls back to toggle for modifier-only keys.
  Keys typed while holding PASS THROUGH to the focused app natively:
  the hold opens by releasing the XGrabKey activation (which by X11
  semantics grabs the whole keyboard for the key's press-to-release
  duration) and re-arming it when the hold ends. The release itself is
  detected by polling query_keymap() - auto-repeat-proof, since the ~30 Hz
  synthetic KeyRelease+KeyPress pairs of a held key never clear its bit.
  (An earlier XTEST ungrab->inject->re-grab replay design was abandoned:
  live Xorg 21.1 silently drops XTEST fakes that match the current key
  state - the original event already flipped it - so replayed presses
  are deduped away and never reach the app.)
- "MousePTTListener": mouse-button push-to-talk via XGrabButton. The press
  is a core passive grab (all lock-mask combos, owner_events=False); the
  grab activation is released with ungrab_pointer so CLICKS during the
  hold pass through to the focused window natively, and the passive grab
  SURVIVES that ungrab (buttons have no auto-repeat, unlike keys, so no
  re-arm dance is needed). Release detection is XI2 RawButtonRelease:
  core XQueryPointer's CARD16 mask only carries buttons 1-5, which cannot
  see the canonical thumb buttons (8/9), so the listener subscribes to raw
  events on all master pointers and parses them from python-xlib's
  GenericEvent bytes (RawButtonRelease is not registered in the ge table).
  RawButtonRelease requires a negotiated XI version > 2.0 - python-xlib
  hardcodes XIQueryVersion(2,0) and live Xorg then silently withholds the
  release events - so XI 2.2 is negotiated directly as the connection's
  FIRST XI2 request (the version is fixed per client at first ask).
  A passive Escape grab covers cancel-during-hold, like keyboard holds.
"""
from __future__ import annotations

import threading
import time

from Xlib import X, XK
from Xlib.display import Display
from Xlib.ext import xinput as _xi_ext

MODIFIER_MASKS = {
    "ctrl": X.ControlMask,
    "alt": X.Mod1Mask,
    "shift": X.ShiftMask,
    "super": X.Mod4Mask,
}
# Lock states that would otherwise defeat the grab (Num/Caps/Scroll)
_LOCK_MASKS = [0, X.Mod2Mask, X.LockMask, X.Mod5Mask,
               X.Mod2Mask | X.LockMask, X.Mod2Mask | X.Mod5Mask,
               X.LockMask | X.Mod5Mask, X.Mod2Mask | X.LockMask | X.Mod5Mask]

# A refused hotkey combo warns once after this many distinct attempts,
# then stays quiet until the next recording-idle period (retries never stop).
_MAX_GRAB_ATTEMPTS = 10

MODIFIER_ONLY_KEYSYMS = {
    XK.XK_Shift_L, XK.XK_Shift_R, XK.XK_Control_L, XK.XK_Control_R,
    XK.XK_Alt_L, XK.XK_Alt_R, XK.XK_Super_L, XK.XK_Super_R,
    XK.XK_Meta_L, XK.XK_Meta_R, XK.XK_Hyper_L, XK.XK_Hyper_R,
    getattr(XK, "XK_ISO_Level3_Shift", None),
    getattr(XK, "XK_ISO_Level5_Shift", None),
} - {None}

# Upstream macOS: the cancel shortcut (Escape) only acts while the overlay
# is up - i.e. dictation in progress. Same here: the cancel key is grabbed
# only while recording, so an idle daemon never swallows Escape.
DEFAULT_CANCEL_KEY = "Escape"


class HotkeyError(RuntimeError):
    pass


# Mouse push-to-talk button range: 1-3 are click buttons, 4-5 are scroll
# (wheel) buttons - grabbing any of those would break the desktop, so the
# parser refuses them. X's button detail is a CARD8 (<= 255).
MOUSE_PTT_MIN_BUTTON = 6
MOUSE_PTT_MAX_BUTTON = 255


def parse_button_spec(name: str) -> int | None:
    """Config value -> X button number for mouse push-to-talk.

    None for ""/blank/"none" (feature off). Accepts "button8", "b8", "8",
    "Button 8" (case/space insensitive, leading zeros stripped). Buttons
    1-5 raise HotkeyError (a click/scroll button would break the desktop:
    the grab would swallow every primary click while armed); so does
    anything unparsable or outside the 6-255 CARD8 range."""
    raw = (name or "").strip()
    if not raw or raw.lower() in ("none", "off", "disabled"):
        return None
    normalized = raw.lower().replace(" ", "")
    if normalized.startswith("button"):
        normalized = normalized[len("button"):]
    elif normalized.startswith("b"):
        normalized = normalized[1:]
    if not normalized.isdigit():
        raise HotkeyError(f"invalid mouse button '{name}' (expected e.g. "
                          "'button8', 'b8' or '8')")
    button = int(normalized)
    if not MOUSE_PTT_MIN_BUTTON <= button <= MOUSE_PTT_MAX_BUTTON:
        if 1 <= button <= 3:
            kind = "primary/click button"
        elif 4 <= button <= 5:
            kind = "scroll/wheel button"
        else:
            raise HotkeyError(f"mouse button '{name}' out of range "
                              f"({MOUSE_PTT_MIN_BUTTON}-"
                              f"{MOUSE_PTT_MAX_BUTTON})")
        raise HotkeyError(f"mouse button {button} is a {kind} - grabbing it "
                          "would break the desktop; push-to-talk needs a "
                          "spare button (thumb buttons are usually 8/9)")
    return button


def parse_raw_button_event(data) -> tuple[int, int] | None:
    """(deviceid, button) from a raw XI2 GenericEvent's .data bytes, or
    None when the payload is missing/too short/non-bytes.

    python-xlib does not register RawButton{Press,Release} in its generic-
    event table, so they arrive as GenericEvent with `.evtype` (15 = press,
    16 = release) and `.data` = the raw wire payload past the 10-byte
    generic header. Little-endian layout: CARD16 deviceid @ [0:2], CARD32
    time @ [2:6], CARD32 detail (= the button number) @ [6:10]."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 10:
        return None
    return (int.from_bytes(data[0:2], "little"),
            int.from_bytes(data[6:10], "little"))


# XI2 evtype numbers for raw events (XIRawEvent; evtype is on the
# GenericEvent because python-xlib leaves Raw* unregistered - see above)
_XI_RAW_BUTTON_PRESS = _xi_ext.RawButtonPress    # 15
_XI_RAW_BUTTON_RELEASE = _xi_ext.RawButtonRelease  # 16


def _xi_query_version(display, major: int, minor: int):
    """XIQueryVersion built directly with a caller-chosen version.

    python-xlib's Display.xinput_query_version hardcodes (2, 0), and live
    Xorg delivers RawButtonRelease only to clients that negotiated > 2.0
    (with 2.0 you get raw presses but never releases - verified
    side-by-side against `xinput test-xi2 --root`). The negotiated version
    is fixed per client at its FIRST XI2 request, so this must be called
    before any other XI2 request on the connection."""
    return _xi_ext.XIQueryVersion(
        display=display,
        opcode=display.get_extension_major(_xi_ext.extname),
        major_version=major,
        minor_version=minor)


# Verdicts for one keyboard event seen during a hold cycle (see
# classify_hold_event). In the native-passthrough design only ABORT is
# consumed by the hold loop; REPLAY-classified keys flow straight to the
# focused app and are never delivered to us at all.
_HOLD_END, _HOLD_ABORT, _HOLD_REPLAY, _HOLD_IGNORE = "end", "abort", "replay", "ignore"
# verdicts for one event seen during a MOUSE hold cycle (see
# MousePTTListener._classify_hold_event): END = raw release of the PTT
# button, ABORT = Escape press, REPRESS = the same button pressed again
# mid-hold by another pointer (passive-grab re-activation; tolerated by
# ungrabbing again), IGNORE = everything else (raw presses, other buttons,
# motions, the aborting key's release)
_MOUSE_HOLD_REPRESS = "re-press"


def classify_hold_event(etype, detail, hotkey_keycode, escape_keycode) -> str:
    """Pure: how _hold_cycle should treat one keyboard event.

    - KeyRelease(hotkey)            -> end
    - KeyPress(escape_keycode)      -> abort   (cancel recording)
    - Key{Press,Release}(anything else incl. modifiers) -> replay
      (delivered natively to the focused app; the hold loop ignores them)
    - hotkey KeyPress (auto-repeat), escape KeyRelease, non-key events,
      None details                  -> ignore
    escape_keycode may be None (cancel disabled) -> escape is a normal key.
    """
    if etype not in (X.KeyPress, X.KeyRelease) or detail is None:
        return _HOLD_IGNORE
    if etype == X.KeyRelease:
        if detail == hotkey_keycode:
            return _HOLD_END
        if escape_keycode is not None and detail == escape_keycode:
            return _HOLD_IGNORE  # the aborting press already ended the hold
        return _HOLD_REPLAY
    if etype == X.KeyPress:
        if escape_keycode is not None and detail == escape_keycode:
            return _HOLD_ABORT
        if detail == hotkey_keycode:
            return _HOLD_IGNORE  # auto-repeat press of the held hotkey
    return _HOLD_REPLAY


# Friendly aliases -> X11 keysym names (macOS-style modifiers etc.)
_KEY_ALIASES = {
    "right_control": "Control_R", "left_control": "Control_L",
    "right_ctrl": "Control_R", "left_ctrl": "Control_L",
    "right_alt": "Alt_R", "left_alt": "Alt_L", "right_option": "Alt_R",
    "left_option": "Alt_L", "right_command": "Super_R", "left_command": "Super_L",
    "right_super": "Super_R", "left_super": "Super_L", "super": "Super_L",
    "right_shift": "Shift_R", "left_shift": "Shift_L",
    "esc": "Escape", "return": "Return", "enter": "Return",
}


def resolve_keysym(name: str) -> int:
    keysym = XK.string_to_keysym(name)
    if keysym == X.NoSymbol:
        alias = _KEY_ALIASES.get(name.strip().lower().replace(" ", "_"))
        if alias:
            keysym = XK.string_to_keysym(alias)
    if keysym == X.NoSymbol:
        # "page up" -> "Page_Up", "right control" -> "Right_Control"
        normalized = "_".join(p.capitalize() for p in name.strip().split())
        keysym = XK.string_to_keysym(normalized)
    if keysym == X.NoSymbol:
        raise HotkeyError(f"unknown key name '{name}'")
    return keysym


class HotkeyListener:
    """Grabs the hotkey and invokes callbacks from its own thread."""

    def __init__(self, key: str, modifiers: list[str], mode: str,
                 on_toggle, on_cancel=None, cancel_key: str | None = None,
                 display_name: str | None = None,
                 log=None, on_grab_change=None):
        self.key = key
        self.mode = mode
        self.on_toggle = on_toggle
        self.on_cancel = on_cancel
        self.cancel_key = cancel_key
        self.display_name = display_name
        self.log = log or (lambda m: print(f"[sayit-ermano] {m}", flush=True))
        # fired (best-effort) whenever hotkey health flips, so surfaces
        # (tray tooltip, status) can follow blocking/recovery live
        self._on_grab_change = on_grab_change or (lambda healthy: None)
        self._mods = sum(MODIFIER_MASKS.get(m, 0) for m in modifiers)
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._display: Display | None = None
        self._keycode = 0
        self._cancel_keycode: int | None = None
        self._escape_keycode: int | None = None
        self._want_cancel = False   # recording active -> grab the cancel key
        self._cancel_grabbed = False
        self._cancel_grab_warned = False
        # Grab health, per (keycode, full modifier mask) combo: python-xlib
        # never raises BadAccess through grab_key - it hands the error to a
        # per-request onerror callback (a truthy return suppresses the
        # printing default handler). A combo the X server refuses (another
        # client already holds it) is tracked here as data and retried by
        # _sync_hotkey_grab on the poll-loop cadence until it succeeds.
        # _pending holds combos whose latest grab is issued but not yet
        # error-pumped: optimistic marks must never read as healthy (the
        # retry thread and the readers race - a blocked daemon must never
        # transiently report a good grab).
        self._combo_ok: dict[tuple[int, int], bool] = {}
        self._combo_attempts: dict[tuple[int, int], int] = {}
        self._pending: set[tuple[int, int]] = set()
        self._refuse_warned = False
        self._was_healthy: bool | None = None  # None until first sync
        self._summary: list[str] = []

    # -- setup ---------------------------------------------------------------

    def _keycode_for(self, keysym: int) -> int:
        assert self._display is not None
        code = self._display.keysym_to_keycode(keysym)
        return code or 0

    def _grab(self, keycode: int, masks=None) -> None:
        """Issue one grab per lock-mask combo, routing refusals to data.

        Each grab_key carries an onerror closure keyed by (keycode, full
        mask) so a BadAccess (another client holds that combo) marks the
        combo missing instead of hitting python-xlib's printing default
        handler - grab_key itself never raises for it. Marked optimistically
        True; the closure flips it False when the server says no. `masks`
        (full modifier masks, _mods already folded in) narrows the issue to
        the combos a caller knows are missing; default = every combo."""
        assert self._display is not None
        if masks is None:
            masks = [self._mods | extra for extra in _LOCK_MASKS]
        root = self._display.screen().root
        for mask in masks:
            combo = (keycode, mask)
            self._combo_ok[combo] = True
            self._pending.add(combo)
            root.grab_key(keycode, mask, False,
                          X.GrabModeAsync, X.GrabModeAsync,
                          onerror=self._make_grab_onerror(combo))

    def _settle_grabs(self, keycode: int, masks=None) -> None:
        """Pump errors for recently issued grabs and mark them resolved:
        after sync() every onerror the server had queued has fired, so the
        optimistic marks that survive are settled truth."""
        if masks is None:
            masks = [self._mods | extra for extra in _LOCK_MASKS]
        try:
            self._display.sync()
        except Exception:
            return  # display dying: leave pending (reads as unresolved)
        for mask in masks:
            self._pending.discard((keycode, mask))

    def _make_grab_onerror(self, combo: tuple[int, int]):
        """Per-request error handler for one grab combo. Must stay trivial
        (dict writes + one log) and must return truthy, or the error falls
        through to the printing default handler."""
        def _onerror(_error, _request) -> int:
            self._combo_ok[combo] = False
            self._pending.discard(combo)
            attempts = self._combo_attempts.get(combo, 0) + 1
            self._combo_attempts[combo] = attempts
            if attempts >= _MAX_GRAB_ATTEMPTS and not self._refuse_warned:
                self._refuse_warned = True
                self.log(f"WARN hotkey grab still refused after {attempts} "
                         "attempts - held by another client?")
            return 1  # handled: suppress the printing default handler
        return _onerror

    @property
    def hotkey_grabbed(self) -> bool:
        """True = all lock-mask combos of the hotkey are believed held.
        Partial coverage (some Num/Caps/Scroll states refused) is False:
        the hotkey would only work in some lock states, so it is reported
        and retried until complete. Grabs issued but not yet error-pumped
        (in-flight retries) do NOT count - a blocked daemon must never
        transiently read as healthy."""
        return self._keycode != 0 and all(
            self._combo_ok.get((self._keycode, self._mods | extra), False)
            and (self._keycode, self._mods | extra) not in self._pending
            for extra in _LOCK_MASKS)

    def _sync_hotkey_grab(self) -> None:
        """Self-heal: re-attempt missing hotkey combos, then resolve errors.

        Called from setup() and as the first step of every poll-loop tick
        (the same ~10 ms cadence _sync_cancel_grab rides). A healthy grab
        issues zero extra X traffic; a refused one retries every tick so a
        released holder is re-taken within ~1 s, with the refusal WARN
        capped (see _MAX_GRAB_ATTEMPTS). The daemon must never sit "ready"
        with a dead hotkey."""
        try:
            if self._display is None or not self._keycode:
                return
            missing = [self._mods | extra for extra in _LOCK_MASKS
                       if not self._combo_ok.get(
                           (self._keycode, self._mods | extra), False)
                       or (self._keycode, self._mods | extra) in self._pending]
            if not missing:
                return  # healthy: nothing to do, no X traffic
            healthy_before = self.hotkey_grabbed
            self._grab(self._keycode, masks=missing)
            self._settle_grabs(self._keycode, masks=missing)
            healthy_after = self.hotkey_grabbed
            if healthy_after != healthy_before:
                try:
                    self._on_grab_change(healthy_after)
                except Exception:
                    pass
            if (healthy_after and not healthy_before
                    and self._was_healthy is not None):
                # recovered after a refusal (not the initial grab): the
                # blocking period is over - log it once and re-arm the
                # WARN cycle for any future refusal
                self.log("hotkey grab recovered")
                for extra in _LOCK_MASKS:
                    self._combo_attempts.pop((self._keycode, self._mods | extra), None)
                self._refuse_warned = False
            self._was_healthy = healthy_after
        except Exception:
            pass  # a display closing under stop() must not kill the loop

    def _resolve_cancel(self) -> str:
        """Config value -> keysym name. None/"" mean the macOS default
        (Escape) - important for upgrade migration, since old templates
        wrote cancel_key = "" into saved configs. "none"/"off" disables."""
        raw = "" if self.cancel_key is None else self.cancel_key.strip()
        if not raw:
            return DEFAULT_CANCEL_KEY
        if raw.lower() in ("none", "off", "disabled"):
            return ""
        return raw

    def setup(self) -> list[str]:
        try:
            self._display = Display(self.display_name)
        except Exception as e:
            raise HotkeyError(f"cannot open X display ({e}) - is this an X11 session?") from e
        self._keycode = self._keycode_for(resolve_keysym(self.key))
        if not self._keycode:
            raise HotkeyError(f"key '{self.key}' has no keycode on this keymap")
        self._sync_hotkey_grab()  # grab + record per-combo health
        if not self.hotkey_grabbed:
            # another client holds some combos; retries start immediately in
            # _run(), but the listener's cap-WARN stays quiet here - the
            # daemon owns the startup WARN + notification for this moment
            self._refuse_warned = True
        # cancel acts ONLY while recording (macOS overlay-up semantics)
        cancel = self._resolve_cancel()
        self._cancel_keycode = self._keycode_for(resolve_keysym(cancel)) \
            if cancel else None
        self._cancel_grabbed = False
        self._escape_keycode = self._keycode_for(XK.XK_Escape)
        self._display.sync()
        self._summary = [f"hotkey {self.key} = keycode {self._keycode}, "
                         f"modifiers {self._mods:#x}, mode {self.mode}"
                         + (f", cancel {cancel} while recording" if cancel
                            else ", cancel disabled")]
        return self._summary

    # -- loop ----------------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self.setup()
        self._thread = threading.Thread(target=self._run, name="fluidvoice-hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._display:
            try:
                self._display.close()
            except Exception:
                pass

    @property
    def summary(self) -> list[str]:
        return list(self._summary)

    def _run(self) -> None:  # pragma: no cover - needs a real X server
        d = self._display
        assert d is not None
        keysym = resolve_keysym(self.key)
        if self.mode in ("hold", "both") and keysym in MODIFIER_ONLY_KEYSYMS:
            self.mode = "toggle"  # push-to-talk needs a non-modifier key
        try:
            while not self._stop_flag.is_set():
                self._sync_hotkey_grab()  # re-take refused combos (~10 ms)
                self._sync_cancel_grab()
                if d.pending_events() == 0:
                    self._stop_flag.wait(0.01)  # poll: responsive to grabs/stop
                    continue
                try:
                    event = d.next_event()
                except Exception:
                    break
                if getattr(event, "type", None) != X.KeyPress:
                    continue
                detail = getattr(event, "detail", None)
                if detail is None:
                    continue
                if self._cancel_keycode and detail == self._cancel_keycode:
                    self._safe(self.on_cancel)
                    continue
                if detail != self._keycode:
                    continue
                if self.mode == "both":
                    self._both_cycle(d, detail)
                elif self.mode == "hold":
                    self._hold_cycle(d, detail)
                else:
                    self._safe(self.on_toggle)
        finally:
            try:
                d.close()
            except Exception:
                pass

    def set_recording(self, active: bool) -> None:
        """Tell the listener dictation started/stopped; the cancel key is
        grabbed only while recording (macOS parity: Escape dismisses the
        overlay and discards, and does nothing when idle)."""
        self._want_cancel = bool(active)
        if not active:
            self._refuse_warned = False  # new idle period may WARN again

    def _sync_cancel_grab(self) -> None:
        if self._display is None or not self._cancel_keycode:
            return
        if self._want_cancel == self._cancel_grabbed:
            return
        root = self._display.screen().root
        try:
            if self._want_cancel:
                self._grab(self._cancel_keycode)
                self._settle_grabs(self._cancel_keycode)
            else:
                root.ungrab_key(self._cancel_keycode, X.AnyModifier)
                self._display.sync()
            self._cancel_grabbed = self._want_cancel
            self._cancel_grab_warned = False
        except Exception:
            # Another client holds a conflicting grab on the cancel key (a
            # second recording daemon on a shared desktop, a WM shortcut).
            # Retried every loop, so a transient holder self-heals; warn
            # once per recording so a dead cancel key is log-diagnosable.
            if self._want_cancel and not self._cancel_grab_warned:
                self._cancel_grab_warned = True
                self.log(f"WARN cancel key '{self.cancel_key}' "
                         "grab failed - held by another client?")
            # best-effort; cancel via CLI still works

    def _hotkey_still_down(self, d, keycode: int) -> bool:
        """True = the hotkey is still physically held, by query_keymap().
        Auto-repeat-proof: the ~30 Hz synthetic KeyRelease+KeyPress pairs a
        held key generates never clear its bit, so a clear bit is a REAL
        release. On any error: return True (keep holding; a closed display
        is handled by pending_events/next_event raising)."""
        try:
            km = d.query_keymap()
            return bool(km[keycode // 8] & (1 << (keycode % 8)))
        except Exception:
            return True

    def _hold_cycle(self, d: Display, keycode: int) -> None:
        """Push-to-talk with native key passthrough.

        The XGrabKey passive grab that fired this hold activates a FULL
        keyboard grab for the key's press-to-release duration (X11
        semantics) - historically that swallowed every other keystroke
        typed during the hold. This cycle instead RELEASES that activation
        immediately (ungrab_keyboard + ungrab_key), so every other key
        flows to the focused application natively: real events, no
        XTEST/XSendEvent injection, no fake-event dedup races. The hotkey's
        release is detected by polling query_keymap() (auto-repeat-proof);
        a passive Escape grab is armed just for the hold so Escape still
        cancels the recording (upstream semantics: a cancelled recording is
        discarded, not transcribed); the hotkey grab is re-armed on the way
        out. Deliberate divergences (documented in STATUS.md): typed keys
        do NOT interrupt the dictation (upstream clean-tap does), and the
        held hotkey's auto-repeat pairs reach the focused app like any
        other key. If the initial ungrab fails, keys keep being swallowed
        - the pre-passthrough behavior - but the hold still works."""
        self._safe(self.on_toggle)  # start
        self._free_keyboard(d, keycode)
        aborted = self._hold_until_release(d, keycode)
        if aborted:
            self._safe(self.on_cancel)
        else:
            self._safe(self.on_toggle)  # stop and transcribe

    def _free_keyboard(self, d: Display, keycode: int) -> None:
        """Release the passive grab's full-keyboard activation so other
        keys pass through to the focused app (best-effort)."""
        try:
            d.ungrab_keyboard(X.CurrentTime)
            d.screen().root.ungrab_key(keycode, X.AnyModifier)
            d.sync()
        except Exception:
            pass  # best-effort: worst case keys stay swallowed

    def _hold_until_release(self, d: Display, keycode: int) -> bool:
        """Arm the hold-time Escape grab, poll query_keymap() until the
        hotkey's REAL release (auto-repeat never clears the bit), re-arm
        the hotkey grab on the way out. Returns True when Escape aborted
        the hold. See _hold_cycle for the passthrough rationale."""
        root = d.screen().root
        aborted = False
        try:
            # Escape still cancels: arm a passive grab for the hold only
            # (its press-activation delivers Escape to us; other keys are
            # untouched by a passive grab that has not fired).
            if self._escape_keycode:
                try:
                    root.grab_key(self._escape_keycode, X.AnyModifier, False,
                                  X.GrabModeAsync, X.GrabModeAsync,
                                  onerror=lambda _e, _r: 1)  # swallow refusals
                except Exception:
                    pass  # best-effort: cancel via CLI still works
            try:
                while not self._stop_flag.is_set():
                    # Release detection: a clear keymap bit is a real
                    # release (auto-repeat never clears it).
                    if not self._hotkey_still_down(d, keycode):
                        break
                    # Escape (or anything the escape-grab activation
                    # delivered to us) - the only events we can see now.
                    try:
                        if d.pending_events():
                            event = d.next_event()
                            if classify_hold_event(
                                    getattr(event, "type", None),
                                    getattr(event, "detail", None),
                                    keycode, self._escape_keycode) == _HOLD_ABORT:
                                aborted = True
                                break
                    except Exception:
                        break  # display closed / stop() - end the hold
                    self._stop_flag.wait(0.02)  # ~50 Hz poll: stop-responsive
            finally:
                if self._escape_keycode:
                    try:
                        root.ungrab_key(self._escape_keycode, X.AnyModifier)
                    except Exception:
                        pass
                try:
                    self._grab(keycode)  # re-arm the dictation hotkey
                    self._settle_grabs(keycode)
                except Exception:
                    pass
        finally:
            try:
                d.ungrab_keyboard(X.CurrentTime)  # release escape activation
            except Exception:
                pass
        return aborted

    # Upstream HotkeyActivationMode "Automatic": tap-vs-hold disambiguation
    # window. A release inside the window is a toggle tap; a key still down
    # at the deadline starts push-to-talk until release.
    BOTH_TAP_S = 0.25

    def _both_cycle(self, d: Display, keycode: int) -> None:
        """'both' activation mode: a quick tap toggles dictation on/off,
        holding the key talks (push-to-talk until release). Same native
        passthrough + Escape-cancel semantics as hold mode; the keyboard
        is freed for the decision window too, so a held key's repeats
        reach the app either way."""
        self._free_keyboard(d, keycode)
        try:
            deadline = time.monotonic() + self.BOTH_TAP_S
            tap = False
            aborted = False
            while not self._stop_flag.is_set():
                if not self._hotkey_still_down(d, keycode):
                    tap = True
                    break
                if time.monotonic() >= deadline:
                    break  # still down past the window -> hold
                try:
                    if d.pending_events():
                        event = d.next_event()
                        if classify_hold_event(
                                getattr(event, "type", None),
                                getattr(event, "detail", None),
                                keycode,
                                self._escape_keycode) == _HOLD_ABORT:
                            aborted = True
                            break
                except Exception:
                    break
                self._stop_flag.wait(0.01)
            if aborted:
                self._safe(self.on_cancel)
            elif tap:
                self._safe(self.on_toggle)  # plain toggle tap
            else:
                self._safe(self.on_toggle)  # hold: start talking...
                if self._hold_until_release(d, keycode):
                    self._safe(self.on_cancel)
                else:
                    self._safe(self.on_toggle)  # ...stop and transcribe
        finally:
            try:
                self._grab(keycode)  # re-arm after the decision window
                self._settle_grabs(keycode)
            except Exception:
                pass

    def _safe(self, cb) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass


class MousePTTListener:
    """Mouse push-to-talk via XGrabButton + XI2 raw release events.

    Mirrors HotkeyListener hold mode with the pointer twin of each
    mechanism (upstream macOS PR #939 parity): press -> on_toggle (start)
    via a passive core button grab over every lock-mask combo
    (owner_events=False, GrabModeAsync); the grab activation is released
    with ungrab_pointer immediately so CLICKS during the hold pass through
    to the window under the pointer natively - and the passive grab
    SURVIVES that ungrab (buttons have no auto-repeat, unlike keys, so no
    re-arm dance is needed on hold end). Release -> on_toggle (stop &
    transcribe), detected from XI2 RawButtonRelease events on all master
    pointers: core XQueryPointer's CARD16 mask only carries buttons 1-5
    and cannot see the canonical thumb buttons (8/9), and raw events are
    grab-independent and non-consuming. Escape while holding -> on_cancel
    via a passive key grab armed just for the hold (the keyboard twin's
    cancel-during-hold design). Small helpers duplicate the keyboard
    twin's battle-tested machinery on purpose - do not refactor one to
    serve both; keycodes and buttons entangle differently."""

    def __init__(self, button: int, modifiers: list[str], on_toggle,
                 on_cancel=None, cancel_key: str | None = "Escape",
                 display_name: str | None = None,
                 log=None, on_grab_change=None):
        self.button = button
        self.on_toggle = on_toggle
        self.on_cancel = on_cancel
        self.cancel_key = cancel_key
        self.display_name = display_name
        self.log = log or (lambda m: print(f"[sayit-ermano] {m}", flush=True))
        self._on_grab_change = on_grab_change or (lambda healthy: None)
        self._mods = sum(MODIFIER_MASKS.get(m, 0) for m in modifiers)
        self.modifiers = list(modifiers)
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._display: Display | None = None
        self._cancel_keycode: int | None = None
        self._escape_keycode: int | None = None
        self._want_cancel = False   # recording active -> grab the cancel key
        self._cancel_grabbed = False
        self._cancel_grab_warned = False
        self._holding = False       # a hold cycle is in progress
        self._released = False      # raw release seen for the held button
        # Grab health per (button/keycode, full modifier mask) combo - the
        # same refusal-as-data contract as the keyboard twin (python-xlib
        # never raises BadAccess through grab_*; the per-request onerror
        # closure flips the combo missing, _sync_button_grab retries).
        self._combo_ok: dict[tuple[int, int], bool] = {}
        self._combo_attempts: dict[tuple[int, int], int] = {}
        self._pending: set[tuple[int, int]] = set()
        self._refuse_warned = False
        self._was_healthy: bool | None = None
        self._summary: list[str] = []

    # -- setup ---------------------------------------------------------------

    def _keycode_for(self, keysym: int) -> int:  # the keyboard twin's helper
        assert self._display is not None
        code = self._display.keysym_to_keycode(keysym)
        return code or 0

    def _negotiate_xi(self, d) -> bool:
        """XI 2.2 as the connection's FIRST XI2 request; True when the
        server negotiates >= 2.1 (below that, RawButtonRelease is never
        delivered and release detection would silently never fire)."""
        try:
            if not d.has_extension(_xi_ext.extname):
                return False
            reply = _xi_query_version(d.display, 2, 2)
            major, minor = int(reply.major_version), int(reply.minor_version)
        except Exception:
            return False
        return (major, minor) >= (2, 1)

    def _make_grab_onerror(self, combo: tuple[int, int]):
        """Per-request error handler for one grab combo (the keyboard
        twin's contract): trivial, records data, returns truthy."""
        def _onerror(_error, _request) -> int:
            self._combo_ok[combo] = False
            self._pending.discard(combo)
            attempts = self._combo_attempts.get(combo, 0) + 1
            self._combo_attempts[combo] = attempts
            if attempts >= _MAX_GRAB_ATTEMPTS and not self._refuse_warned:
                self._refuse_warned = True
                self.log(f"WARN mouse PTT grab still refused after "
                         f"{attempts} attempts - held by another client?")
            return 1  # handled: suppress the printing default handler
        return _onerror

    def _grab_button(self, masks=None) -> None:
        """Issue one button grab per lock-mask combo, routing refusals to
        data (the keyboard twin's _grab contract, pointer edition)."""
        assert self._display is not None
        if masks is None:
            masks = [self._mods | extra for extra in _LOCK_MASKS]
        root = self._display.screen().root
        for mask in masks:
            combo = (self.button, mask)
            self._combo_ok[combo] = True
            self._pending.add(combo)
            root.grab_button(self.button, mask, False,
                             X.ButtonPressMask | X.ButtonReleaseMask,
                             X.GrabModeAsync, X.GrabModeAsync,
                             X.NONE, X.NONE,
                             onerror=self._make_grab_onerror(combo))

    def _grab_key(self, keycode: int, masks=None) -> None:
        """Cancel-key grab during a recording started some other way - the
        keyboard twin's _grab, duplicated here for the mouse connection."""
        assert self._display is not None
        if masks is None:
            masks = [self._mods | extra for extra in _LOCK_MASKS]
        root = self._display.screen().root
        for mask in masks:
            combo = (keycode, mask)
            self._combo_ok[combo] = True
            self._pending.add(combo)
            root.grab_key(keycode, mask, False,
                          X.GrabModeAsync, X.GrabModeAsync,
                          onerror=self._make_grab_onerror(combo))

    def _settle_grabs(self, code: int, masks=None) -> None:
        """Pump errors for recently issued grabs (keyboard twin contract)."""
        if masks is None:
            masks = [self._mods | extra for extra in _LOCK_MASKS]
        try:
            self._display.sync()
        except Exception:
            return  # display dying: leave pending (reads as unresolved)
        for mask in masks:
            self._pending.discard((code, mask))

    @property
    def button_grabbed(self) -> bool:
        """True = all lock-mask combos of the button are believed held.
        Partial coverage reads False; in-flight grabs never count (the
        keyboard twin's hotkey_grabbed semantics)."""
        return all(
            self._combo_ok.get((self.button, self._mods | extra), False)
            and (self.button, self._mods | extra) not in self._pending
            for extra in _LOCK_MASKS)

    def _sync_button_grab(self) -> None:
        """Self-heal refused button combos on the poll-loop cadence - the
        pointer twin of _sync_hotkey_grab. A healthy grab issues zero extra
        X traffic; a refused one retries every tick (~1 s worst case to
        re-take after a holder releases)."""
        try:
            if self._display is None:
                return
            missing = [self._mods | extra for extra in _LOCK_MASKS
                       if not self._combo_ok.get(
                           (self.button, self._mods | extra), False)
                       or (self.button, self._mods | extra) in self._pending]
            if not missing:
                return  # healthy: nothing to do, no X traffic
            healthy_before = self.button_grabbed
            self._grab_button(masks=missing)
            self._settle_grabs(self.button, masks=missing)
            healthy_after = self.button_grabbed
            if healthy_after != healthy_before:
                try:
                    self._on_grab_change(healthy_after)
                except Exception:
                    pass
            if (healthy_after and not healthy_before
                    and self._was_healthy is not None):
                self.log("mouse PTT grab recovered")
                for extra in _LOCK_MASKS:
                    self._combo_attempts.pop((self.button, self._mods | extra), None)
                self._refuse_warned = False
            self._was_healthy = healthy_after
        except Exception:
            pass  # a display closing under stop() must not kill the loop

    def _resolve_cancel(self) -> str:
        """Config value -> keysym name (the keyboard twin's semantics:
        None/"" = the Escape default, "none"/"off" disables)."""
        raw = "" if self.cancel_key is None else self.cancel_key.strip()
        if not raw:
            return DEFAULT_CANCEL_KEY
        if raw.lower() in ("none", "off", "disabled"):
            return ""
        return raw

    def setup(self) -> list[str]:
        try:
            self._display = Display(self.display_name)
        except Exception as e:
            raise HotkeyError(f"cannot open X display ({e}) - is this an X11 session?") from e
        if not self._negotiate_xi(self._display):
            raise HotkeyError(
                "X server does not deliver raw button releases "
                "(XI extension missing or < 2.1) - mouse push-to-talk "
                "cannot detect the button release")
        root = self._display.screen().root
        # raw events on ALL master pointers: non-consuming, delivered even
        # while the pointer is grabbed by someone else, immune to
        # auto-repeat (buttons do not repeat). Raw presses are filtered.
        root.xinput_select_events([(_xi_ext.AllMasterDevices,
                                    _xi_ext.RawButtonPressMask
                                    | _xi_ext.RawButtonReleaseMask)])
        cancel = self._resolve_cancel()
        self._cancel_keycode = self._keycode_for(resolve_keysym(cancel)) \
            if cancel else None
        self._escape_keycode = self._keycode_for(XK.XK_Escape)
        self._sync_button_grab()
        if not self.button_grabbed:
            # another client holds some combos; the daemon owns the startup
            # WARN - the listener's cap-WARN stays quiet (twin contract)
            self._refuse_warned = True
        self._display.sync()
        self._summary = [f"mouse PTT button {self.button} (XGrabButton), "
                         f"modifiers {self._mods:#x}"
                         + (f", cancel {cancel} while holding" if cancel
                            else ", cancel disabled")]
        return self._summary

    # -- loop ----------------------------------------------------------------

    def start(self) -> None:
        if self._thread:
            return
        self.setup()
        self._thread = threading.Thread(target=self._run,
                                        name="fluidvoice-mouse-ptt",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._display:
            try:
                self._display.close()
            except Exception:
                pass

    @property
    def summary(self) -> list[str]:
        return list(self._summary)

    def _run(self) -> None:  # pragma: no cover - needs a real X server
        d = self._display
        assert d is not None
        try:
            while not self._stop_flag.is_set():
                self._sync_button_grab()
                self._sync_cancel_grab()
                if d.pending_events() == 0:
                    self._stop_flag.wait(0.01)
                    continue
                try:
                    event = d.next_event()
                except Exception:
                    break
                verdict = self._classify_idle_event(event)
                if verdict == "cancel":
                    self._safe(self.on_cancel)
                elif verdict == "hold":
                    self._hold_cycle(d)
                # everything else (raw presses/releases, other buttons,
                # motions) is ignored at idle
        finally:
            try:
                d.close()
            except Exception:
                pass

    def _classify_idle_event(self, event) -> str:
        """Pure: how the idle loop treats one event. GenericEvents have no
        .detail/.window attributes - never getattr those without a guard
        (they arrive unregistered, see parse_raw_button_event)."""
        evtype = getattr(event, "evtype", None)
        if evtype is not None:
            return "ignore"  # raw XI2 events only matter mid-hold
        etype = getattr(event, "type", None)
        detail = getattr(event, "detail", None)
        if etype == X.KeyPress and detail is not None \
                and self._cancel_keycode and detail == self._cancel_keycode:
            return "cancel"
        if etype == X.ButtonPress and detail == self.button \
                and not self._holding:
            return "hold"
        return "ignore"

    def set_recording(self, active: bool) -> None:
        """Tell the listener dictation started/stopped (the keyboard
        twin's contract): the cancel key is grabbed only while recording."""
        self._want_cancel = bool(active)
        if not active:
            self._refuse_warned = False  # new idle period may WARN again

    def _sync_cancel_grab(self) -> None:
        """Arm/disarm the cancel-key grab (keyboard twin contract; a
        recording started by the keyboard hotkey or the tray still gets
        Escape-to-cancel through this connection)."""
        if self._display is None or not self._cancel_keycode:
            return
        if self._want_cancel == self._cancel_grabbed:
            return
        root = self._display.screen().root
        try:
            if self._want_cancel:
                self._grab_key(self._cancel_keycode)
                self._settle_grabs(self._cancel_keycode)
            else:
                root.ungrab_key(self._cancel_keycode, X.AnyModifier)
                self._display.sync()
            self._cancel_grabbed = self._want_cancel
            self._cancel_grab_warned = False
        except Exception:
            if self._want_cancel and not self._cancel_grab_warned:
                self._cancel_grab_warned = True
                self.log(f"WARN cancel key '{self.cancel_key}' "
                         "grab failed - held by another client?")
            # best-effort; cancel via CLI still works

    def _classify_hold_event(self, event) -> str:
        """Pure: how _hold_cycle treats one event seen during a mouse hold
        (the pointer twin of classify_hold_event)."""
        evtype = getattr(event, "evtype", None)
        if evtype is not None:  # GenericEvent: raw XI2
            if evtype == _XI_RAW_BUTTON_RELEASE:
                parsed = parse_raw_button_event(getattr(event, "data", None))
                if parsed and parsed[1] == self.button:
                    return _HOLD_END
            return _HOLD_IGNORE
        etype = getattr(event, "type", None)
        detail = getattr(event, "detail", None)
        if etype == X.KeyPress and detail is not None \
                and self._escape_keycode is not None \
                and detail == self._escape_keycode:
            return _HOLD_ABORT
        if etype == X.ButtonPress and detail == self.button:
            # the same button pressed again mid-hold by a second pointer:
            # the surviving passive grab re-activated - tolerate it and
            # ungrab again so the click still passes through
            return _MOUSE_HOLD_REPRESS
        return _HOLD_IGNORE

    def _hold_cycle(self, d) -> None:
        """Mouse push-to-talk hold with native click passthrough.

        The XGrabButton passive grab that fired this hold activates a FULL
        pointer grab for the button's press-to-release duration (X11
        semantics). This cycle RELEASES that activation immediately
        (ungrab_pointer), so every other click flows to the window under
        the pointer natively: real events, no injection. The held button's
        release is detected from XI2 RawButtonRelease events delivered to
        the same connection; a passive Escape grab is armed just for the
        hold so Escape still cancels the recording (upstream semantics: a
        cancelled recording is discarded, not transcribed). Unlike the
        keyboard twin there is no re-arm dance on the way out: the passive
        button grab SURVIVES the activation ungrab (buttons have no
        auto-repeat). If the pointer device vanishes mid-hold (USB unplug)
        no release ever arrives - the daemon's max_seconds watchdog ends
        the take and the listener stays healthy."""
        root = d.screen().root
        self._holding = True
        self._released = False
        aborted = False
        try:
            self._safe(self.on_toggle)  # start
            # Free the pointer: the passive grab's activation holds it.
            try:
                d.ungrab_pointer(X.CurrentTime)
            except Exception:
                pass  # best-effort: worst case clicks stay swallowed
            # Escape still cancels: arm a passive grab for the hold only
            if self._escape_keycode:
                try:
                    root.grab_key(self._escape_keycode, X.AnyModifier, False,
                                  X.GrabModeAsync, X.GrabModeAsync,
                                  onerror=lambda _e, _r: 1)  # swallow refusals
                except Exception:
                    pass  # best-effort: cancel via CLI still works
            try:
                while not self._stop_flag.is_set():
                    try:
                        if d.pending_events():
                            event = d.next_event()
                            verdict = self._classify_hold_event(event)
                            if verdict == _HOLD_END:
                                self._released = True
                                break
                            if verdict == _HOLD_ABORT:
                                aborted = True
                                break
                            if verdict == _MOUSE_HOLD_REPRESS:
                                try:
                                    d.ungrab_pointer(X.CurrentTime)
                                except Exception:
                                    pass
                    except Exception:
                        break  # display closed / stop() - end the hold
                    self._stop_flag.wait(0.02)  # ~50 Hz poll: stop-responsive
            finally:
                if self._escape_keycode:
                    try:
                        root.ungrab_key(self._escape_keycode, X.AnyModifier)
                    except Exception:
                        pass
        finally:
            self._holding = False
            try:
                # idempotent; covers the re-press path's re-activation
                d.ungrab_pointer(X.CurrentTime)
            except Exception:
                pass
            try:
                d.ungrab_keyboard(X.CurrentTime)  # release escape activation
            except Exception:
                pass
        if aborted:
            self._safe(self.on_cancel)
        elif self._released:
            self._safe(self.on_toggle)  # stop and transcribe
        # stop() ended the hold: silent end (the daemon is shutting down)

    def _safe(self, cb) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass
