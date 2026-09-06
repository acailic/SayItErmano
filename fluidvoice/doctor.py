"""`sayit-ermano doctor` - environment report."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from . import __version__, backends, paths
from . import session as session_mod
from .config import load_config


def _gtk_available() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
        return True
    except (ImportError, ValueError):
        return False


def _whispercpp_lines(cfg: dict) -> list[str]:
    """Human-readable whisper.cpp resolution: binary + model path or hint."""
    from . import model_catalog
    binary = backends._whispercpp_binary()
    lines = [f"  binary: {binary or 'not found (install whisper-cli)'}"]
    raw = (cfg.get("model", {}).get("whispercpp_model") or "").strip()
    if not raw:
        lines.append("  model: not set (a catalog name like 'ggml-base.bin' "
                     "or a path — see Settings → Models)")
        return lines
    if "/" in raw or raw.startswith("~"):
        p = Path(raw).expanduser()
        lines.append(f"  model: {p} ({'found' if p.is_file() else 'MISSING'})")
    elif raw in model_catalog.GGUF_CATALOG:
        p = model_catalog.gguf_path(raw)
        lines.append(f"  model: {raw} -> {p} "
                     f"({'downloaded' if p.is_file() else 'not downloaded - get it in Settings -> Models'})")
    else:
        lines.append(f"  model: unknown name '{raw}' "
                     f"(catalog: {', '.join(sorted(model_catalog.GGUF_CATALOG))})")
    have = (sorted(p.name for p in model_catalog.gguf_dir().glob("ggml-*.bin")
                   if p.is_file())
            if model_catalog.gguf_dir().is_dir() else [])
    lines.append("  downloaded ggml models: " + (", ".join(have) if have else "none"))
    return lines


def _parakeet_lines(cfg: dict) -> list[str]:
    """Parakeet (ONNX) report: runtime, providers, per-model download state."""
    from . import model_catalog
    try:
        import onnxruntime as ort
        provs = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                 if p in ort.get_available_providers()]
        where = "CUDA+CPU" if "CUDAExecutionProvider" in provs else "CPU"
        lines = [f"  onnxruntime: {ort.__version__} ({where})"]
    except Exception:
        return ["  onnxruntime: not installed (pip install onnxruntime)"]
    for name, info in model_catalog.PARAKEET_CATALOG.items():
        d = model_catalog.parakeet_model_dir(name)
        if model_catalog.parakeet_downloaded(name):
            lines.append(f"  {name}: downloaded ({d})")
        else:
            missing = ", ".join(f for f in info["files"]
                                 if not (d / f).is_file()) or "incomplete"
            lines.append(f"  {name}: not downloaded — missing: {missing} "
                         "(get it in Settings -> Models)")
    if (cfg.get("model", {}).get("backend") or "") == "parakeet":
        raw = str(cfg.get("model", {}).get("name", "")).strip() or "auto"
        if raw in ("auto", ""):
            raw = model_catalog.PARAKEET_DEFAULT_MODEL
        if raw in model_catalog.PARAKEET_CATALOG:
            mark = ("downloaded" if model_catalog.parakeet_downloaded(raw)
                    else "not downloaded")
            lines.append(f"  active model: {raw} ({mark})")
        else:
            lines.append(f"  active model: unknown name '{raw}' "
                         f"(catalog: "
                         f"{', '.join(sorted(model_catalog.PARAKEET_CATALOG))})")
    return lines


def _formatting_lines(cfg: dict) -> list[str]:
    """Chat/terminal formatting resolution: one line per key."""
    p = cfg.get("processing", {})
    i = cfg.get("insertion", {})
    apps = cfg.get("general", {}).get("terminal_apps") or []
    names = ", ".join(apps) if apps else "none"
    return [
        f"  slash/mention squeeze: "
        f"{'on' if p.get('slash_mention_squeeze', True) else 'off'} "
        f"(processing.slash_mention_squeeze)",
        f"  terminal autocomplete space: "
        f"{'on' if i.get('terminal_autocomplete_space', True) else 'off'} "
        f"(insertion.terminal_autocomplete_space)",
        f"  terminal_apps ({len(apps)}): {names} "
        f"(spoken-send Enter suppressed here)",
    ]


def _command_mode_lines(cfg: dict) -> list[str]:
    """Command mode v2 resolution: AI readiness, tool registry size,
    the destructive-pattern counts (built-in port + user additions), and
    the follow-up context window."""
    from . import command as command_mod
    ready = command_mod.command_mode_ready(cfg)
    ai_line = (f"  ai: not configured ({ready})" if ready else
               f"  ai: ready (model {cfg.get('ai', {}).get('model')})")
    tools = command_mod.TOOL_REGISTRY
    tool_names = ", ".join(sorted(tools))
    built_in = (len(command_mod.DESTRUCTIVE_PREFIXES)
                + len(command_mod.DESTRUCTIVE_PATTERNS))
    user = (cfg.get("command", {}).get("destructive_patterns") or [])
    window = float((cfg.get("command", {}) or {}).get(
        "context_window_s", 300.0))
    ctx_line = ("  context window: disabled (command.context_window_s = 0)"
                if window <= 0 else
                f"  context window: {window:g} s "
                f"(last {command_mod.CommandContextStore().max_entries} "
                "results per app, in-memory only)")
    return [
        ai_line,
        f"  tools: {len(tools)} ({tool_names})",
        f"  destructive patterns: {built_in} built-in + {len(user)} user "
        f"(command.destructive_patterns)",
        ctx_line,
    ]


def _models_cache_lines(cfg: dict) -> list[str]:
    """Models-cache report: one line per cached entry (with the ACTIVE
    marker), a total, and a note that the legacy huggingface/hub location
    is not manageable here."""
    from . import model_catalog
    entries = model_catalog.cached_models()
    lines = [f"models cache: {paths.models_dir()}"]
    active = backends.config_model_key(cfg)
    for e in entries:
        mark = " · ACTIVE" if active and e["name"] == active else ""
        lines.append(f"  {e['kind']} {e['name']} "
                     f"{model_catalog.human_bytes(e['bytes'])}{mark}")
    total = sum(e["bytes"] for e in entries)
    lines.append(f"  total: {len(entries)} model"
                 f"{'' if len(entries) == 1 else 's'}, "
                 f"{model_catalog.human_bytes(total)}")
    lines.append("  note: the legacy huggingface/hub cache "
                 f"({paths.cache_dir().parent / 'huggingface' / 'hub'}) "
                 "is not managed here")
    return lines


def _model_state_lines(cfg: dict) -> list[str]:
    """Idle-unload policy + live model state. The policy comes from the
    config; the loaded/unloaded state and the idle age come from the
    daemon's status surface (the same control-socket query the other
    daemon-live lines use). Daemon down -> the config policy alone."""
    from . import control
    t = int((cfg.get("model", {}) or {}).get("idle_unload_s", 0) or 0)
    try:
        if not paths.socket_path().exists():
            raise FileNotFoundError("no control socket")
        status = control.request("status")
    except Exception:  # noqa: BLE001 - daemon down / older daemon / timeout
        return [f"  model: daemon down (policy from config: {t}s)"]
    ms = status.get("model_state") if isinstance(status, dict) else None
    if not isinstance(ms, dict):
        return [f"  model: unknown (older daemon; policy from config: {t}s)"]
    idle_m = int(float(ms.get("idle_s", 0) or 0) // 60)
    loaded = bool(ms.get("loaded"))
    if t <= 0:
        if loaded:
            return ["  model: loaded (idle unload off)"]
        return ["  model: not loaded (lazy first use)"]
    state = "loaded" if loaded else "unloaded"
    return [f"  model: {state} (idle {idle_m}m; policy {t}s)"]


def _language_lines(cfg: dict) -> list[str]:
    """Language resolution: general.language, per-model overrides, and the
    effective language of the config's active model."""
    from . import model_catalog
    general = str((cfg.get("general", {}) or {}).get("language") or "auto")
    overrides = (cfg.get("model", {}) or {}).get("languages") or {}
    lines = [f"  general: {general} (general.language)"]
    if overrides:
        pretty = ", ".join(f"{k}={v}" for k, v in overrides.items())
        lines.append(f"  per-model overrides: {pretty} (model.languages)")
    else:
        lines.append("  per-model overrides: none (model.languages)")
    key = backends.config_model_key(cfg)
    lines.append(f"  active model {key or '-'} -> "
                 f"{backends.effective_language(cfg)}")
    m = cfg.get("model", {}) or {}
    if str(m.get("backend", "")) == "parakeet" and key:
        langs = model_catalog.PARAKEET_CATALOG.get(key, {}).get("langs", "")
        if langs == "en":
            lines.append(
                f"  note: {key} is English-only - the language code is "
                "recorded but not enforced")
    return lines


