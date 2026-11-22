#!/usr/bin/env python3
"""Daemon soak harness (quality plan Q9) — spawn, load, watch, judge.

Model-free process-lane evidence runner (Q7 pattern: real daemon
subprocess via ``sys.executable -m fluidvoice``, no GPU, no display, no
model download). What one run does:

1. builds an isolated sandbox (config/socket/XDG dirs + a stub
   ``pw-record`` on the daemon's PATH that streams silence), spawns the
   REAL daemon from THIS checkout with the X11/Wayland-agnostic flags
   (``--no-hotkey --no-sounds``, ``SAYITERMANO_NO_APP_SPAWN=1``);
2. drives scripted takes over the duration — ``toggle`` → record →
   ``toggle`` (stop) → wait for the pipeline to settle. Model-free by
   construction: ``HF_HUB_OFFLINE=1`` + a private ``HF_HOME`` make the
   backend load fail fast offline, so every take exercises the full
   capture/stop/process/error lifecycle without downloading anything;
3. samples RSS, fd count, thread count and socket health every
   ``--interval`` seconds (default 15);
4. writes ``docs/quality/soak/<ISO-date>-<duration>m.json`` + the
   generated README table and EXITS NON-ZERO when a gate fails.

Gates (the tolerance numbers are part of the contract, visible in every
evidence file):

* **RSS growth** — after a warmup window (default the first 60 s, or
  ``--warmup``), the baseline is the median of the post-warmup window
  and the end value the median of the final window (last 20 % of
  samples, minimum 3). FAIL when ``(end - baseline) / baseline`` exceeds
  ``--rss-tolerance`` (default 0.15 = +15 %). Start-to-end AFTER
  warmup, so one-time loads (imports, caches) do not count.
* **Socket health** — every sample's ``status`` request must answer
  ``ok`` within the timeout; one failure fails the run.

Usage:
    .venv/bin/python scripts/soak_run.py                # 10 min, CI-short via --minutes
    .venv/bin/python scripts/soak_run.py --minutes 1    # smoke
Owner real-model soaks (CPU tiny, real mic) stay on the documented Q9
cadence: 2 h pre-release + 24 h lifecycle — this harness is the
model-free leg that any checkout/CI can reproduce.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs" / "quality" / "soak"

SAMPLE_INTERVAL_S = 15.0
DEFAULT_MINUTES = 10.0
DEFAULT_RSS_TOLERANCE = 0.15
DEFAULT_WARMUP_S = 60.0
SOCKET_TIMEOUT_S = 15.0

DAEMON_CONFIG = """\
[general]
language = "en"

[hotkey]
key = "F9"
cancel_key = "F12"

[model]
eager_warmup = false
name = "tiny"

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

# stub pw-record: streams 16 kHz s16 silence to the LAST argv path until
# SIGINT/SIGTERM (recorder.stop sends SIGINT). Python-based so the shim
# needs no audio stack on the host; the daemon's start probe sees PCM
# within ~60 ms.
PW_RECORD_SHIM = """\
#!{python}
import signal, sys, time
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
out = sys.argv[-1]
chunk = b"\\x00\\x00" * 1024  # 1024 frames of silence (~64 ms)
with open(out, "wb") as f:
    while True:
        f.write(chunk)
        f.flush()
        time.sleep(0.06)
"""


# ── /proc readers (pure text/list inputs — unit-tested with fakes) ─────────

def parse_status_kb(status_text: str, key: str) -> int | None:
    """VmRSS:/VmHWM: value in kB from /proc/<pid>/status content."""
    for line in status_text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def parse_status_threads(status_text: str) -> int | None:
    return parse_status_kb(status_text, "Threads")


def sample_process(pid: int) -> dict:
    """RSS (kB), open fds, threads of a pid — {} when it is gone."""
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        rss_kb = parse_status_kb(status, "VmRSS")
        threads = parse_status_threads(status)
        fds = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        return {}
    if rss_kb is None:
        return {}
    return {"rss_kb": rss_kb, "fds": fds if threads is not None else None,
            "threads": threads}


# ── gate math (pure — unit-tested) ──────────────────────────────────────────

def _window_median(samples: list[dict], values: list[float]) -> float | None:
    """Median RSS of the samples whose t_s falls in [lo, hi]."""
    rss = [s["rss_kb"] for s in samples
           if values[0] <= s.get("t_s", -1) <= values[1] and s.get("rss_kb")]
    return statistics.median(rss) if rss else None


