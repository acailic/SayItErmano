"""Live X11 integration: real hotkey grab + synthetic keypress, and the
overlay window rendering (pixel-verified like the manual proof)."""
import os
import subprocess
import time

import pytest

from fluidvoice import control

# `desktop` layer: these verify against the LIVE interactive session (real
# key grabs, real screen pixels) and can flake while the machine is in use.
# Deterministic default runs exclude them:  pytest -m "not desktop"
pytestmark = [pytest.mark.integration, pytest.mark.desktop]


def _requires_x11():
    has_display = bool(os.environ.get("DISPLAY"))
    has_xdotool = subprocess.run(["which", "xdotool"],
                                 capture_output=True).returncode == 0
    return pytest.mark.skipif(not (has_display and has_xdotool),
                              reason="requires DISPLAY and xdotool")


requires_x11 = _requires_x11()


@requires_x11
class TestHotkeyLive:
    def test_synthetic_hotkey_toggles_recording(self, daemon_with_hotkey):
        # TEST_CONFIG grabs F9 (never Right_Control: the user's own daemon
        # may hold that grab on a live session). Retry: a previous test
        # daemon's X connection can take a moment to release the grab.
        recording = False
        for _ in range(3):
            subprocess.run(["xdotool", "key", "F9"], check=True, timeout=5)
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                if control.request("status")["recording"]:
                    recording = True
                    break
                time.sleep(0.15)
            if recording:
                break
        assert recording, "F9 grab did not fire within 3 attempts"
        control.request("cancel")
        assert control.request("status")["recording"] is False

    def test_escape_cancels_recording(self, daemon_with_hotkey):
        # macOS parity: the cancel key while dictating discards it (the grab
        # exists only during recording, so an idle cancel key is never
        # swallowed). TEST_CONFIG uses F12, not Escape: the user's live
        # daemon holds the Escape grab whenever it is recording, and two
        # clients cannot both grab the same key.
        # Retry like above: a previous test daemon may still hold the grab.
        recording = False
        for _ in range(3):
            subprocess.run(["xdotool", "key", "F9"], check=True, timeout=5)
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline:
                if control.request("status")["recording"]:
                    recording = True
                    break
                time.sleep(0.15)
            if recording:
                break
        assert recording, "F9 grab did not fire within 3 attempts"
        time.sleep(0.3)  # let the poll loop establish the cancel grab
        subprocess.run(["xdotool", "key", "F12"], check=True, timeout=5)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            status = control.request("status")
            if not status["recording"]:
                break
            time.sleep(0.15)
        status = control.request("status")
        assert status["recording"] is False
        assert status["ok"] is True  # daemon healthy after the cancel

    def test_daemon_survives_rapid_toggles(self, daemon_process):
        for _ in range(5):
            control.request("toggle")
            time.sleep(0.05)
        control.request("cancel")
        time.sleep(0.3)
        assert control.request("status")["ok"]