def _preview_lines(cfg: dict) -> list[str]:
    """Live-preview resolution: engine kind, window size, VAD auto-stop."""
    r = cfg.get("recording", {}) or {}
    if not r.get("preview_enabled", True):
        return ["  disabled (recording.preview_enabled = false)"]
    m = cfg.get("model", {}) or {}
    backend = str(m.get("backend") or "auto")
    if r.get("preview_segmented", True):
        engine = (f"segmented (constant-cost windows, backend '{backend}') "
                  "- legacy whole-buffer only while the model loads")
        window = f"{float(r.get('preview_segment_s', 2.0)):g} s window, 50% hop"
    else:
        engine = "legacy whole-buffer re-decode (recording.preview_segmented = false)"
        window = None
    vad = float(r.get("preview_vad_silence_s", 2.0))
    lines = [f"  engine: {engine}"]
    if window:
        lines.append(f"  windows: {window} (recording.preview_segment_s)")
    lines.append(
        f"  vad auto-stop: {vad:g} s trailing silence "
        "(recording.preview_vad_silence_s; 0 = off)" if vad > 0 else
        "  vad auto-stop: off (recording.preview_vad_silence_s = 0)")
    return lines


def _insertion_lines(cfg: dict) -> list[str]:
    """Insertion hardening resolution: one line per key."""
    i = cfg.get("insertion", {})
    return [
        f"  paste verification: "
        f"{'on' if i.get('verify_paste', True) else 'off'} "
        f"(insertion.verify_paste)",
        f"  terminal paste key: "
        f"{i.get('terminal_paste_key', 'ctrl+shift+v')} "
        f"(insertion.terminal_paste_key)",
    ]