def rss_growth(samples: list[dict], warmup_s: float) -> dict | None:
    """Start-to-end RSS growth AFTER warmup.

    baseline = median of post-warmup samples within one extra interval
    window; end = median of the final window (last 20 % of samples,
    minimum 3). Returns None when there are too few samples to judge
    (fewer than 2 post-warmup samples or no end window).
    """
    if not samples:
        return None
    post = [s for s in samples if s.get("t_s", 0) >= warmup_s
            and s.get("rss_kb")]
    if len(post) < 2:
        return None
    interval = max(samples[i + 1].get("t_s", 0) - samples[i].get("t_s", 0)
                   for i in range(len(samples) - 1)) if len(samples) > 1 else 0
    baseline = _window_median(samples, [warmup_s, warmup_s + max(interval,
                                                                SAMPLE_INTERVAL_S)])
    n_end = max(3, len(samples) // 5)
    end_ts = [s.get("t_s", 0) for s in samples]
    end = _window_median(samples, [sorted(end_ts)[-n_end], float("inf")])
    if not baseline or not end:
        return None
    return {"baseline_kb": round(baseline, 1), "end_kb": round(end, 1),
            "growth_frac": round((end - baseline) / baseline, 4),
            "warmup_s": warmup_s,
            "n_samples": len(samples), "n_post_warmup": len(post)}


def evaluate(samples: list[dict], takes: dict, warmup_s: float,
             rss_tolerance: float) -> dict:
    """The verdict: which gates passed, with the numbers visible."""
    reasons: list[str] = []
    growth = rss_growth(samples, warmup_s)
    if growth is None:
        rss_ok = False
        reasons.append(f"insufficient samples for RSS slope "
                       f"(warmup {warmup_s:.0f}s, {len(samples)} samples)")
    else:
        rss_ok = growth["growth_frac"] <= rss_tolerance
        reasons.append(
            f"RSS {growth['baseline_kb']:.0f}kB -> {growth['end_kb']:.0f}kB "
            f"after {warmup_s:.0f}s warmup = {growth['growth_frac'] * 100:+.1f}% "
            f"(tolerance +{rss_tolerance * 100:.0f}%): "
            + ("within" if rss_ok else "EXCEEDED"))
    bad = [s.get("t_s") for s in samples if not s.get("socket_ok")]
    socket_ok = not bad and bool(samples)
    if bad:
        reasons.append(f"socket failed to answer at t_s={bad}")
    elif not samples:
        reasons.append("no samples recorded")
    return {"growth": growth, "rss_tolerance": rss_tolerance,
            "rss_ok": rss_ok, "socket_ok": socket_ok,
            "takes_total": takes.get("total", 0),
            "takes_failed": takes.get("failed", 0),
            "passed": rss_ok and socket_ok, "reasons": reasons}


def markdown_summary(report: dict) -> str:
    """The soak README table row content (install-matrix pattern)."""
    rows = ["| Date | Duration | Takes | RSS after warmup | Growth | "
            "Tolerance | Verdict |", "|---|---|---|---|---|---|---|"]
    for run in report["runs"]:
        g = run.get("growth") or {}
        verdict = "PASS" if run["passed"] else "FAIL"
        link = f"[{run['file']}]({run['file']})"
        rows.append(
            f"| {run['date']} | {run['duration_min']:.0f}m "
            f"| {run['takes_total']} | "
            f"{(g.get('baseline_kb') or 0) / 1024:.0f} MB | "
            f"{(g.get('growth_frac') or 0) * 100:+.1f}% | "
            f"+{run['rss_tolerance'] * 100:.0f}% | {verdict} ({link}) |")
    return "\n".join(rows) + "\n"


# ── daemon driver (real subprocess — process-lane territory) ───────────────

@dataclass
class TakeStats:
    total: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)


