"""Update check + assisted upgrade (check-and-assist, never self-install).

Motivating incident (2026-09-04): the daily driver drifted to v0.5.0 by
hand-pipping into the relocated user venv while the old 0.2.1 deb kept
autostarting from /etc/xdg/autostart - the two-daemon XGrabKey race
silently killed the hotkey for a whole session. This module checks GitHub
releases (once per daemon start + daily), notifies exactly once per newer
release, and prints the exact copy-paste upgrade command for the install
method that is actually running. NOTHING is ever executed automatically.

Design: a pure, stdlib-only core (semver compare, release fetch, install-
method detection, upgrade-command rendering) so every consumer is unit-
testable offline. The daemon-side UpdateChecker runs its I/O on a thread
and never blocks startup; `sayit-ermano update` does one sync check.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import __version__, paths

GITHUB_LATEST_URL = "https://api.github.com/repos/acailic/SayItErmano/releases/latest"
RELEASES_URL = "https://github.com/acailic/SayItErmano/releases"
ONE_SHOT_INSTALLER = \
    "https://raw.githubusercontent.com/acailic/SayItErmano/linux/scripts/install-one-shot.sh"
ROUTE_GUIDE_URL = ("https://github.com/acailic/SayItErmano/blob/linux/"
                   "docs/guides/install.md#updates")
APT_SOURCES_FILE = Path("/etc/apt/sources.list.d/sayit-ermano.list")
APT_REPO_URL = "https://acailic.github.io/SayItErmano/apt"
CHECK_TIMEOUT_S = 10          # per the request spec
RECHECK_INTERVAL_S = 24 * 3600  # daily
SKIP_ENV = "SAYITERMANO_SKIP_UPDATE_CHECK"


def _default_log(msg: str) -> None:
    """Same format as daemon.log (update.py must not import daemon)."""
    print(f"[sayit-ermano] {time.strftime('%H:%M:%S')} {msg}",
          file=sys.stderr, flush=True)


def update_skipped() -> bool:
    """Env kill-switch: every network probe (daemon checker, doctor
    section, CLI update fetch) refuses when this is set."""
    return os.environ.get(SKIP_ENV, "") == "1"


# ---------------------------------------------------------------------------
# Pure core: semver
# ---------------------------------------------------------------------------

def parse_version(tag: str) -> tuple[int, ...]:
    """'v0.5.0' -> (0, 5, 0). Anything unparsable ('', 'abc', 'v', '0.5.0-1')
    -> () so callers fail safe (never treat junk as newer)."""
    s = (tag or "").strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    if not s:
        return ()
    parts: list[int] = []
    for chunk in s.split("."):
        if not chunk.isdigit():
            return ()
        parts.append(int(chunk))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """Tuple compare, zero-padded to equal length ('0.5' == '0.5.0',
    '0.10' > '0.9.1'). False when either side is unparsable."""
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


# ---------------------------------------------------------------------------
# Pure core: GitHub latest-release fetch (stdlib urllib, never raises)
# ---------------------------------------------------------------------------

def _parse_release(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None
    version = tag.strip().lstrip("vV")
    assets = []
    for a in data.get("assets") or []:
        if not isinstance(a, dict):
            continue
        name = a.get("name")
        url = a.get("browser_download_url") or a.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        assets.append({"name": name, "url": url,
                       "size": a.get("size"),
                       "digest": a.get("digest")})
    return {"tag": tag.strip(), "version": version,
            "url": data.get("html_url") or RELEASES_URL, "assets": assets}


def fetch_latest_result(url: str = GITHUB_LATEST_URL,
                        timeout: float = CHECK_TIMEOUT_S,
                        opener: Callable | None = None,
                        ) -> tuple[dict | None, str | None]:
    """GET the GitHub 'latest release' API. Returns (release, error):
    release is None on ANY failure (URLError/HTTPError/timeout/bad JSON/
    missing tag_name) - this never raises. `opener` (defaults to
    urllib.request.urlopen) is the test seam."""
    req = urllib.request.Request(
        url, headers={"User-Agent": f"SayItErmano/{__version__}",
                      "Accept": "application/vnd.github+json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            raw = resp.read()
        release = _parse_release(json.loads(raw.decode("utf-8", "replace")))
        if release is None:
            return None, "GitHub API response missing tag_name"
        return release, None
    except Exception as e:  # noqa: BLE001 - offline is a normal state
        return None, f"{type(e).__name__}: {e}"[:200]


def fetch_latest(url: str = GITHUB_LATEST_URL, timeout: float = CHECK_TIMEOUT_S,
                 opener: Callable | None = None) -> dict | None:
    """fetch_latest_result's release half (None on any failure)."""
    return fetch_latest_result(url, timeout, opener)[0]