def _suggestions_line(cfg: dict) -> str:
    """Dictionary-learning report: pending count + the decisions file."""
    from . import history, paths
    from .processing import dict_learn
    try:
        n = len(dict_learn.pending_suggestions(cfg, history.read_all()))
    except Exception as e:  # read-only report; never fails doctor
        return f"  dictionary suggestions: unavailable ({e})"
    return (f"  dictionary suggestions: {n} pending "
            f"({paths.dictionary_suggestions_file()})")


def _history_lines() -> list[str]:
    """History sanity report: entry count, file size, oldest entry date,
    and a warning when test-fingerprint rows (the pre-isolation suite
    pollution) are still present."""
    from . import history, paths
    hpath = paths.history_file()
    lines = [f"history: {hpath}"]
    if not hpath.exists():
        return lines + ["  entries: 0 (no history yet), test rows: 0"]
    entries = history.read_all()
    size_kb = hpath.stat().st_size / 1024
    oldest = next((e.get("ts") for e in entries if e.get("ts")), None)
    when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(oldest))
            if oldest else "-")
    test_rows = history.count_test_entries(entries)
    lines.append(f"  entries: {len(entries)} ({size_kb:.1f} KB), "
                 f"oldest: {when}, test rows: {test_rows}")
    if test_rows:
        lines.append(f"  WARNING: {test_rows} test-fingerprint rows present "
                     f"\u2014 run `sayit-ermano history --scrub-tests`")
    return lines


def _hotkey_grab_line() -> list[str]:
    """Last-known hotkey grab state straight from the daemon (the same
    control-socket query the other daemon checks use). The listener tracks
    per-combo grab health and retries refused grabs, so doctor reflects
    the live truth - not a 'ready' that may be keyless."""
    from . import control
    try:
        if not paths.socket_path().exists():
            raise FileNotFoundError("no control socket")
        status = control.request("status")
    except Exception:  # noqa: BLE001 - daemon down / older daemon / timeout
        return ["  hotkey grab: unknown (daemon down)"]
    state = status.get("hotkey_grabbed")
    if state is None:
        return ["  hotkey grab: disabled (--no-hotkey or older daemon)"]
    if state is False:
        return ["  hotkey grab: BLOCKED (held by another client - daemon is "
                "retrying)"]
    return ["  hotkey grab: ok"]