def socket_request(path: Path, payload: dict) -> dict:
    """One JSON-line request over the control socket (client of the
    byte-identical protocol; standalone so the harness needs no import
    of the package under test)."""
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(SOCKET_TIMEOUT_S)
        sock.connect(str(path))
        sock.sendall(json.dumps(payload).encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("daemon closed the socket")
            buf += chunk
        return json.loads(buf)


class SoakDaemon:
    """The real daemon under soak: isolated env + stub recorder on PATH."""

    def __init__(self, sandbox: Path, log_to: Path):
        self.sandbox = sandbox
        self.log_to = log_to
        self.proc: subprocess.Popen | None = None
        (sandbox / "run").mkdir(parents=True, exist_ok=True)
        (sandbox / "bin").mkdir(parents=True, exist_ok=True)
        (sandbox / "config-home").mkdir(parents=True, exist_ok=True)
        (sandbox / "data").mkdir(parents=True, exist_ok=True)
        (sandbox / "cache").mkdir(parents=True, exist_ok=True)
        (sandbox / "hf-home").mkdir(parents=True, exist_ok=True)
        (sandbox / "config.toml").write_text(DAEMON_CONFIG, encoding="utf-8")
        shim = sandbox / "bin" / "pw-record"
        shim.write_text(PW_RECORD_SHIM.format(python=sys.executable),
                        encoding="utf-8")
        shim.chmod(0o755)
        self.socket = sandbox / "run" / "fluidvoice.sock"

    @property
    def env(self) -> dict:
        e = dict(os.environ)
        e.update({
            "PATH": f"{self.sandbox / 'bin'}:{e.get('PATH', '')}",
            "SAYITERMANO_CONFIG": str(self.sandbox / "config.toml"),
            "SAYITERMANO_SOCKET": str(self.socket),
            "XDG_CONFIG_HOME": str(self.sandbox / "config-home"),
            "XDG_DATA_HOME": str(self.sandbox / "data"),
            "XDG_CACHE_HOME": str(self.sandbox / "cache"),
            "SAYITERMANO_NO_APP_SPAWN": "1",
            "SAYITERMANO_SKIP_UPDATE_CHECK": "1",
            # session-agnostic: the daemon probes, never grabs (headless
            # X11 probe is fine without a display; Wayland vars cleared)
            "XDG_SESSION_TYPE": "x11",
            # model-free take cycle: backend load fails fast OFFLINE
            "HF_HUB_OFFLINE": "1",
            "HF_HOME": str(self.sandbox / "hf-home"),
        })
        e.pop("WAYLAND_DISPLAY", None)
        return e

    def start(self, timeout_s: float = 30.0) -> None:
        args = [sys.executable, "-m", "fluidvoice", "daemon",
                "--no-hotkey", "--no-sounds"]
        with open(self.log_to, "w", encoding="utf-8") as out:
            self.proc = subprocess.Popen(
                args, stdout=out, stderr=subprocess.STDOUT,
                text=True, cwd=str(ROOT), env=self.env)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.socket.exists():
                return
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"daemon died at startup (see {self.log_to})")
            time.sleep(0.2)
        self.stop()
        raise RuntimeError("daemon socket never appeared")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def take(self, record_s: float) -> dict:
        """One scripted take: toggle → record → stop → settle."""
        self._expect(lambda: socket_request(self.socket, {"action": "toggle"}))
        time.sleep(record_s)
        self._expect(lambda: socket_request(self.socket, {"action": "toggle"}))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = self._expect(
                lambda: socket_request(self.socket, {"action": "status"}))
            if not status.get("busy"):
                return status
            time.sleep(0.5)
        raise TimeoutError("take never settled (busy stuck)")

    def _expect(self, call) -> dict:
        resp = call()
        if not resp.get("ok"):
            raise RuntimeError(f"socket action failed: {resp}")
        return resp


