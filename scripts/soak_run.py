#!/usr/bin/env python3
"""Q9 soak harness — real daemon subprocess, scripted takes, RSS gate.

scripts/soak.py (Q-prior) PASSIVELY samples a daemon the owner started;
this harness is the CI-able counterpart: it spawns the REAL daemon
(``sys.executable -m fluidvoice daemon``, model-free, headless-friendly,
X11/Wayland-agnostic — no hotkey, no sounds, no app spawn, no update
probe), drives scripted toggle/cancel takes, samples RSS / fd count /
thread count / socket health every interval, and writes evidence:

    docs/quality/soak/<ISO-date>-<duration>m.json   samples + verdict
    docs/quality/soak/<ISO-date>-<duration>m.md     human summary

Exit 1 when the verdict is red, so a lane can gate on it. The two gate
numbers (both documented in docs/quality/soak/README.md):

* RSS growth  — median RSS of the warmup window vs median of the last
  window; FAIL when start-to-end growth exceeds --tolerance-pct
  (default 15%). Warmup (default 60 s) absorbs one-time allocations.
* Socket      — every sample round-trips a `status` request over the
  control socket; any failure (or a dead daemon) is red.

Usage (defaults: 10 min, 15 s samples, a take every 30 s):

    python scripts/soak_run.py
    python scripts/soak_run.py --duration-minutes 10 --interval-seconds 15
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DEFAULT = REPO / "docs" / "quality" / "soak"

SOCKET_TIMEOUT_S = 10.0

# Model-free daemon config (integration-conftest shape): no preview, no
# sounds, no notifications, history on — everything that needs a display,
# a model or a session bus is off; the take path still runs end to end.
SOAK_CONFIG = """\
[general]
language = "en"

[hotkey]
key = "F9"
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


class HarnessError(RuntimeError):
    """Fatal setup/verdict error (spawn failure, dead daemon, bad args)."""


# ---------------------------------------------------------------------------
# pure verdict math (unit-tested with fakes — no daemon involved)
# ---------------------------------------------------------------------------

def median(vals: list[float]) -> float:
    return float(statistics.median(vals))