def find_deb_asset(release: dict | None) -> dict | None:
    """The *_amd64.deb asset of a release, or None."""
    for a in (release or {}).get("assets") or []:
        if str(a.get("name", "")).endswith("amd64.deb"):
            return a
    return None


def deb_checksum(release: dict | None) -> str | None:
    """The sha256 digest string of the amd64 asset, when GitHub published
    one. Printed for the user to verify - nothing is checked automatically
    (out of scope)."""
    a = find_deb_asset(release)
    return (a or {}).get("digest") or None


# ---------------------------------------------------------------------------
# Pure core: install-method detection + upgrade commands
# ---------------------------------------------------------------------------

def detect_install_method(exe: Path | None = None,
                          home: Path | None = None,
                          apt_sources_file: Path | None = None) -> dict:
    """How was the running binary installed? Resolution order (first marker
    wins - this is the truth of *the binary that ran*, exactly what you
    want to upgrade):

      apt        deb layout (below) AND the signed apt repo configured
                 (sources.list.d entry shipped by the deb) - upgrades flow
                 through plain `apt update && apt upgrade`
      deb        sys.executable under /opt/sayit-ermano/ (bundled venv
                 from packaging/build-deb.sh)
      user-venv  under ~/.local/share/sayit-ermano/ (one-shot installer
                 layout)
      pipx       under $PIPX_HOME/venvs/ (default ~/.local/pipx)
      source     a venv whose parent has both .git and pyproject.toml
      unknown    anything else

    exe/home/apt_sources_file are injectable so tests build the marker
    trees under tmp_path instead of monkeypatching internals.
    """
    # abspath, NOT resolve(): venv `bin/python` entries are symlinks to the
    # system interpreter on Linux - following them would collapse every
    # layout to /usr/bin and break all detection (sys.executable is already
    # the truth of the binary that ran).
    exe = Path(os.path.abspath(exe or sys.executable))
    home = Path(home) if home is not None else Path.home()
    apt_sources_file = (apt_sources_file if apt_sources_file is not None
                        else APT_SOURCES_FILE)
    s = str(exe)

    deb_root = Path("/opt/sayit-ermano")
    if s.startswith(str(deb_root) + os.sep):
        if apt_sources_file.exists():
            return {"method": "apt", "marker": str(apt_sources_file)}
        return {"method": "deb", "marker": str(deb_root / "venv")}

    user_root = home / ".local" / "share" / "sayit-ermano"
    if s.startswith(str(user_root) + os.sep):
        return {"method": "user-venv", "marker": str(user_root)}

    venv_dir = exe.parent.parent  # .../venv/bin/python -> the venv root
    for pipx_home in _pipx_homes(home):
        if s.startswith(str(pipx_home) + os.sep + "venvs" + os.sep):
            return {"method": "pipx",
                    "marker": str(pipx_home / "venvs" / "sayit-ermano")}

    repo = venv_dir.parent
    if (repo / ".git").exists() and (repo / "pyproject.toml").exists():
        return {"method": "source", "marker": str(repo)}
    return {"method": "unknown", "marker": str(venv_dir)}