def run_soak(minutes: float, interval_s: float, warmup_s: float,
             rss_tolerance: float, record_s: float = 2.0,
             keep_sandbox: Path | None = None) -> dict:
    """Drive one full soak; returns the report (verdict included)."""
    started = time.monotonic()
    takes = TakeStats()
    samples: list[dict] = []
    sandbox = keep_sandbox or Path(tempfile.mkdtemp(prefix="sayit-soak-"))
    daemon = SoakDaemon(sandbox, sandbox / "daemon.log")
    daemon.start()
    pid = daemon.proc.pid
    try:
        duration_s = minutes * 60.0
        next_take = started + min(warmup_s, interval_s)
        n = 0
        while True:
            now = time.monotonic()
            if now - started >= duration_s:
                break
            proc_sample = sample_process(pid)
            socket_ok = True
            latency_ms = None
            try:
                t0 = time.monotonic()
                daemon._expect(
                    lambda: socket_request(daemon.socket, {"action": "status"}))
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
            except Exception as exc:  # socket failure is a verdict input
                socket_ok = False
                with daemon.log_to.open("a", encoding="utf-8") as fh:
                    fh.write(f"soak: socket error: {exc}\n")
            samples.append({"t_s": round(now - started, 1),
                            "socket_ok": socket_ok,
                            "socket_latency_ms": latency_ms, **proc_sample})
            if time.monotonic() >= next_take:
                try:
                    daemon.take(record_s)
                    takes.total += 1
                except Exception as exc:  # take failure is a verdict input
                    takes.total += 1
                    takes.failed += 1
                    takes.errors.append(str(exc))
                next_take = time.monotonic() + interval_s
            n += 1
            wake = started + n * interval_s
            time.sleep(max(0.2, wake - time.monotonic()))
    finally:
        daemon.stop()
    verdict = evaluate(samples, {"total": takes.total,
                                 "failed": takes.failed}, warmup_s,
                       rss_tolerance)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True,
                            cwd=str(ROOT)).stdout.strip()
    report = {
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_min": minutes,
        "sample_interval_s": interval_s,
        "warmup_s": warmup_s,
        "take_mode": "model-free toggle/stop (stub pw-record silence + "
                     "HF_HUB_OFFLINE=1 fast backend-load failure)",
        "commit": commit,
        "python": sys.version.split()[0],
        "host": os.uname().nodename,
        "samples": samples,
        "takes": {"total": takes.total, "failed": takes.failed,
                  "errors": takes.errors[:10]},
        "daemon_log_tail": _tail(daemon.log_to, 40),
        "verdict": verdict,
    }
    if keep_sandbox is None:
        shutil.rmtree(sandbox, ignore_errors=True)
    return report


def _tail(path: Path, n: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []


def write_evidence(report: dict, evidence_dir: Path = EVIDENCE_DIR) -> Path:
    """One dated JSON per run + the generated README table."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    name = f"{date.today().isoformat()}-{report['duration_min']:g}m.json"
    path = evidence_dir / name
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    runs = []
    for f in sorted(evidence_dir.glob("*.json"), reverse=True):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        v = r.get("verdict", {})
        runs.append({"date": r.get("started_utc", "?")[:10],
                     "duration_min": r.get("duration_min", 0),
                     "takes_total": v.get("takes_total", 0),
                     "growth": v.get("growth"),
                     "rss_tolerance": v.get("rss_tolerance", 0),
                     "passed": v.get("passed", False), "file": f.name})
    readme = evidence_dir / "README.md"
    readme.write_text(
        "# Soak evidence (Q9)\n\n"
        "Dated daemon soak runs recorded by `scripts/soak_run.py` "
        "(model-free process lane: real daemon subprocess, stub `pw-record` "
        "silence, `HF_HUB_OFFLINE=1`). RSS/fd/thread sampling + socket "
        "health + tolerance math — the gate numbers live in each JSON's "
        "`verdict`. Table generated; do not hand-edit.\n\n"
        + markdown_summary({"runs": runs}), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="soak_run", description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=float, default=DEFAULT_MINUTES,
                    help=f"soak duration (default {DEFAULT_MINUTES:g})")
    ap.add_argument("--interval", type=float, default=SAMPLE_INTERVAL_S,
                    help="seconds between samples (default 15)")
    ap.add_argument("--warmup", type=float, default=DEFAULT_WARMUP_S,
                    help="warmup seconds excluded from the RSS gate "
                         f"(default {DEFAULT_WARMUP_S:.0f})")
    ap.add_argument("--rss-tolerance", type=float,
                    default=DEFAULT_RSS_TOLERANCE,
                    help="max RSS growth after warmup as a fraction "
                         f"(default {DEFAULT_RSS_TOLERANCE})")
    ap.add_argument("--record", type=float, default=2.0,
                    help="seconds of 'recording' per take (default 2)")
    ap.add_argument("--keep-sandbox", type=Path, metavar="DIR",
                    help="reuse a sandbox dir (debugging; not removed)")
    args = ap.parse_args(argv)

    effective_warmup = min(args.warmup, args.minutes * 60.0 * 0.5)
    report = run_soak(args.minutes, args.interval, effective_warmup,
                      args.rss_tolerance, record_s=args.record,
                      keep_sandbox=args.keep_sandbox)
    path = write_evidence(report)
    v = report["verdict"]
    for reason in v["reasons"]:
        print(reason)
    print(f"soak verdict: {'PASS' if v['passed'] else 'FAIL'} "
          f"({report['takes']['total']} takes, {len(report['samples'])} "
          f"samples) -> {path}")
    return 0 if v["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