def _linreg_slope_kb_per_s(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of (t_s, rss_kb); 0.0 for fewer than 2 points."""
    if len(points) < 2:
        return 0.0
    n = float(len(points))
    sum_t = sum(p[0] for p in points)
    sum_v = sum(p[1] for p in points)
    mean_t = sum_t / n
    mean_v = sum_v / n
    num = sum((p[0] - mean_t) * (p[1] - mean_v) for p in points)
    den = sum((p[0] - mean_t) ** 2 for p in points)
    return num / den if den else 0.0


def rss_verdict(samples: list[dict], warmup_s: float,
                tolerance_pct: float = 15.0,
                end_window: int = 3) -> dict:
    """PASS/FAIL on start-to-end RSS growth after warmup (pure).

    baseline = median RSS of samples with t <= warmup_s; end = median
    RSS of the last `end_window` samples (a single-sample GC blip can
    neither pass nor fail the gate). Fail-safe: a run too short to have
    >= 2 warmup and >= 2 post-warmup samples is RED with reason
    "insufficient-samples" — a soak that proved nothing did not pass.
    """
    warm = [s for s in samples if s["t"] <= warmup_s]
    post = [s for s in samples if s["t"] > warmup_s]
    out: dict = {
        "tolerance_pct": tolerance_pct,
        "warmup_s": warmup_s,
        "warmup_samples": len(warm),
        "post_samples": len(post),
    }
    if len(warm) < 2 or len(post) < 2:
        out.update(ok=False, reason="insufficient-samples",
                   baseline_rss_kb=None, end_rss_kb=None,
                   growth_pct=None, slope_mb_per_h=None)
        return out
    baseline = median([s["rss_kb"] for s in warm])
    tail = post[-max(2, end_window):]
    end = median([s["rss_kb"] for s in tail])
    growth = (end - baseline) / baseline * 100.0 if baseline > 0 else 0.0
    slope_kb_s = _linreg_slope_kb_per_s(
        [(s["t"], s["rss_kb"]) for s in post])
    out.update(
        baseline_rss_kb=round(baseline, 1),
        end_rss_kb=round(end, 1),
        growth_pct=round(growth, 2),
        slope_mb_per_h=round(slope_kb_s * 3600.0 / 1024.0, 2),
        ok=bool(growth <= tolerance_pct),
    )
    if not out["ok"]:
        out["reason"] = (f"RSS grew {growth:.1f}% start-to-end after "
                         f"warmup (tolerance {tolerance_pct:.0f}%)")
    return out


def socket_verdict(samples: list[dict]) -> dict:
    """Every sample's status round-trip must have succeeded (pure)."""
    failed = [s["t"] for s in samples if not s.get("socket_ok", False)]
    return {"failures": len(failed), "failed_at_s": failed,
            "ok": not failed}


def verdict(samples: list[dict], warmup_s: float,
            tolerance_pct: float) -> dict:
    """Composite gate: RSS growth within tolerance AND socket always up."""
    rss = rss_verdict(samples, warmup_s, tolerance_pct)
    sock = socket_verdict(samples)
    ok = rss["ok"] and sock["ok"]
    reasons = []
    if not rss["ok"]:
        reasons.append(rss.get("reason", "rss gate red"))
    if not sock["ok"]:
        reasons.append(f"control socket failed to answer at "
                       f"{sock['failed_at_s']}")
    return {"ok": ok, "reasons": reasons, "rss": rss, "socket": sock}


# ---------------------------------------------------------------------------
# process + socket probes (stdlib-only; no fluidvoice import needed)
# ---------------------------------------------------------------------------

def read_proc_sample(pid: int) -> dict | None:
    """VmRSS kB, fd count, thread count from /proc, or None when gone."""
    try:
        rss_kb = None
        threads = None
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                elif line.startswith("Threads:"):
                    threads = int(line.split()[1])
                if rss_kb is not None and threads is not None:
                    break
        fds = len(os.listdir(f"/proc/{pid}/fd"))
        if rss_kb is None or threads is None:
            return None
        return {"rss_kb": float(rss_kb), "fds": fds, "threads": threads}
    except (OSError, ValueError, IndexError):
        return None


def sock_request(path: Path, action: str, **kwargs) -> dict:
    """One JSON-line control request (the daemon/client wire format)."""
    import socket
    payload = dict(kwargs, action=action)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(SOCKET_TIMEOUT_S)
        s.connect(str(path))
        s.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    if not buf.strip():
        raise HarnessError("empty response from daemon")
    return json.loads(buf.decode())


# ---------------------------------------------------------------------------
# daemon subprocess (isolated env, same overrides as the process lane)
# ---------------------------------------------------------------------------

@dataclass
class SoakDaemon:
    proc: subprocess.Popen
    socket: Path
    workdir: Path
    log_path: Path
    env: dict = field(default_factory=dict)


def spawn_daemon(python: str, workdir: Path, repo: Path = REPO,
                 env: dict | None = None) -> SoakDaemon:
    """Start the real daemon model-free; raise HarnessError on a bad start.

    Isolation mirrors tests/integration/conftest.py: private config,
    socket, XDG dirs; NO_APP_SPAWN + SKIP_UPDATE_CHECK keep it headless
    and offline; XDG_SESSION_TYPE is left as inherited (the daemon is
    display-agnostic — it only needs the socket to come up).
    """
    run = workdir / "run"
    run.mkdir(parents=True, exist_ok=True)
    (workdir / "data").mkdir(exist_ok=True)
    (workdir / "config").mkdir(exist_ok=True)
    (workdir / "cache").mkdir(exist_ok=True)
    cfg = workdir / "config.toml"
    cfg.write_text(SOAK_CONFIG)
    socket_path = run / "fluidvoice.sock"
    denv = {**(env if env is not None else os.environ),
            "SAYITERMANO_CONFIG": str(cfg),
            "SAYITERMANO_SOCKET": str(socket_path),
            "XDG_DATA_HOME": str(workdir / "data"),
            "XDG_CONFIG_HOME": str(workdir / "config"),
            "XDG_CACHE_HOME": str(workdir / "cache"),
            "SAYITERMANO_NO_APP_SPAWN": "1",
            "SAYITERMANO_SKIP_UPDATE_CHECK": "1",
            # deterministic locale for the log; nothing else leaks in
            "LC_ALL": "C", "LANG": "C",
            }
    denv.pop("WAYLAND_DISPLAY", None)
    log_path = workdir / "daemon.log"
    with open(log_path, "w") as out:
        proc = subprocess.Popen(
            [python, "-m", "fluidvoice", "daemon", "--no-hotkey",
             "--no-sounds"],
            stdout=out, stderr=subprocess.STDOUT, text=True,
            cwd=str(repo), env=denv,
            start_new_session=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if socket_path.exists():
            return SoakDaemon(proc, socket_path, workdir, log_path, denv)
        if proc.poll() is not None:
            raise HarnessError(
                f"daemon died at startup (rc={proc.returncode}); "
                f"log: {log_path.read_text(errors='replace')[-2000:]}")
        time.sleep(0.2)
    stop_daemon(SoakDaemon(proc, socket_path, workdir, log_path, denv))
    raise HarnessError("daemon socket never appeared; log: "
                       + log_path.read_text(errors="replace")[-2000:])


def stop_daemon(d: SoakDaemon, timeout_s: float = 10.0) -> None:
    """SIGINT (graceful, cleans the socket), then SIGKILL if needed."""
    if d.proc.poll() is None:
        try:
            d.proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass
        try:
            d.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            d.proc.kill()
            d.proc.wait(timeout=5)


def drive_take(d: SoakDaemon, hold_s: float) -> dict:
    """One scripted take: toggle, hold, cancel (model-free — a machine
    with no recorder takes the clean error path, which is still load)."""
    take: dict = {"t": None, "recording": None, "cancelled": None,
                  "error": None}
    try:
        resp = sock_request(d.socket, "toggle")
        take["recording"] = bool(resp.get("recording"))
        time.sleep(hold_s)
        if take["recording"]:
            resp = sock_request(d.socket, "cancel")
            take["cancelled"] = bool(resp.get("cancelled"))
        else:
            resp = sock_request(d.socket, "cancel")
            take["cancelled"] = resp.get("cancelled") is True
    except Exception as e:  # noqa: BLE001 — recorded as a take error
        take["error"] = f"{type(e).__name__}: {e}"[:200]
    return take


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

def evidence_paths(out_dir: Path, when: datetime,
                   duration_minutes: float) -> tuple[Path, Path]:
    """<ISO-date>-<duration>m.json/.md; -2, -3 … on same-day collision."""
    mins = int(round(duration_minutes))
    stem = f"{when.date().isoformat()}-{mins}m"
    json_path = out_dir / f"{stem}.json"
    n = 2
    while json_path.exists():
        json_path = out_dir / f"{stem}-{n}.json"
        n += 1
    return json_path, json_path.with_suffix(".md")


def write_evidence(out_dir: Path, report: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    when = datetime.now(timezone.utc)
    json_path, md_path = evidence_paths(
        out_dir, when, report["duration_minutes"])
    report["written_utc"] = when.isoformat(timespec="seconds")
    json_path.write_text(json.dumps(report, indent=1) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _fmt(v, spec: str = ".0f") -> str:
    """Format a verdict number, tolerating the insufficient-data None."""
    return format(v, spec) if isinstance(v, (int, float)) else "n/a"


def render_markdown(r: dict) -> str:
    v = r["verdict"]
    rss, sock = v["rss"], v["socket"]
    status = "PASS" if v["ok"] else "FAIL"
    lines = [
        f"# Soak run {r['started_utc']} — {status}",
        "",
        f"- daemon: `{r['daemon_cmd']}` (repo `{r['repo_sha']}`)",
        f"- duration: {r['duration_minutes']} min, sample interval "
        f"{r['config']['interval_s']} s, warmup "
        f"{r['config']['warmup_s']} s",
        f"- takes: {r['take_summary']}",
        "",
        "## Verdict math (the gate, in the open)",
        "",
        f"- baseline RSS (median of {rss['warmup_samples']} warmup "
        f"samples): **{_fmt(rss['baseline_rss_kb'])} kB**",
        f"- end RSS (median of the last samples): "
        f"**{_fmt(rss['end_rss_kb'])} kB**",
        f"- growth: **{_fmt(rss['growth_pct'], '.2f')}%** (tolerance "
        f"≤ {rss['tolerance_pct']:.0f}% → "
        f"{'within' if rss['ok'] else 'EXCEEDED'})",
        f"- post-warmup slope: {_fmt(rss['slope_mb_per_h'], '.2f')} MB/h "
        "(reported, not gated)",
        f"- socket: {sock['failures']} failed status round-trips of "
        f"{len(r['samples'])} samples"
        f"{' — ' + '; '.join(v['reasons']) if v['reasons'] else ''}",
        "",
        "## Samples",
        "",
        "| t (s) | RSS (kB) | fds | threads | socket |",
        "|---|---|---|---|---|",
    ]
    for s in r["samples"]:
        rss = _fmt(s["rss_kb"]) if "rss_kb" in s else "dead"
        lines.append(f"| {s['t']:.0f} | {rss} "
                     f"| {s.get('fds', '—')} | {s.get('threads', '—')} "
                     f"| {'ok' if s['socket_ok'] else 'FAIL'} |")
    lines += ["", f"Reproduce: `python scripts/"
                  f"{Path(r['config']['script']).name}"
                  f" --duration-minutes {r['duration_minutes']}`", ""]
    return "\n".join(lines)


def repo_sha(repo: Path = REPO) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 — evidence carries '?' offline
        return "?"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run_soak(args: argparse.Namespace) -> int:
    duration_s = args.duration_minutes * 60.0
    if args.warmup_s >= duration_s:
        raise HarnessError(f"warmup ({args.warmup_s} s) must be shorter "
                           f"than the duration ({duration_s:.0f} s)")
    import tempfile
    work = Path(tempfile.mkdtemp(prefix="sayit-soak-"))
    d = spawn_daemon(args.python, work, repo=args.repo)
    print(f"daemon pid {d.proc.pid} · socket {d.socket} · "
          f"{args.duration_minutes} min run", flush=True)
    samples: list[dict] = []
    takes: list[dict] = []
    t0 = time.monotonic()
    next_sample = t0
    next_take = t0 + min(args.warmup_s, args.take_every_s) \
        if args.take_every_s > 0 else float("inf")
    sock_latency_ms: list[float] = []
    try:
        while True:
            now = time.monotonic()
            if now >= t0 + duration_s:
                break
            if now >= next_sample:
                proc_s = read_proc_sample(d.proc.pid)
                if proc_s is None:
                    samples.append({"t": round(now - t0, 1),
                                    "socket_ok": False,
                                    "socket_ms": None, "dead": True})
                    break
                t_req = time.monotonic()
                ok = True
                try:
                    resp = sock_request(d.socket, "status")
                    ok = bool(resp.get("ok"))
                except Exception:  # noqa: BLE001 — recorded per-sample
                    ok = False
                ms = (time.monotonic() - t_req) * 1000.0
                sock_latency_ms.append(ms)
                samples.append({"t": round(now - t0, 1), **proc_s,
                                "socket_ok": ok,
                                "socket_ms": round(ms, 1)})
                print(f"t={samples[-1]['t']:>6.0f}s rss="
                      f"{proc_s['rss_kb'] / 1024:8.1f}MB fds="
                      f"{proc_s['fds']:3d} thr={proc_s['threads']:2d} "
                      f"sock={'ok' if ok else 'FAIL'}", flush=True)
                next_sample += args.interval_s
            if now >= next_take:
                take = drive_take(d, args.take_hold_s)
                take["t"] = round(time.monotonic() - t0, 1)
                takes.append(take)
                next_take += args.take_every_s
            time.sleep(min(0.5, args.interval_s / 4))
    finally:
        stop_daemon(d)
        alive = d.proc.poll() is None
        time.sleep(0.2)
        if d.socket.exists() and not alive:
            pass  # SIGKILL path: socket file may linger; not a gate input
    errored = [tk for tk in takes if tk["error"]]
    started = [tk for tk in takes if tk["recording"]]
    cancelled = [tk for tk in takes if tk["cancelled"]]
    take_summary = (f"{len(takes)} driven, {len(started)} started "
                    f"recording, {len(cancelled)} cancelled, "
                    f"{len(errored)} errored (no recorder on this host "
                    "takes the clean error path — still socket+daemon load)")
    v = verdict(samples, args.warmup_s, args.tolerance_pct)
    report = {
        "schema": "sayit-soak/1",
        "started_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "repo_sha": repo_sha(args.repo),
        "daemon_cmd": f"{args.python} -m fluidvoice daemon --no-hotkey "
                      "--no-sounds",
        "daemon_exit": d.proc.returncode,
        "duration_minutes": args.duration_minutes,
        "config": {
            "script": str(Path(__file__).name),
            "interval_s": args.interval_s,
            "warmup_s": args.warmup_s,
            "tolerance_pct": args.tolerance_pct,
            "take_every_s": args.take_every_s,
            "take_hold_s": args.take_hold_s,
        },
        "env": {"python": args.python,
                "kernel": os.uname().release,
                "argv": sys.argv[1:]},
        "samples": samples,
        "socket": {"max_status_ms": round(max(sock_latency_ms), 1)
                   if sock_latency_ms else None},
        "takes": takes,
        "take_summary": take_summary,
        "verdict": v,
        "daemon_log_tail": d.log_path.read_text(errors="replace")[-4000:],
    }
    json_path, md_path = write_evidence(args.out_dir, report)
    print(f"verdict: {'PASS' if v['ok'] else 'FAIL'} "
          f"({'; '.join(v['reasons']) or 'rss + socket gates green'})")
    print(f"evidence: {json_path}\n          {md_path}", flush=True)
    if args.keep_work:
        print(f"workdir kept: {work}")
    else:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return 0 if v["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Q9 soak harness: real daemon, scripted takes, "
                    "RSS/fd/thread/socket sampling, evidence + gate.")
    ap.add_argument("--duration-minutes", type=float, default=10.0,
                    dest="duration_minutes",
                    help="total run length (default 10; shorter in CI)")
    ap.add_argument("--interval-seconds", type=float, default=15.0,
                    dest="interval_s",
                    help="seconds between samples (default 15)")
    ap.add_argument("--warmup-seconds", type=float, default=60.0,
                    dest="warmup_s",
                    help="samples at t<=this are the RSS baseline "
                         "(default 60)")
    ap.add_argument("--tolerance-pct", type=float, default=15.0,
                    dest="tolerance_pct",
                    help="FAIL when start-to-end RSS growth exceeds this "
                         "percent after warmup (default 15)")
    ap.add_argument("--take-every-seconds", type=float, default=30.0,
                    dest="take_every_s",
                    help="seconds between scripted toggle/cancel takes "
                         "(0 disables)")
    ap.add_argument("--take-hold-seconds", type=float, default=2.0,
                    dest="take_hold_s",
                    help="hold time inside a take (default 2 s)")
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT,
                    help=f"evidence directory (default {OUT_DEFAULT})")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter for the daemon subprocess "
                         "(default: this one)")
    ap.add_argument("--repo", type=Path, default=REPO,
                    help="repo to run the daemon from (default: the "
                         "checkout holding this script)")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the daemon workdir (config/log) for "
                         "debugging")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run_soak(args)
    except HarnessError as e:
        print(f"soak: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