def _mouse_ptt_lines(cfg: dict) -> list[str]:
    """Mouse push-to-talk resolution: config -> button + modifiers, plus
    the live arm state from the daemon when one is running (the same
    status surface _hotkey_grab_line uses)."""
    from . import control
    from .hotkey import HotkeyError, parse_button_spec
    rcfg = cfg.get("recording", {}) or {}
    spec = str(rcfg.get("push_to_talk_button") or "").strip()
    try:
        button = parse_button_spec(spec)
    except HotkeyError as e:
        return [f"  push-to-talk button: INVALID: {e}"]
    if button is None:
        return ["  push-to-talk button: not configured "
                "(keyboard hotkey only)"]
    mods = rcfg.get("push_to_talk_modifiers") or []
    mods_txt = "+".join(mods) if mods else "none"
    lines = [f"  push-to-talk button: button{button} "
             f"(XGrabButton on button {button}, modifiers {mods_txt})"]
    try:
        if not paths.socket_path().exists():
            return lines  # daemon down: report the resolution only
        status = control.request("status")
    except Exception:  # noqa: BLE001 - daemon down / older daemon / timeout
        return lines
    state = status.get("mouse_ptt_grabbed")
    if state is False:
        lines.append("  push-to-talk arm: BLOCKED (another client holds it "
                     "- daemon is retrying)")
    elif state is True:
        lines.append("  push-to-talk arm: ok")
    else:
        lines.append("  push-to-talk arm: disabled (daemon not running it)")
    return lines


def _lock_watch_lines(cfg: dict) -> list[str]:
    """Lock-watch state from the daemon's lock_watch status surface (the
    same control-socket query the hotkey/mouse-PTT lines use): which
    logind session is watched and how it was resolved, or the sleep-only
    mode when no graphical session exists at all."""
    if not (cfg.get("general", {}) or {}).get("pause_when_locked", True):
        return ["  lock watch: disabled (general.pause_when_locked = false)"]
    from . import control
    from .lockmon import VIA_DISPLAY
    try:
        if not paths.socket_path().exists():
            raise FileNotFoundError("no control socket")
        status = control.request("status")
    except Exception:  # noqa: BLE001 - daemon down / older daemon / timeout
        return ["  lock watch: unknown (daemon down)"]
    lw = status.get("lock_watch")
    if not lw:
        return ["  lock watch: unknown (older daemon)"]
    if lw.get("mode") == "session" and lw.get("session"):
        sid = str(lw["session"]).rsplit("/", 1)[-1]
        via = VIA_DISPLAY.get(lw.get("via"), "?")
        return [f"  lock watch: ok (watching session {sid} via {via})"]
    if lw.get("mode") == "sleep-only":
        return ["  lock watch: suspend-only (no graphical session)"]
    return ["  lock watch: off"]


def _update_lines(cfg: dict, *, check=None) -> list[str]:
    """Version/updates report: current vs latest, install method, and the
    copy-paste upgrade block. `check` is injectable (tests pass a stub);
    the default does one real GitHub fetch honoring the env kill-switch.
    A network failure is a single informational line - `ok` must NOT flip
    false on offline (that would make every air-gapped machine 'broken')."""
    from . import update as update_mod
    upd = cfg.get("updates", {}) or {}
    if update_mod.update_skipped():
        return ["update check: skipped (SAYITERMANO_SKIP_UPDATE_CHECK=1)"]
    if not upd.get("check", True):
        return ["update check: disabled (updates.check = false)"]
    if check is None:
        release, error = update_mod.fetch_latest_result()
    else:
        res = check()
        if isinstance(res, tuple):
            release, error = res
        else:
            release, error = res, None if res is not None \
                else "offline or GitHub API error"
    info = update_mod.detect_install_method()
    if release is None:
        return [f"version: {__version__} -> latest unknown "
                f"(offline or GitHub API error{': ' + error if error else ''})",
                f"  install: {info['method']} ({info['marker']})"]
    latest = release.get("version") or "?"
    if update_mod.is_newer(latest, __version__):
        lines = [f"version: {__version__} -> latest {latest} "
                 "(update available: sayit-ermano update)",
                 f"  install: {info['method']} ({info['marker']})"]
        block = update_mod.upgrade_command(info["method"], release).splitlines()
        lines.append("  upgrade: " + block[0])
        for extra in block[1:]:
            lines.append("           " + extra)
        digest = update_mod.deb_checksum(release)
        if digest:
            lines.append(f"  sha256: {digest.removeprefix('sha256:')}")
        return lines
    return [f"version: {__version__} -> latest {latest} (up to date)",
            f"  install: {info['method']} ({info['marker']})"]