def _pipx_homes(home: Path) -> list[Path]:
    """Candidate pipx homes, in order: $PIPX_HOME, the classic
    ~/.local/pipx, and the XDG-flavor ~/.local/share/pipx (pipx >= 1.7
    distros default to the latter - both are checked so detection works
    on every layout without asking pipx)."""
    homes: list[Path] = []
    env = os.environ.get("PIPX_HOME")
    if env:
        homes.append(Path(env))
    homes.append(home / ".local" / "pipx")
    homes.append(home / ".local" / "share" / "pipx")
    return homes


def upgrade_command(method: str, release: dict | None) -> str:
    """The exact copy-paste block for one install method (pure; golden-
    tested). The deb block falls back to the user-venv block when the
    release has no amd64 asset."""
    if method == "apt":
        # the signed repo the deb configured (packaging/apt): updates are
        # ordinary apt traffic - config (~/.config) and history
        # (~/.local/share) are never touched by an upgrade
        return "sudo apt update && sudo apt upgrade -y sayit-ermano"
    if method == "deb":
        asset = find_deb_asset(release)
        if asset is not None:
            fname = asset["url"].rsplit("/", 1)[-1]
            return (f"curl -LO {asset['url']}\n"
                    f"sudo apt install -y ./{fname}")
    if method in ("deb", "user-venv"):
        # the documented one-shot installer: the supported user-install
        # path, also stops duplicate daemons (the 2026-09-04 incident fix)
        return f"curl -fsSL {ONE_SHOT_INSTALLER} | bash"
    if method == "pipx":
        return "pipx upgrade sayit-ermano"
    if method == "source":
        return "git pull && ./scripts/install.sh"
    # unknown: releases URL + the pip one-liner hint (README), then the
    # one-shot installer as the catch-all
    return (f"# see {RELEASES_URL}\n"
            "~/.local/share/sayit-ermano/venv/bin/pip install -U sayit-ermano\n"
            f"# or re-run the one-shot installer:\n"
            f"curl -fsSL {ONE_SHOT_INSTALLER} | bash")


# ---------------------------------------------------------------------------
# Dismissed/notification state (config_dir()/update-state.json)
# ---------------------------------------------------------------------------

def _read_state(path: Path | None) -> dict:
    path = path or paths.update_state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}  # malformed JSON -> treated as empty, never crashes


def _write_state(path: Path | None, state: dict) -> None:
    path = path or paths.update_state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # state is best-effort: never break a check on a bad disk


def dismiss_update(version: str | None = None,
                   state_path: Path | None = None) -> str:
    """Record <version> (default: last_seen, else the running version) as
    dismissed - the notification for exactly that version stops firing."""
    state = _read_state(state_path)
    ver = version or state.get("last_seen") or __version__
    state["dismissed"] = ver
    _write_state(state_path, state)
    return ver


# ---------------------------------------------------------------------------
# Manual, user-initiated check (no-telemetry ADR posture)
# ---------------------------------------------------------------------------

def manual_check(method_info: dict | None = None,
                 fetch: Callable[[], tuple[dict | None, str | None]]
                 | None = None) -> dict:
    """One check because the USER asked for it (Settings → About →
    "Check for updates", control route `update-check`). This is the
    no-telemetry-compliant surface: it runs regardless of updates.check
    (that switch governs the *background* checker only), honors the env
    kill-switch, never notifies, never writes state, never raises.
    Returns the same shape as UpdateChecker.status() plus route_guide."""
    if method_info is None:
        method_info = detect_install_method()
    st: dict = {"enabled": True, "checked": False, "latest": None,
                "update_available": None, "url": None, "error": None,
                "checked_at": None, "method": method_info["method"],
                "upgrade_command": "", "route_guide": ROUTE_GUIDE_URL}
    if update_skipped():
        st["error"] = f"skipped ({SKIP_ENV}=1)"
        return st
    fetch = fetch or fetch_latest_result
    try:
        release, error = fetch()
    except Exception as e:  # noqa: BLE001 - offline is a normal state
        release, error = None, f"{type(e).__name__}: {e}"[:200]
    st["checked"] = True
    st["checked_at"] = time.time()
    if release is None:
        st["error"] = error or "offline or GitHub API error"
        return st
    latest = release.get("version") or release.get("tag") or ""
    st["latest"] = latest
    if is_newer(latest, __version__):
        st["update_available"] = latest
        st["url"] = release.get("url")
        st["upgrade_command"] = upgrade_command(st["method"], release)
    return st