@requires_x11
class TestHoldPassthroughLive:
    """Push-to-talk (hold mode): keys typed during the hold must reach the
    focused app (replayed through the daemon's keyboard grab), the ~30 Hz
    auto-repeat pairs of the held hotkey must not end the hold early, and
    releasing the hotkey completes the dictation (stop, not cancel)."""

    def _status(self):
        try:
            return control.request("status")
        except Exception:
            return {}

    def test_typing_during_hold_reaches_app_and_recording_completes(
            self, daemon_hold_hotkey):
        from tests.integration.conftest import skip_if_gpu_busy
        skip_if_gpu_busy()  # the take is transcribed (real model) after the hold
        from Xlib import XK, X
        from Xlib.display import Display

        d = Display()
        win = None
        prev_focus = None
        try:
            root = d.screen().root
            # receiver window (probe pattern): override-redirect, key events
            win = root.create_window(
                10, 10, 240, 120, 1, X.CopyFromParent, X.InputOutput,
                X.CopyFromParent, override_redirect=True,
                event_mask=X.KeyPressMask | X.KeyReleaseMask)
            win.map()
            d.sync()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if win.get_attributes().map_state == X.IsViewable:
                    break
                time.sleep(0.05)
            assert win.get_attributes().map_state == X.IsViewable
            prev_focus = d.get_input_focus().focus
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
            d.sync()

            # 1. keydown F9 -> push-to-talk hold starts (retry: a previous
            #    test daemon's X connection may still hold the F9 grab, and
            #    its release can lag the new daemon's startup)
            recording = False
            for _ in range(4):
                subprocess.run(["xdotool", "keydown", "F9"], check=True, timeout=5)
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    if self._status().get("recording"):
                        recording = True
                        break
                    time.sleep(0.15)
                if recording:
                    break
                subprocess.run(["xdotool", "keyup", "F9"], timeout=5)
                time.sleep(0.5)
            assert recording, "hold-mode F9 grab did not start recording"

            try:
                # 2. type while holding: the daemon's grab swallows the XTEST
                #    chars and replays them to the focused window; the held
                #    F9 auto-repeats at ~30 Hz, which must NOT end the hold
                subprocess.run(["xdotool", "type", "--delay", "60", "hi"],
                               check=True, timeout=10)
                time.sleep(0.4)
                assert self._status().get("recording") is True, \
                    "hold ended while typing (auto-repeat / replay race)"

                # 3. release F9 -> stop path: recording ends, daemon healthy
                subprocess.run(["xdotool", "keyup", "F9"], check=True, timeout=5)
                deadline = time.monotonic() + 10
                stopped = False
                while time.monotonic() < deadline:
                    status = self._status()
                    if status and not status.get("recording") and status.get("ok"):
                        stopped = True
                        break
                    time.sleep(0.15)
                assert stopped, "hold did not end on F9 release"

                # 4. drain the receiver window: the typed chars arrived as
                #    REAL (send_event False) keystrokes - XTEST replay, not
                #    the XSendEvent fallback. Focus stays on the receiver so
                #    the daemon's post-transcription insertion lands there.
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and d.pending_events() < 4:
                    time.sleep(0.05)
                real_presses = set()
                while d.pending_events():
                    ev = d.next_event()
                    if ev.type == X.KeyPress and not ev.send_event:
                        real_presses.add(ev.detail)
                want = {d.keysym_to_keycode(XK.string_to_keysym(c)) for c in "hi"}
                assert want <= real_presses, \
                    f"typed chars did not reach the focused app: " \
                    f"wanted {want} among real presses, got {real_presses}"

                # best-effort: let the take transcribe while focus is our
                # harmless receiver window (no assertion - GPU timing)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    status = self._status()
                    if status and not status.get("recording") \
                            and not status.get("busy"):
                        break
                    time.sleep(0.5)
            finally:
                subprocess.run(["xdotool", "keyup", "F9"], timeout=5)
                try:
                    control.request("cancel")
                except Exception:
                    pass
        finally:
            if win is not None:
                try:
                    if prev_focus is not None:
                        prev_focus.set_input_focus(X.RevertToParent, X.CurrentTime)
                except Exception:
                    pass
                try:
                    win.unmap()
                    win.destroy()
                    d.sync()
                except Exception:
                    pass
            d.close()