def _duplicate_install_lines(home: Path | None = None) -> list[str]:
    """The 2026-09-04 incident guard: a system deb (/opt/sayit-ermano,
    XDG-autostarted) AND a user install (~/.local/share/sayit-ermano)
    both present means two daemons fight over the XGrabKey hotkey. Pure
    path existence - no network, unit-testable."""
    home = Path(home) if home is not None else Path.home()
    deb = Path("/opt/sayit-ermano")
    user = home / ".local" / "share" / "sayit-ermano"
    if deb.is_dir() and user.is_dir():
        return ["  WARNING: both a system deb (/opt/sayit-ermano) and a user "
                "install (~/.local/share/sayit-ermano) are present — two "
                "daemons fight over the hotkey; remove one "
                "(sudo apt remove sayit-ermano, or drop the user layout)"]
    return []


def _session_matrix_lines(cfg: dict) -> list[str]:
    """Session type + per-capability backend matrix (the wayland port's
    honest surface: one row per capability naming the resolved backend,
    with per-tool found/missing detail and the install hint when insertion
    is unavailable)."""
    info = session_mod.probe()
    caps = session_mod.capabilities(info, cfg=cfg)
    where = info.type + (f" ({info.desktop})" if info.desktop else "")
    lines = [f"session: {where}  DISPLAY={os.environ.get('DISPLAY', '-')} "
             f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '-')}"]
    rows = [("hotkey", "hotkey"), ("insertion", "insertion"),
            ("clipboard", "clipboard"), ("overlay", "overlay"),
            ("preview", "preview"), ("tray", "tray"),
            ("app-hint", "app hints")]
    for cap, label in rows:
        value = caps[cap]
        note = ""
        if info.is_wayland:
            if cap == "insertion" and value == "unavailable":
                value = "UNAVAILABLE"
                note = " - install wtype or ydotool"
            elif cap == "insertion":
                tools = [f"{t} {'found' if shutil.which(t) else 'missing'}"
                         for t in session_mod.WAYLAND_TYPE_TOOLS]
                note = f" ({', '.join(tools)}; xdotool is X11-only)"
            elif cap in ("overlay", "preview"):
                note = " (X11 pill not possible here; layer-shell pill: future)"
            elif cap == "hotkey":
                note = " (no global grabs on wayland - bind a DE shortcut)"
        lines.append(f"  {label}: {value}{note}")
    if info.is_wayland:
        _tool, reason = session_mod.resolve_wayland_tool(
            str((cfg.get("insertion", {}) or {}).get("wayland_tool",
                                                       "auto")),
            info.desktop_all)
        if reason:
            lines.append(f"  note: {reason}")
        lines.append("  paste verification: fixed delay only - cross-client "
                     "selection reads (the X11 read-observation) are "
                     "impossible on Wayland")
    return lines


def _evdev_ptt_lines(cfg: dict) -> list[str]:
    """Optional evdev push-to-talk (hotkey.wayland_evdev): python-evdev
    presence, /dev/input access and the input-group requirement - the
    privileged path, said plainly."""
    h = (cfg.get("hotkey", {}) or {})
    enabled = bool(h.get("wayland_evdev", False))
    device = str(h.get("wayland_evdev_device", "") or "")
    key = str(h.get("wayland_evdev_key", "KEY_RIGHTCTRL") or "")
    state = "enabled" if enabled else "off (hotkey.wayland_evdev)"
    lines = [f"  evdev push-to-talk: {state}",
             f"  device pattern: {device or '(unset)'} · key: {key}"]
    try:
        import evdev  # noqa: F401
    except ImportError:
        lines.append("  python-evdev: MISSING (pip install "
                     "'sayit-ermano[wayland]' or pip install evdev)")
        return lines
    import glob
    events = glob.glob("/dev/input/event*")
    if not events:
        lines.append("  /dev/input: no event devices")
        return lines
    readable = [e for e in events if os.access(e, os.R_OK)]
    if len(readable) == len(events):
        lines.append(f"  /dev/input: {len(readable)}/{len(events)} event "
                     "devices readable")
    elif readable:
        lines.append(f"  /dev/input: {len(readable)}/{len(events)} readable - "
                     "add yourself to the 'input' group for the rest "
                     "(privileged path)")
    else:
        lines.append("  /dev/input: NOT readable - add yourself to the input "
                     "group (privileged path): sudo usermod -aG input $USER")
    if device:
        matches = []
        for e in readable:
            try:
                name = evdev.InputDevice(e).name
            except Exception:
                continue
            if device.lower() in name.lower():
                matches.append(f"{name} ({e})")
        lines.append("  matching devices: " + (", ".join(matches) if matches
                     else "none - check hotkey.wayland_evdev_device"))
    return lines


