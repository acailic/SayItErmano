#!/usr/bin/env python3
"""Soak monitor (upstream #917/#948: multi-day memory growth, idle CPU).

Samples the running daemon's RSS + CPU (via /proc) and writes a CSV row
every interval. Leave it running for hours-to-days; plot with:

    python scripts/soak.py --hours 24
    column -t -s, /tmp/sayit-ermano-soak.csv | tail
    # or: gnuplot -p -e "set datafile separator ','; \\
    #  plot '/tmp/sayit-ermano-soak.csv' using 1:3 with lines title 'RSS MB'"

Caveats: reads /proc/<pid>/stat for utime+stime (jiffies, 100/s), so CPU
column is cumulative seconds. Idle expectation: flat RSS after model
load, CPU growing by well under a minute per hour.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import time
from pathlib import Path


def daemon_pid() -> int | None:
    out = subprocess.run(["pgrep", "-f", "sayit-ermano daemon"],
                         capture_output=True, text=True).stdout.split()
    # the systemd unit may show as .../venv/bin/python -m fluidvoice daemon
    if not out:
        out = subprocess.run(["pgrep", "-f", "fluidvoice daemon"],
                             capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def sample(pid: int) -> tuple[float, float] | None:
    try:
        rss_kb = 0
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
        with open(f"/proc/{pid}/stat") as fh:
            parts = fh.read().rsplit(")", 1)[1].split()
        cpu_s = (int(parts[11]) + int(parts[12])) / 100.0  # utime+stime
        return rss_kb / 1024.0, cpu_s
    except (OSError, IndexError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between samples (default 60)")
    ap.add_argument("--hours", type=float, default=1.0,
                    help="how long to run (default 1)")
    ap.add_argument("--out", type=Path,
                    default=Path("/tmp/sayit-ermano-soak.csv"),
                    help="CSV destination")
    args = ap.parse_args()

    pid = daemon_pid()
    if pid is None:
        print("no sayit-ermano daemon found", flush=True)
        return 1
    deadline = time.monotonic() + args.hours * 3600
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "rss_mb", "cpu_s"])
        t0 = time.monotonic()
        n = 0
        while time.monotonic() < deadline:
            s = sample(pid)
            if s is None:
                print(f"daemon {pid} gone", flush=True)
                return 1
            w.writerow([round(time.monotonic() - t0, 1),
                        round(s[0], 1), round(s[1], 1)])
            n += 1
            if n % 10 == 0:
                fh.flush()
                print(f"{n} samples · rss {s[0]:.0f} MB · "
                      f"cpu {s[1]:.0f} s", flush=True)
            time.sleep(args.interval)
    print(f"wrote {n} samples to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