@requires_x11
class TestHotkeyGrabRecovery:
    """Self-healing (live incident 2026-09-04): a second client holding
    the hotkey must leave the daemon loudly blocked (WARN + status false),
    and within ~1 s of that holder letting go the grab is re-taken and
    actually fires - no restart."""

    def test_conflicting_holder_blocks_then_recovery(self,
                                                     daemon_blocked_hotkey,
                                                     tmp_path):
        log_path = tmp_path / "daemon.log"
        # 1. blocked: the daemon must never sit "ready" while keyless
        deadline = time.monotonic() + 2
        status = {}
        while time.monotonic() < deadline:
            status = control.request("status")
            if status.get("hotkey_grabbed") is False:
                break
            time.sleep(0.1)
        assert status.get("hotkey_grabbed") is False, \
            "status should report the refused grab"
        log_text = log_path.read_text()
        assert "grab refused - held by another client, will retry" in log_text

        # 2. the conflicting holder lets go -> grab re-taken (expected well
        #    under 1 s on the ~10 ms retry cadence; slack for slow CI)
        daemon_blocked_hotkey.release()
        deadline = time.monotonic() + 5
        recovered = False
        while time.monotonic() < deadline:
            if control.request("status").get("hotkey_grabbed") is True:
                recovered = True
                break
            time.sleep(0.05)
        assert recovered, "grab not re-taken within 5 s of holder release"
        assert "hotkey grab recovered" in log_path.read_text()

        # 3. the re-taken grab actually fires (not just believed healthy)
        subprocess.run(["xdotool", "key", "F9"], check=True, timeout=5)
        recording = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if control.request("status")["recording"]:
                recording = True
                break
            time.sleep(0.1)
        assert recording, "re-taken F9 grab did not toggle recording"
        control.request("cancel")
        assert control.request("status")["recording"] is False