def run() -> int:
    print(f"SayItErmano v{__version__} doctor\n")
    ok = True

    try:
        cfg = load_config(paths.config_file())
    except Exception:
        cfg = {}

    info = session_mod.probe()
    caps = session_mod.capabilities(info, cfg=cfg)
    print("session:")
    for line in _session_matrix_lines(cfg):
        print(line)
    if info.is_wayland:
        print("  shortcut:")
        for line in session_mod.de_shortcut_instructions(
                info.desktop_all, str(paths.toggle_script())):
            print("    " + line)
        if caps["insertion"] == "unavailable":
            ok = False  # the one wayland hard failure: nothing can insert text
        if bool((cfg.get("hotkey", {}) or {}).get("wayland_evdev", False)):
            print("  evdev push-to-talk:")
            for line in _evdev_ptt_lines(cfg):
                print("    " + line)

    print("\nversion/updates:")
    for line in _update_lines(cfg):
        print(line)
    for line in _duplicate_install_lines():
        print(line)

    print(f"\nconfig: {paths.config_file()} ({'exists' if paths.config_file().exists() else 'not created yet - defaults in use'})")
    for line in _history_lines():
        print(line)
    print("\ndictionary learning:")
    print(_suggestions_line(cfg))
    for line in _models_cache_lines(cfg):
        print(line)
    for line in _model_state_lines(cfg):
        print(line)

    print("\ntools:")
    for tool, why in [
        ("pw-record", "PipeWire recording (preferred)"),
        ("parecord", "PulseAudio recording (fallback)"),
        ("xdotool", "typing text into apps (X11)"),
        ("xclip", "clipboard paste mode + restore (X11)"),
        ("wtype", "typing text into apps (Wayland; wlroots/KDE - not GNOME)"),
        ("ydotool", "typing on any compositor via uinput (Wayland; needs "
                  "ydotoold running + /dev/uinput access)"),
        ("wl-copy", "clipboard on Wayland (wl-clipboard)"),
        ("wl-paste", "clipboard read/restore on Wayland (wl-clipboard)"),
        ("notify-send", "desktop notifications"),
        ("pw-play", "start/stop sounds"),
        ("ffmpeg", "transcribe fallback for non-WAV/undecodable input"),
    ]:
        have = shutil.which(tool)
        print(f"  {'OK ' if have else '-- '}{tool}: {'found' if have else 'missing'} ({why})")
        if tool in ("pw-record", "parecord") and not have:
            ok = False
    if os.environ.get("XDG_SESSION_TYPE") == "x11" and not shutil.which("xdotool"):
        ok = False

    print("\nspeech backends:")
    try:
        for name, status in backends.backend_status().items():
            print(f"  {name}: {status}")
    except Exception as e:
        print(f"  error probing backends: {e}")
    print(f"  cuda_available(): {backends.cuda_available()}")
    if shutil.which("nvidia-smi"):
        os.system("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  GPU: /'")

    print("\nwhisper.cpp:")
    for line in _whispercpp_lines(cfg):
        print(line)

    print("\nparakeet:")
    for line in _parakeet_lines(cfg):
        print(line)

    print("\nlanguage resolution:")
    for line in _language_lines(cfg):
        print(line)

    print("\nlive preview:")
    for line in _preview_lines(cfg):
        print(line)

    print("\nchat/terminal formatting:")
    for line in _formatting_lines(cfg):
        print(line)

    print("\ncommand mode:")
    for line in _command_mode_lines(cfg):
        print(line)

    print("\ninsertion hardening:")
    for line in _insertion_lines(cfg):
        print(line)

    print(f"\ncontrol socket: {paths.socket_path()} "
          f"({'alive' if paths.socket_path().exists() else 'daemon not running'})")
    print("\n".join(_hotkey_grab_line()))
    for line in _mouse_ptt_lines(cfg):
        print(line)
    for line in _lock_watch_lines(cfg):
        print(line)
    if _gtk_available():
        print("settings app: GTK 4 + libadwaita OK (`sayit-ermano app`)")
    else:
        print("settings app: GTK 4 / libadwaita missing - install with\n"
              "  apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1")
        ok = False

    print("\nresult:", "ready" if ok else "see warnings above")
    return 0 if ok else 1
