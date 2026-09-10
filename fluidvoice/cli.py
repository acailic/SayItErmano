"""SayItErmano command line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__, paths
from . import doctor as doctor_mod
from .config import load_config, write_template


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sayit-ermano",
        description="SayItErmano - local voice dictation with AI polish (community Linux port of FluidVoice)")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("daemon", help="run the dictation daemon (foreground)")
    p.add_argument("--no-hotkey", action="store_true",
                   help="skip the X11 global hotkey (use `sayit-ermano toggle` instead)")
    p.add_argument("--no-sounds", action="store_true", help="disable start/stop sounds")
    p.add_argument("--config", type=Path, help="alternative config file")

    for name, help_ in [("toggle", "start/stop a recording"),
                        ("cancel", "cancel the running recording (no transcription)"),
                        ("status", "daemon status"),
                        ("paste-last", "re-type the most recent transcription"),
                        ("language", "cycle the dictation language (general.language_cycle)")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--json", action="store_true", help="raw JSON output")

    p = sub.add_parser("transcribe", help="one-shot transcription of an audio file")
    p.add_argument("file", type=Path,
                   help="audio file (wav/flac/mp3/opus/oga/ogg/m4a/aac/wma/aiff/webm)")
    p.add_argument("--no-process", action="store_true",
                   help="skip filler/punctuation post-processing")
    p.add_argument("--ai", action="store_true", help="also run AI polish")
    p.add_argument("--json", action="store_true",
                   help="print structured JSON {text, language, duration_s, segments} "
                        "instead of plain text")
    p.add_argument("--out", type=Path, metavar="PATH",
                   help="write the result to PATH instead of stdout (plain text, "
                        "or JSON with --json)")
    p.add_argument("--config", type=Path, help="alternative config file")

    p = sub.add_parser("history", help="show recent transcriptions")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--export", type=Path, metavar="PATH.zip",
                   help="write history + retained audio to a zip")
    p.add_argument("--scrub-tests", action="store_true",
                   help="remove test-suite fingerprint rows "
                        "(dry-run by default; shows a per-command breakdown)")
    p.add_argument("--yes", action="store_true",
                   help="with --scrub-tests: apply the removal "
                        "(a history.jsonl.bak-<ts> backup is written first)")

    p = sub.add_parser("config", help="show/init the config file")
    p.add_argument("action", nargs="?", default="path", choices=["path", "init", "print"])

    p = sub.add_parser("settings",
                       help="open the native Settings window (alias of `app --open settings`)")
    p = sub.add_parser("app", help="open the native GTK app (History/Settings)")
    p.add_argument("--open", choices=["history", "settings"], default="history",
                   help="window to raise (default: history)")
    p.add_argument("--onboard", action="store_true",
                   help="run the first-run onboarding flow")
    sub.add_parser("doctor", help="environment check")
    sub.add_parser("mcp",
                   help="run the MCP server on stdio (for MCP-capable "
                        "agents; forwards to the running daemon)")

    # local evaluation harness (plan P1.4): thin passthrough — everything
    # lives in fluidvoice.evalharness (python -m fluidvoice.evalharness)
    p = sub.add_parser("eval-run",
                       help="local evaluation harness (args passed through; "
                            "see `python -m fluidvoice.evalharness --help`)")
    p.add_argument("harness_args", nargs=argparse.REMAINDER, metavar="ARGS",
                   help="harness subcommand and flags (run/check/fixtures/soak)")

    p = sub.add_parser(
        "update",
        help="check for a newer release and print the upgrade command for "
             "this install (nothing is executed)")
    p.add_argument("--dismiss", action="store_true",
                   help="stop the update notification for the current/latest "
                             "release (records it in the update state)")
    p.add_argument("--json", action="store_true",
                   help="raw JSON output")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    if args.cmd == "daemon":
        from .daemon import Daemon
        cfg = load_config(args.config)
        lock = _acquire_daemon_lock()
        if lock is None:
            print("sayit-ermano daemon is already running - second instance "
                  "exiting", file=sys.stderr)
            return 0
        try:
            Daemon(cfg, use_hotkey=not args.no_hotkey,
                   use_sounds=not args.no_sounds).run()
        finally:
            lock.close()
            _DAEMON_LOCK_FILE.unlink(missing_ok=True)
        return 0

    if args.cmd in ("toggle", "cancel", "status", "paste-last", "language"):
        from . import control
        try:
            resp = control.request("cycle-language" if args.cmd == "language"
                                   else args.cmd)
        except control.ControlError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(resp))
        elif args.cmd == "language":
            if resp.get("ok"):
                print(f"cycled -> {resp.get('language')} ({resp.get('source')})")
            else:
                print(f"language cycle: {resp.get('error')}")
        else:
            print(_describe(resp))
        return 0 if resp.get("ok") else 1

    if args.cmd == "transcribe":
        from . import backends, chunking
        from .ai.client import AIClient
        from .audio_utils import SUPPORTED_AUDIO_EXTS, AudioFormatError
        from .processing import post_process
        if not args.file.exists():
            print(f"error: file not found: {args.file}", file=sys.stderr)
            return 1
        cfg = load_config(args.config)
        backend = backends.load_backend(cfg)
        if args.file.suffix.lower() not in SUPPORTED_AUDIO_EXTS:
            print(f"note: '{args.file.suffix}' is not a verified format - trying "
                  "anyway (ffmpeg fallback when needed)", file=sys.stderr)
        try:
            # long inputs are chunked (P3): convert once, ten-minute
            # overlapping chunks, one reconciled transcript
            result = chunking.transcribe_long(
                backend, args.file,
                language=backends.effective_language(cfg, backend),
                force_whisper_cpp=backend.name == "whisper.cpp")
        except (AudioFormatError, chunking.AudioTooLargeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        text = result.to_plain_text()
        if not args.no_process:
            text = post_process(text, cfg)
        if args.ai and cfg["ai"].get("enabled"):
            text = AIClient(cfg).polish(text)
        elif args.ai:
            print("(ai.enabled=false in config; raw transcription only)", file=sys.stderr)
        if args.json:
            # CLI-edge serialization: the historical --json payload shape
            # (duration_s; raw per-segment text, not post-processed)
            payload = {"text": text,  # final text (post-processed/AI if on)
                       "language": result.language,
                       # null for torch/whisper.cpp; [] when backend exposes none
                       "duration_s": result.duration,
                       "segments": [s.to_dict() for s in result.segments]}
            out_text = json.dumps(payload, indent=2, ensure_ascii=False)
        else:
            out_text = text
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(out_text + "\n", encoding="utf-8")
            print(f"wrote {args.out}", file=sys.stderr)  # stderr keeps stdout clean
        else:
            print(out_text)
        return 0

    if args.cmd == "history":
        from . import history
        if args.export:
            def _note(m):
                print(m, file=sys.stderr)
            try:
                n = history.export_zip(args.export, on_note=_note)
            except OSError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            print(f"exported {n} entries to {args.export}")
            return 0
        if args.scrub_tests:
            counts = history.test_command_counts()
            for cmd in sorted(counts):
                print(f"  {cmd}: {counts[cmd]}")
            removed = sum(counts.values())
            if args.yes:
                removed, total, backup = history.scrub_test_entries(apply=True)
                if backup is not None:
                    print(f"removed {removed} entries (kept {total - removed}), "
                          f"backup: {backup}")
                else:
                    print(f"nothing to remove ({total} entries, 0 test rows)")
            else:
                total = len(history.read_all())
                print(f"would remove {removed} of {total} entries "
                      f"\u2014 run with --yes to apply")
            return 0
        for entry in history.tail(args.n):
            ts = entry.get("ts")
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"
            ai = " [AI]" if entry.get("ai") else ""
            print(f"{when}{ai}: {entry.get('text', '')}")
        return 0

    if args.cmd == "config":
        if args.action == "init":
            path = write_template()
            print(f"wrote {path}")
        elif args.action == "print":
            print(paths.config_file().read_text() if paths.config_file().exists()
                  else "(no config file - defaults in use; run `sayit-ermano config init`)")
        else:
            print(paths.config_file())
        return 0

    if args.cmd == "settings":
        # Kept for the .desktop entry (Exec=sayit-ermano settings): opens the
        # native window now that the web UI is retired.
        from .gtkui.application import run as run_app
        return run_app(["--open", "settings"])

    if args.cmd == "app":
        from .gtkui.application import run as run_app
        return run_app(["--open", args.open] + (["--onboard"] if args.onboard else []))

    if args.cmd == "mcp":
        from .mcp_server import serve
        serve()
        return 0

    if args.cmd == "eval-run":
        from .evalharness.cli import main as eval_main
        return eval_main(list(args.harness_args))

    if args.cmd == "doctor":
        return doctor_mod.run()

    if args.cmd == "update":
        return _cmd_update(args)

    return 0


def _cmd_update(args) -> int:
    """`sayit-ermano update`: one sync check (10 s timeout, env kill-
    switch honored) + the detected-method copy-paste block. Informational
    only - NOTHING is executed. Exit 0 even when offline."""
    from . import update as update_mod
    info = update_mod.detect_install_method()
    method, marker = info["method"], info["marker"]

    if args.dismiss:
        ver = update_mod.dismiss_update()
        print(f"dismissed {ver} (no notification until a newer release)")
        return 0

    skipped = update_mod.update_skipped()
    release = None
    error = None
    if not skipped:
        release, error = update_mod.fetch_latest_result()

    latest = (release or {}).get("version")
    # offline / skipped: still show a usable block from the last-seen state
    # (or just the releases URL when nothing was ever seen)
    if release is None:
        last_seen = update_mod._read_state(None).get("last_seen")
        if last_seen:
            release = {"tag": f"v{last_seen}", "version": last_seen,
                       "url": update_mod.RELEASES_URL, "assets": []}

    available = bool(latest) and update_mod.is_newer(latest, __version__)
    command = update_mod.upgrade_command(method, release)
    checksum = update_mod.deb_checksum(release) if available else None

    payload = {"current": __version__, "method": method, "marker": marker,
               "latest": latest, "update_available": latest if available else None,
               "url": (release or {}).get("url") if available else None,
               "upgrade_command": command, "sha256": checksum,
               "skipped": skipped, "error": error}
    if args.json:
        print(json.dumps(payload))
        return 0

    print(f"SayItErmano {__version__} (install: {method} — {marker})")
    if skipped:
        print("check skipped (SAYITERMANO_SKIP_UPDATE_CHECK=1)")
    elif latest is None:
        print(f"latest release: unknown (offline or GitHub API error"
              f"{': ' + error if error else ''})")
    elif available:
        print(f"latest release: v{latest} — update available")
    else:
        print(f"latest release: v{latest} — up to date")
    print("upgrade (copy-paste):")
    for line in command.splitlines():
        print(f"  {line}")
    if checksum:
        print(f"sha256: {checksum.removeprefix('sha256:')}"
              "   (published digest - verify after download)")
    if available:
        print("(--dismiss stops the notification for this release)")
    return 0


_DAEMON_LOCK_FILE = None


def _acquire_daemon_lock():
    """Singleton guard: the deb starts the daemon via XDG autostart AND a
    systemd unit - at login both fire. First instance holds an flock on
    ~/.config/sayit-ermano/daemon.lock; the second exits immediately."""
    import fcntl

    from .paths import config_dir
    global _DAEMON_LOCK_FILE
    path = config_dir() / "daemon.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    _DAEMON_LOCK_FILE = path
    return fh


def _describe(resp: dict) -> str:
    if "recording" in resp:
        state = "recording" if resp["recording"] else "stopped"
        text = f"{state}" + (" (cancelled)" if resp.get("cancelled") else "")
        if "today" in resp:
            from . import history
            text += "\ntoday: " + history.format_today(resp["today"])
        caps = resp.get("capabilities") or {}
        if resp.get("session", {}).get("type") == "wayland":
            # the wayland capability line (x11 prints nothing new)
            text += ("\nsession: wayland — insertion: "
                     f"{caps.get('insertion', '?')} — hotkey: DE shortcut "
                     f"— overlay: {caps.get('overlay', '?')}")
        if resp.get("update_available"):
            text += (f"\nupdate available: {resp['update_available']} "
                     "(sayit-ermano update)")
        return text
    return json.dumps(resp)


if __name__ == "__main__":
    raise SystemExit(main())