@requires_x11
class TestSelectionHoldLive:
    """SelectionHold against the live X server: a background reader is
    observed by wait_read from a NEW window (the paste-verify signal), and
    hygiene markers keep the flashed dictation out of CopyQ's history
    (live-verified during planning; re-verifiable here)."""

    def test_background_reader_observed(self):
        from fluidvoice.selection import SelectionHold
        hold = SelectionHold(b"FV-ITEST-DICTATION-5d2c")
        try:
            known = hold.quiesce(0.3)  # eager managers reveal themselves
            # non-blocking on purpose: the hold's event loop must never
            # block on a subprocess (probe deadlock lesson)
            proc = subprocess.Popen(["xclip", "-o", "-selection", "clipboard"],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            reader = hold.wait_read(1.0, exclude_windows=known)
            out, _ = proc.communicate(timeout=5)
            assert reader is not None, "the xclip -o read was not observed"
            assert reader not in known  # a window quiesce did not see
            assert out == b"FV-ITEST-DICTATION-5d2c"
        finally:
            hold.release()

    def test_hygiene_markers_suppress_copyq_history(self):
        import shutil
        if not shutil.which("copyq"):
            pytest.skip("copyq not installed")
        before = subprocess.run(["copyq", "read", "0"], capture_output=True,
                                timeout=5)
        if before.returncode != 0:
            pytest.skip("copyq not running")
        from fluidvoice.insertion import HYGIENE_TARGETS
        from fluidvoice.selection import SelectionHold
        hold = SelectionHold(b"FV-ITEST-SECRET-MARKER-8a1f", HYGIENE_TARGETS)
        try:
            # CopyQ's monitor re-checks at ~+0.03/+0.09/+0.25/+0.60 s after
            # an ownership change - quiesce long enough to cover the ladder
            hold.quiesce(0.7)
        finally:
            hold.release()
        time.sleep(0.2)
        after = subprocess.run(["copyq", "read", "0"], capture_output=True,
                               timeout=5)
        assert b"FV-ITEST-SECRET-MARKER-8a1f" not in after.stdout
        assert after.stdout == before.stdout  # top of history unchanged


@requires_x11
class TestOverlayLive:
    def test_pill_overlay_renders_text_pixels(self):
        from PIL import Image

        from fluidvoice.overlay import BOTTOM_OFFSET, PILL_H, FluidOverlay
        overlay = FluidOverlay()
        assert overlay.using_overlay, "expected the X11 pill overlay on this display"
        try:
            overlay.start()
            overlay.show("INTEGRATION TEST 12345")
            time.sleep(0.8)
            geom = subprocess.run(["xdotool", "getdisplaygeometry"],
                                  capture_output=True, text=True, timeout=5)
            w, h = map(int, geom.stdout.split())
            shot = "/tmp/fv-overlay-itest.png"
            subprocess.run(["import", "-window", "root", shot], timeout=10)
            img = Image.open(shot).convert("L")
            # the pill floats bottom-center, BOTTOM_OFFSET above the screen edge
            cy = h - BOTTOM_OFFSET - PILL_H // 2
            dark = sum(1 for dx in range(-80, 80, 4)
                       if img.getpixel((w // 2 + dx, cy)) < 60)
            light = sum(1 for x in range(w // 2 - 260, w // 2 + 260, 3)
                        for y in range(cy - 60, cy + 20, 3)
                        if img.getpixel((x, y)) > 150)
            assert dark > 20, "pill (dark band) not visible at bottom center"
            assert light > 20, "no bright text/bar pixels inside the pill"
        finally:
            overlay.close()


@requires_x11
class TestMousePTTLive:
    """Mouse push-to-talk against the live X server (XGrabButton press +
    XI2 raw release, upstream macOS PR #939 parity). Every XTEST fake is
    balanced: teardowns always mouseup 8 - a stray held button silently
    poisons later grabs (state-matching fakes are dropped by Xorg)."""

    def _status(self):
        try:
            return control.request("status")
        except Exception:
            return {}

    def _wait(self, predicate, timeout, interval=0.1):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self._status()):
                return True
            time.sleep(interval)
        return False

    def _mouse_up_8(self):
        subprocess.run(["xdotool", "mouseup", "8"], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _pointer_pos(self):
        out = subprocess.run(["xdotool", "getmouselocation", "--shell"],
                             capture_output=True, text=True, timeout=5).stdout
        pos = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                pos[k] = int(v)
        return pos.get("X", 0), pos.get("Y", 0)

    def test_hold_passthrough_and_release(self, daemon_mouse_ptt):
        from tests.integration.conftest import skip_if_gpu_busy
        skip_if_gpu_busy()  # the take is transcribed (real model) at release
        from Xlib import X
        from Xlib.display import Display

        # 1. arm: retry window - a previous test daemon's grabs can release
        #    lazily after its X connection drops
        assert self._wait(lambda s: s.get("mouse_ptt_grabbed") is True, 10), \
            "button 8 grab never became healthy"

        d = Display()
        win = None
        prev_focus = prev_pointer = None
        try:
            root = d.screen().root
            # receiver window (probe pattern): override-redirect, button events
            win = root.create_window(
                200, 200, 240, 120, 1, X.CopyFromParent, X.InputOutput,
                X.CopyFromParent, override_redirect=True,
                event_mask=X.ButtonPressMask | X.ButtonReleaseMask)
            win.map()
            d.sync()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if win.get_attributes().map_state == X.IsViewable:
                    break
                time.sleep(0.05)
            assert win.get_attributes().map_state == X.IsViewable
            prev_focus = d.get_input_focus().focus
            prev_pointer = self._pointer_pos()
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
            subprocess.run(["xdotool", "mousemove", "260", "240"],
                           check=True, timeout=5)
            d.sync()

            # 2. mousedown 8 -> hold starts (retry: lazy grab release)
            recording = False
            for _ in range(3):
                subprocess.run(["xdotool", "mousedown", "8"], check=True,
                               timeout=5)
                if self._wait(lambda s: s.get("recording"), 1.5, 0.1):
                    recording = True
                    break
                self._mouse_up_8()
                time.sleep(0.5)
            assert recording, "button 8 grab did not start recording"

            try:
                # the hold's ungrab_pointer landed: clicks reach the window
                # under the pointer as REAL events (send_event False).
                # Re-park first: this is the LIVE session - the operator's
                # physical mouse can move the pointer mid-hold (desktop-
                # layer flake), and the click lands wherever it is.
                time.sleep(0.2)
                subprocess.run(["xdotool", "mousemove", "260", "240"],
                               check=True, timeout=5)
                subprocess.run(["xdotool", "click", "1"], check=True,
                               timeout=5)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and d.pending_events() < 2:
                    time.sleep(0.05)
                clicks = []
                while d.pending_events():
                    ev = d.next_event()
                    if ev.type in (X.ButtonPress, X.ButtonRelease) \
                            and not ev.send_event:
                        clicks.append((ev.type, ev.detail))
                assert (X.ButtonPress, 1) in clicks \
                    and (X.ButtonRelease, 1) in clicks, \
                    f"click did not reach the window under the pointer: {clicks}"

                # 3. mouseup 8 -> release detected (raw XI2): stop & transcribe
                subprocess.run(["xdotool", "mouseup", "8"], check=True,
                               timeout=5)
                assert self._wait(
                    lambda s: not s.get("recording") and s.get("ok"), 10), \
                    "hold did not end on button release"
            finally:
                self._mouse_up_8()
                try:
                    control.request("cancel")
                except Exception:
                    pass

            # best-effort: let the take transcribe while focus is the
            # harmless receiver window (no assertion - GPU timing)
            self._wait(lambda s: not s.get("recording") and not s.get("busy"),
                       30, 0.5)
        finally:
            self._mouse_up_8()
            if prev_pointer is not None:
                subprocess.run(["xdotool", "mousemove", str(prev_pointer[0]),
                                str(prev_pointer[1])], timeout=5)
            if win is not None:
                try:
                    if prev_focus is not None:
                        prev_focus.set_input_focus(X.RevertToParent,
                                                   X.CurrentTime)
                except Exception:
                    pass
                try:
                    win.unmap()
                    win.destroy()
                    d.sync()
                except Exception:
                    pass
            d.close()

    def test_escape_cancels_hold(self, daemon_mouse_ptt):
        assert self._wait(lambda s: s.get("mouse_ptt_grabbed") is True, 10)
        try:
            subprocess.run(["xdotool", "mousedown", "8"], check=True,
                           timeout=5)
            assert self._wait(lambda s: s.get("recording"), 2), \
                "button 8 grab did not start recording"
            time.sleep(0.3)  # the hold's Escape grab must be established
            subprocess.run(["xdotool", "key", "Escape"], check=True, timeout=5)
            assert self._wait(
                lambda s: not s.get("recording") and s.get("ok"), 3), \
                "Escape did not cancel the mouse hold"
        finally:
            self._mouse_up_8()
            try:
                control.request("cancel")
            except Exception:
                pass

    def _close_quietly(self, display):
        try:
            display.close()
        except Exception:
            pass

    def test_blocked_arm_then_recovery(self, daemon_mouse_ptt, tmp_path):
        from Xlib import X
        from Xlib.display import Display

        from fluidvoice.hotkey import _LOCK_MASKS
        from tests.integration.conftest import _spawn_and_wait, _stop_daemon

        # 1. tear the fixture's daemon down FIRST (it holds the button-8
        #    grabs); its X connection drop frees them
        daemon_mouse_ptt.terminate()
        try:
            daemon_mouse_ptt.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon_mouse_ptt.kill()
            daemon_mouse_ptt.wait(timeout=5)
        # 2. pre-hold EVERY lock-mask combo of button 8, then spawn: the
        #    daemon must report the arm blocked (grab refusal as data)
        holder = Display()
        for extra in _LOCK_MASKS:
            holder.screen().root.grab_button(
                8, extra, False,
                X.ButtonPressMask | X.ButtonReleaseMask,
                X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE)
        holder.sync()
        log_path = tmp_path / "blocked.log"
        proc = _spawn_and_wait(tmp_path, ["--no-sounds"], log_to=log_path)
        try:
            assert self._wait(lambda s: s.get("mouse_ptt_grabbed") is False,
                              5), "arm should report BLOCKED while held"
            assert "grab refused" in log_path.read_text()
            # 3. holder lets go (X frees passive grabs on disconnect) ->
            #    the ~10ms retry loop re-takes within ~1 s
            self._close_quietly(holder)
            assert self._wait(
                lambda s: s.get("mouse_ptt_grabbed") is True, 5), \
                "arm did not recover after the holder released"
            # 4. the re-taken grab actually fires
            subprocess.run(["xdotool", "mousedown", "8"], check=True,
                           timeout=5)
            assert self._wait(lambda s: s.get("recording"), 2)
        finally:
            self._mouse_up_8()
            try:
                control.request("cancel")
            except Exception:
                pass
            self._close_quietly(holder)
            _stop_daemon(proc, tmp_path)