# ---------------------------------------------------------------------------
# Daemon-side checker (thread manager, micmon/lockmon pattern)
# ---------------------------------------------------------------------------

class UpdateChecker:
    """One-shot check at daemon start + a stoppable daily re-check timer.
    All network I/O happens on the checker thread; start() never blocks
    and nothing here raises into the daemon."""

    def __init__(self, cfg: dict, *, fetch: Callable[[], dict | None] = fetch_latest,
                 state_path: Path | None = None,
                 on_notify: Callable[[str, str], None] | None = None,
                 log: Callable[[str], None] = _default_log,
                 interval: float = RECHECK_INTERVAL_S):
        self.cfg = cfg
        self._fetch = fetch
        self._state_path = state_path
        self._on_notify = on_notify
        self.log = log
        self._interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._method = detect_install_method()
        self._status: dict = {"enabled": True, "checked": False,
                              "latest": None, "update_available": None,
                              "url": None, "error": None, "checked_at": None,
                              "method": self._method["method"],
                              "upgrade_command": "",
                              "route_guide": ROUTE_GUIDE_URL}
        self._enabled = bool(cfg.get("updates", {}).get("check", True))

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> bool:
        """Spawn the checker thread (immediate check, then daily). False
        when updates.check=false or the env kill-switch is set."""
        if not self._enabled:
            self._status["enabled"] = False
            return False
        if update_skipped():
            self._status["enabled"] = False
            return False
        self._thread = threading.Thread(target=self._run, name="fluidvoice-update",
                                        daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        self.check_now()
        while True:
            if self._stop.wait(self._interval):
                break
            self.check_now()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=CHECK_TIMEOUT_S + 2)

    # -- one check ------------------------------------------------------------

    def check_now(self) -> dict | None:
        """Sync check + state update + notify-once. Returns the new status
        snapshot, or None when the fetch failed (status()['error'] holds
        the reason; never raises, never notifies)."""
        if update_skipped():
            return None
        with self._lock:
            state = _read_state(self._state_path)
            state["last_check"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            release = None
            error = None
            try:
                release = self._fetch()
            except Exception as e:  # noqa: BLE001 - offline is a normal state
                error = f"{type(e).__name__}: {e}"[:200]
            st = dict(self._status)
            st["checked"] = True
            st["checked_at"] = time.time()
            st["error"] = None
            if release is None:
                st["latest"] = None
                st["update_available"] = None
                st["url"] = None
                st["upgrade_command"] = ""
                st["error"] = error or "offline or GitHub API error"
                self._status = st
                _write_state(self._state_path, state)  # records last_check
                return None
            latest = release.get("version") or release.get("tag") or ""
            state["last_seen"] = latest
            st["latest"] = latest
            if is_newer(latest, __version__):
                st["update_available"] = latest
                st["url"] = release.get("url")
                st["upgrade_command"] = upgrade_command(
                    self._method["method"], release)
                if (state.get("notified") != latest
                        and state.get("dismissed") != latest):
                    if self._on_notify is not None:
                        try:
                            self._on_notify(
                                "SayItErmano update available",
                                f"v{latest} — run 'sayit-ermano update' "
                                "for the upgrade command")
                        except Exception as e:  # noqa: BLE001
                            self.log(f"WARN update notification failed: {e}")
                    state["notified"] = latest
            else:
                st["update_available"] = None
                st["url"] = None
                st["upgrade_command"] = ""
            self._status = st
            _write_state(self._state_path, state)
            return dict(st)

    def status(self) -> dict:
        """Snapshot for the control socket (flat update_available/url are
        added by the daemon; the upgrade_command travels here so consumers
        never need the release dict again)."""
        with self._lock:
            return dict(self._status)
