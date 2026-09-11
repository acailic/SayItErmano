"""Integration tests exercise real subsystems: PipeWire recording, CUDA
whisper, a real daemon subprocess with its socket + web UI, packaging, and
the installer download. Run with:  pytest -m integration
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import REAL_SESSION_ENV

REPO = Path(__file__).resolve().parents[2]

# Q2: the root conftest pins XDG_SESSION_TYPE=x11 / clears WAYLAND_DISPLAY
# for the UNIT tier. Real-subsystem tests must see the session they
# actually run in (the Wayland port gates on exactly these vars), so the
# snapshot taken before that pin is restored HERE. Per-test monkeypatch
# overrides keep winning (they run later).
for _var, _val in REAL_SESSION_ENV.items():
    if _val is None:
        os.environ.pop(_var, None)
    else:
        os.environ[_var] = _val

TEST_CONFIG = """\
[general]
language = "en"

[hotkey]
key = "F9"
# Never Escape: the user's live daemon grabs Escape while IT records, and
# two clients cannot both hold it - the test daemon's arm would fail with
# BadAccess whenever a real dictation overlaps the test run.
cancel_key = "F12"

[model]
eager_warmup = false

[recording]
preview_enabled = false
skip_silent = false
first_pcm_timeout = 0

[sounds]
enabled = false

[notifications]
enabled = false

[history]
save = true
"""


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """Fully isolated config/data/socket environment (also for subprocesses)."""
    (tmp_path / "run").mkdir()
    (tmp_path / "data").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(TEST_CONFIG)
    monkeypatch.setenv("SAYITERMANO_CONFIG", str(cfg))
    # keep the real XDG_RUNTIME_DIR (PipeWire lives there); isolate the
    # control socket through its own override instead
    monkeypatch.setenv("SAYITERMANO_SOCKET", str(tmp_path / "run" / "fluidvoice.sock"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # isolate the config dir too, so the test daemon's singleton lock
    # (~/.config/sayit-ermano/daemon.lock) never fights the live daemon
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # test daemons must never launch the GUI (onboarding/tray app spawns)
    monkeypatch.setenv("SAYITERMANO_NO_APP_SPAWN", "1")
    # integration stays offline: no GitHub update probe from the daemon's
    # checker or the CLI/doctor invocations (fluidvoice/update.py kill-switch)
    monkeypatch.setenv("SAYITERMANO_SKIP_UPDATE_CHECK", "1")
    return cfg


def _spawn_and_wait(tmp_path: Path, extra_args: list,
                    log_to: Path | None = None) -> subprocess.Popen:
    from fluidvoice import paths
    # Q2/E7: run THIS checkout's source with the interpreter running the
    # tests (sys.executable), not a venv-pathed script that only exists in
    # the main tree. Isolated worktrees share the venv by policy; `python
    # -m fluidvoice` with cwd=REPO puts this checkout's fluidvoice/ first
    # on sys.path, so the daemon under test is the one beside these tests.
    args = [sys.executable, "-m", "fluidvoice", "daemon", *extra_args]
    if log_to is not None:
        # file mode: daemon log() flushes every line, so the file is already
        # complete without draining a pipe; _stop_daemon skips its rewrite
        with open(log_to, "w") as out:
            proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                    text=True, cwd=str(REPO),
                                    env={**os.environ})
        proc._fv_log_to_file = True
    else:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                cwd=str(REPO), env={**os.environ})
    socket = paths.socket_path()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if socket.exists():
            break
        if proc.poll() is not None:
            if not getattr(proc, "_fv_log_to_file", False):
                (tmp_path / "daemon.log").write_text(proc.stdout.read())
            raise RuntimeError("daemon died at startup, log in %s"
                                % (tmp_path / "daemon.log"))
        time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("daemon socket never appeared")
    return proc


def _stop_daemon(proc: subprocess.Popen, tmp_path: Path) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if getattr(proc, "_fv_log_to_file", False):
        return  # log_to mode: the file was written directly, already complete
    if proc.stdout:
        (tmp_path / "daemon.log").write_text(proc.stdout.read())


@pytest.fixture()
def daemon_process(isolated_env, tmp_path):
    """A real daemon subprocess: socket control + web UI, no hotkey."""
    proc = _spawn_and_wait(tmp_path, ["--no-hotkey", "--no-sounds"])
    yield proc
    _stop_daemon(proc, tmp_path)


@pytest.fixture()
def daemon_with_hotkey(isolated_env, tmp_path):
    """A real daemon with its hotkey grab active (TEST_CONFIG: F9)."""
    proc = _spawn_and_wait(tmp_path, ["--no-sounds"])
    yield proc
    _stop_daemon(proc, tmp_path)


@pytest.fixture()
def daemon_hold_hotkey(isolated_env, tmp_path):
    """A real daemon in hold (push-to-talk) mode with the F9 grab active.
    Same config as daemon_with_hotkey plus mode = "hold" under [hotkey]."""
    isolated_env.write_text(
        isolated_env.read_text().replace('key = "F9"\n', 'key = "F9"\nmode = "hold"\n'))
    proc = _spawn_and_wait(tmp_path, ["--no-sounds"])
    yield proc
    _stop_daemon(proc, tmp_path)


@pytest.fixture()
def daemon_mouse_ptt(isolated_env, tmp_path):
    """A real daemon with mouse push-to-talk armed on button 8 (a spare
    thumb button; 1-5 are click/scroll and refused by validation)."""
    isolated_env.write_text(
        isolated_env.read_text().replace(
            'first_pcm_timeout = 0\n',
            'first_pcm_timeout = 0\npush_to_talk_button = "button8"\n'))
    proc = _spawn_and_wait(tmp_path, ["--no-sounds"])
    yield proc
    # a stray held button poisons later grabs (XTEST state dedup): always
    # mouseup defensively before the daemon goes away
    subprocess.run(["xdotool", "mouseup", "8"], timeout=5,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _stop_daemon(proc, tmp_path)


@pytest.fixture()
def daemon_blocked_hotkey(isolated_env, tmp_path):
    """A real daemon whose F9 grab is refused by a deliberate conflicting
    holder: this fixture pre-grabs ALL lock-mask combos of F9 on the root
    window (the exact masks the daemon will request -> deterministic
    BadAccess x8), then spawns the daemon logging to a file. Yielded handle:
    .proc (the daemon) and .release() (drops the conflicting grabs by
    closing the holder's X connection - X frees its passive grabs)."""
    from Xlib import XK, X
    from Xlib.display import Display

    from fluidvoice.hotkey import _LOCK_MASKS

    holder = Display()
    keycode = holder.keysym_to_keycode(XK.string_to_keysym("F9"))
    assert keycode, "F9 has no keycode on this keymap"
    root = holder.screen().root
    for extra in _LOCK_MASKS:
        root.grab_key(keycode, extra, False, X.GrabModeAsync, X.GrabModeAsync)
    holder.sync()
    proc = _spawn_and_wait(tmp_path, ["--no-sounds"],
                           log_to=tmp_path / "daemon.log")

    class _Handle:
        def __init__(self, display, daemon_proc):
            self._display = display
            self.proc = daemon_proc
            self.released = False

        def release(self):
            if not self.released:
                self.released = True
                try:
                    self._display.close()  # X auto-releases passive grabs
                except Exception:
                    pass

    handle = _Handle(holder, proc)
    yield handle
    handle.release()
    _stop_daemon(proc, tmp_path)


def gpu_free_mb() -> int:
    """Free VRAM in MiB, or -1 when it cannot be determined."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def skip_if_gpu_busy(min_free_mb: int = 1500) -> None:
    free = gpu_free_mb()
    if 0 <= free < min_free_mb:
        pytest.skip(f"GPU busy ({free} MiB free) - real-model transcription "
                    "is covered by the CPU E2E test")


@pytest.fixture(scope="session")
def shared_backend():
    """One loaded whisper model for the whole integration session (an 8 GB
    GPU cannot hold several concurrent instances alongside the user's own
    daemon). Skips cleanly when the GPU is nearly full."""
    skip_if_gpu_busy()
    from fluidvoice import backends
    from fluidvoice.config import load_config
    backend = backends.load_backend(load_config())
    backend.warmup()
    return backend


@pytest.fixture(scope="session")
def jfk_wav(tmp_path_factory) -> Path:
    """JFK sample as a 16 kHz mono WAV (downloaded once per session)."""
    import urllib.request
    flac = tmp_path_factory.mktemp("audio") / "jfk.flac"
    with urllib.request.urlopen(
            "https://github.com/openai/whisper/raw/main/tests/jfk.flac",
            timeout=60) as resp:
        flac.write_bytes(resp.read())
    wav = flac.with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(flac),
                    "-ar", "16000", "-ac", "1", str(wav)], check=True)
    return wav
