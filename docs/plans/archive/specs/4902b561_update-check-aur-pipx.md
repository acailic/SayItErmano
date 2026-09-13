# Plan: Update check + assisted upgrade, pipx hygiene, AUR recipe

Roadmap "Later" items: *"Auto-updater (or packaged releases); onboarding"* and
*"Packaging: AUR, nix, pipx (deb is DONE)"*. Motivating incident 2026-09-04:
the daily-driver machine drifted to v0.5.0 by hand-pipping into the relocated
user venv while the old 0.2.1 deb kept autostarting from `/etc/xdg/autostart`
— the two-daemon XGrabKey race silently killed the hotkey for a session.
A check-and-assist updater prevents exactly this drift class, and a
duplicate-layout WARN in doctor catches the leftover-second-install case.

## Hard rules (project constraints — violating any of these fails review)

1. **CI is manual-only**: no new GitHub workflow, no push/PR triggers, no
   publish automation. All verification below is run by the builder/plan
   reader locally. PyPI publishing stays manual.
2. **No new runtime dependencies.** Networking is stdlib
   `urllib.request` with `User-Agent: SayItErmano/{__version__}` — copy the
   pattern from `fluidvoice/model_download.py:38-44` and `fluidvoice/ai/client.py`.
3. **No silent self-installation.** The updater informs; the human runs one
   command. `sayit-ermano update` never executes the upgrade command.
4. **Every phase ends with**
   `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.
5. `__init__.__version__` and `pyproject.toml` `version` stay manually in
   sync (0.5.0 today) — do not introduce dynamic versioning.
6. Out of scope: unattended upgrades, delta updates, beta channel,
   signature verification beyond printing the asset checksum, nix packaging,
   rewriting the deb, any onboarding work beyond the one "updates on"
   sentence.

---

## Design overview

New module `fluidvoice/update.py` (stdlib only, no GTK/X11 imports) with a
pure core so everything is unit-testable offline:

```python
GITHUB_LATEST_URL = "https://api.github.com/repos/acailic/SayItErmano/releases/latest"
CHECK_TIMEOUT_S = 10          # per the request spec
RECHECK_INTERVAL_S = 24 * 3600  # daily

def parse_version(tag: str) -> tuple[int, ...]      # "v0.5.0" -> (0, 5, 0)
def is_newer(latest: str, current: str) -> bool     # tuple compare, 0-padded

def fetch_latest(url: str = GITHUB_LATEST_URL, timeout: float = CHECK_TIMEOUT_S,
                 opener=None) -> dict | None
    # -> {"tag": "v0.6.0", "version": "0.6.0", "url": html_url,
    #     "assets": [{"name", "url", "size", "digest"}]}
    # None on ANY failure (URLError/HTTPError/timeout/bad JSON/missing tag).
    # Never raises. `opener` (defaults urllib.request.urlopen) is the test seam.

def find_deb_asset(release: dict) -> dict | None     # *_amd64.deb asset

def detect_install_method(exe: Path | None = None,
                          home: Path | None = None) -> dict
    # -> {"method": "deb"|"user-venv"|"pipx"|"source"|"unknown",
    #     "marker": "<the path that decided it>"}
    # exe defaults to Path(sys.executable).resolve(); home to Path.home().

def upgrade_command(method: str, release: dict | None) -> str
    # Pure: the exact copy-paste block for the method. Multi-line for deb.

class UpdateChecker:  # daemon-side thread manager (micmon/lockmon pattern)
    def __init__(self, cfg: dict, *, fetch=fetch_latest, state_path=None,
                 on_notify=None, log=daemon_log, interval=RECHECK_INTERVAL_S)
    def start(self) -> bool    # False when updates.check=false; best-effort
    def stop(self)
    def check_now(self) -> dict | None   # sync check + state + notify-once
    def status(self) -> dict             # snapshot for the control socket
```

**Install-method detection** (resolution order; first marker wins — this is
the truth of *the binary that ran*, which is exactly what you want to
upgrade):

| method | marker (resolved `sys.executable` under) |
|---|---|
| `deb` | `/opt/sayit-ermano/` (bundled venv from `packaging/build-deb.sh`) |
| `user-venv` | `~/.local/share/sayit-ermano/` (one-shot installer layout) |
| `pipx` | `$PIPX_HOME` (default `~/.local/pipx`) + `/venvs/sayit-ermano/` |
| `source` | a dir whose parent contains both `.git` and `pyproject.toml` (dev `.venv` in the repo) |
| `unknown` | anything else |

Tests inject fake `exe`/`home` paths built under `tmp_path` (marker
injection) — no monkeypatching of internals.

**Upgrade command blocks** (`upgrade_command`, exact strings, golden-tested):

- `deb` (asset URL + derived filename from the release assets):
  ```
  curl -LO https://github.com/acailic/SayItErmano/releases/download/v0.6.0/sayit-ermano_0.6.0-1_amd64.deb
  sudo apt install -y ./sayit-ermano_0.6.0-1_amd64.deb
  ```
  If the release has no amd64 asset, fall back to the user-venv block.
- `user-venv`: re-run the documented one-shot installer (README's canonical
  user-install path; it also stops duplicate daemons — the incident fix):
  ```
  curl -fsSL https://raw.githubusercontent.com/acailic/SayItErmano/linux/scripts/install-one-shot.sh | bash
  ```
- `pipx`: `pipx upgrade sayit-ermano`
- `source`: `git pull && ./scripts/install.sh`
- `unknown`: the releases URL + the one-shot installer line.

> Decision note: the prompt calls the user-install command "the pip-in-venv
> one-liner already documented in README". The README today documents the
> one-shot installer line and `./scripts/install.sh` — not a bare
> `pip install -U` line. Resolve it this way: the bundled user venv gets the
> one-shot installer line (the supported path, handles daemon restart +
> duplicate cleanup), and Phase 4 ADDS the pip one-liner to the README
> (`~/.local/share/sayit-ermano/venv/bin/pip install -U sayit-ermano`) for
> pip-style installs; `unknown` venvs print that pip line as a hint. Do not
> block on this; it's settled here.

Checksum printing (in scope): when a GitHub asset carries `digest`
(`sha256:…`), `sayit-ermano update` prints `sha256: …` under the command
block. Nothing is verified automatically.

**Dismissed-state file**: `paths.update_state_file()` →
`config_dir()/update-state.json`, contents
`{"last_check": "<iso>", "last_seen": "0.6.0", "notified": "0.6.0",
"dismissed": "0.6.0"}` (keys omitted until first written). Malformed JSON →
treated as empty, never crashes. Notification fires only when
`is_newer(latest, current)` and `notified != latest` and `dismissed != latest`
— so a newer release produces **exactly one** notification until a *newer*
one appears or the user re-checks after `--dismiss` of an older one.
`sayit-ermano update --dismiss` writes `dismissed = <latest or current>`.

**Config keys** `[updates] check = true, notify = true` must be wired through
`config.py`: `DEFAULTS`, `TEMPLATE` (with a comment block), `_SAVE_WHITELIST`,
`ALLOWED_SETTINGS`, `SETTING_BOOLS`. This wiring is *required*, not cosmetic:
`save_config` rewrites the file from the whitelist, so an unwired `[updates]`
section would be silently dropped the first time the Settings UI saves.

**Env kill-switch** `SAYITERMANO_SKIP_UPDATE_CHECK=1` disables every network
probe (daemon checker start, doctor section, CLI `update` prints "check
skipped" and still prints the detected-method block using `last_seen` state
or the releases URL). `tests/integration/conftest.py::isolated_env` sets it
so the integration daemon/CLI runs stay offline-fast.

---

## Phase 1 — core module + config/paths wiring

Files:
- **`fluidvoice/update.py` (new)**: everything in the design overview above.
  `UpdateChecker.start()` spawns a daemon thread: immediate `check_now()`,
  then loop `if self._stop.wait(interval): break; check_now()`
  (a stoppable daily timer). `check_now` holds a lock, calls `fetch`,
  updates state + `self._status`, and fires the single notification through
  `on_notify` (daemon injects a `ui.notify` wrapper honoring
  `cfg["notifications"]["enabled"] and cfg["updates"]["notify"]`,
  `timeout_ms=8000`, title "SayItErmano update available", body
  `v0.6.0 — run 'sayit-ermano update' for the upgrade command`).
  `status()` returns
  `{"enabled": bool, "checked": bool, "latest": str|None, "update_available":
  str|None, "url": str|None, "error": str|None, "checked_at": float|None,
  "method": str, "upgrade_command": str}` — the command is included here so
  consumers (CLI status, UI, doctor) never need the release dict again.
- **`fluidvoice/paths.py`**: add `update_state_file()` next to
  `dictionary_suggestions_file()`.
- **`fluidvoice/config.py`**: `DEFAULTS["updates"] = {"check": True,
  "notify": True}`; `[updates]` TEMPLATE section; whitelist/ALLOWED/BOOLS
  entries (see above).

Tests — **`tests/test_update.py` (new)**:
- `test_parse_version` + `test_is_newer`: parametrized table — equal
  (`0.5.0` vs `v0.5.0` → False), patch/minor/major bumps, length mismatch
  (`0.10` > `0.9.1`), garbage (`"", "abc", "v"` → parse to `()` so
  `is_newer` is False — fail safe, never notify on junk).
- `test_detect_install_method`: build the five marker trees under
  `tmp_path` (fake `opt/sayit-ermano/venv/bin/python`,
  `.local/share/sayit-ermano/venv/bin/python`,
  `PIPX_HOME`-monkeypatched `pipx/venvs/sayit-ermano/bin/python`, a repo
  `.venv` with `.git`+`pyproject.toml` siblings, a bare venv) and assert
  method + marker.
- `test_upgrade_command_golden`: fake release dict (tag `v0.6.0`, one asset
  `sayit-ermano_0.6.0-1_amd64.deb`) → exact two-line deb block; no-asset
  release → fallback; each other method's exact string.
- `test_fetch_latest_*`: opener stub returning bytes/HTTPError/timeout →
  None, no raise; good payload parses; missing `tag_name` → None.
- `test_check_notifies_exactly_once`: checker with stub fetch + notify
  recorder list; two `check_now()` calls with newer release → recorder
  length 1; a third call after state says notified → still 1; a newer-newer
  release → 2. `--dismiss` path: dismissed version suppresses.
- `test_offline_no_crash`: fetch raising `URLError` → `check_now` returns
  None, `status()["error"]` set, no notify, no exception.
- `test_state_file_corrupt`: garbage JSON → treated empty, works.
- `test_updates_config_roundtrip`: `DEFAULTS` has the keys;
  `save_config` → file contains `[updates]`; `load_config` reads them back.

Gate: unit suite green.

## Phase 2 — daemon, CLI `update`, doctor

- **`fluidvoice/daemon.py`**:
  - `__init__`: `self._update: Any = None`.
  - `run()`: after `self._start_lockmon()` (and before
    `_maybe_first_run_onboard()`), call
    `self._start_update_checker()` — best-effort like the tray: build
    `UpdateChecker(self.cfg, on_notify=<ui.notify wrapper>)`,
    `if checker.start(): self._update = checker; log("update check active
    (daily)")`. Never blocks startup (the checker thread does the I/O).
  - `handle_request` `"status"`: add
    `"update": (self._update.status() if self._update else
    {"enabled": False, "method": update_mod.detect_install_method()["method"]})`
    — plus convenience flat keys `"update_available": <version|None>` and
    `"update_url"` (CLI/UI read these; the dict carries the rest).
  - `shutdown()`: `if self._update: self._update.stop()`.
- **`fluidvoice/cli.py`**:
  - New subparser `update` (help: "check for a newer release and print the
    upgrade command for this install (nothing is executed)") with
    `--dismiss` (record the current/latest as dismissed — stops the
    notification) and `--json`.
  - Implementation: honor `SAYITERMANO_SKIP_UPDATE_CHECK`; do one
    `fetch_latest` (10 s); print:
    ```
    SayItErmano 0.5.0 (install: deb — /opt/sayit-ermano/venv)
    latest release: v0.6.0 — update available
    upgrade (copy-paste):
      curl -LO https://…/sayit-ermano_0.6.0-1_amd64.deb
      sudo apt install -y ./sayit-ermano_0.6.0-1_amd64.deb
    sha256: 0f3a…   (from the release assets, when published)
    ```
    On fetch failure: `latest release: unknown (offline or GitHub API
    error: <err>)` and still print the method + block built from
    `last_seen` state or the releases URL. Up-to-date case prints
    `up to date`. Exit 0 always (informational command).
  - `--dismiss` writes state via the module and prints `dismissed <ver>`.
  - `_describe` for `status`: append a line
    `update available: v0.6.0 (sayit-ermano update)` when present.
- **`fluidvoice/doctor.py`**:
  - New `def _update_lines(cfg: dict, *, check=None) -> list[str]` —
    `check` injectable (tests pass a stub; `run()` passes a partial that
    honors `SAYITERMANO_SKIP_UPDATE_CHECK` and `updates.check`). Emits:
    ```
    version: 0.5.0 -> latest 0.6.0 (update available: sayit-ermano update)
      install: deb (/opt/sayit-ermano/venv)
      upgrade: curl -LO … && sudo apt install -y ./…
    ```
    Failure → `version: 0.5.0 -> latest unknown (offline or GitHub API
    error)` **as the single WARN** — `ok` must NOT flip false on network.
    `updates.check=false` → `update check: disabled (updates.check = false)`.
  - **Duplicate-layout guard (the incident)**: when both
    `/opt/sayit-ermano` and `~/.local/share/sayit-ermano` exist →
    `  WARNING: both a system deb (/opt/sayit-ermano) and a user install
    (~/.local/share/sayit-ermano) are present — two daemons fight over the
    hotkey; remove one (sudo apt remove sayit-ermano, or drop the user
    layout)`. This check is pure path existence (testable, no network).
  - `run()`: print the section after the session block
    (`print("\nversion/updates:")` + lines).
- **`tests/integration/conftest.py`**: add
  `monkeypatch.setenv("SAYITERMANO_SKIP_UPDATE_CHECK", "1")` inside
  `isolated_env` (integration stays offline; the real daemon's checker is
  skipped there).

Tests (extend `tests/test_update.py`):
- Daemon wiring: construct `Daemon(cfg, recorder=StubRecorder(),
  backend_factory=stub, use_hotkey=False)` with an injected fake
  `self._update` (a stub with `.status()`) → `handle_request({"action":
  "status"})` contains `update_available`/`update_url`; without a checker →
  `update_available is None`. Reuse `test_daemon.py` stub idioms.
- CLI: `main(["update"])` with `update.fetch_latest` monkeypatched and
  `detect_install_method` monkeypatched to each of the two required methods
  (deb, user-venv) → `capsys` output contains the exact expected command
  line (`sudo apt install -y ./sayit-ermano_0.6.0-1_amd64.deb` /
  `install-one-shot.sh | bash`). Also the offline path (fetch → None) and
  `--dismiss` writing state.
- Doctor: `_update_lines(DEFAULTS, check=lambda: None)` → the unknown/WARN
  line; duplicate-layout WARN via tmp `home` monkeypatch; disabled-config
  line.

Gate: unit suite green.

## Phase 3 — GTK surfacing (history status, Settings About, onboarding)

- **`fluidvoice/gtkui/main_window.py`**: in the status `Gtk.Box` add
  `self.update_lbl = Gtk.Label(css_classes=["dim-label"])` (appended after
  `today_lbl`; hidden by default). In `_apply_status(st)`, when
  `st.get("update_available")`: show it with text
  `update available: v0.6.0` + `warning` css class, tooltip =
  `st["update"]["upgrade_command"]`; else hide. (Command comes from the
  status payload — no network in the UI.)
- **`fluidvoice/gtkui/settings_window.py`**: About page (~line 1598) gains
  `Adw.ActionRow(title="Update", subtitle="…")` updated from the client's
  status poll: `v0.6.0 available — run 'sayit-ermano update'` or
  `up to date` / `checks disabled`.
- **`fluidvoice/gtkui/onboarding.py`**: one new dim label line (the ONLY
  onboarding change — scope rule):
  **"Updates: SayItErmano checks GitHub once a day for a newer release and
  notifies you (`sayit-ermano update` prints the upgrade command; disable
  with `updates.check = false` in the config)."** Static text, set in
  `__init__` alongside the other labels — no client call needed.

Tests (extend `tests/test_gtkui.py`, StubClient pattern, gi-skipped where
PyGObject is absent): `_apply_status` with/without `update_available`
(label visible + tooltip text); onboarding window contains the sentence
(mirror `TestOnboardingWindow`).

Gate: unit suite green.

## Phase 4 — pipx / PyPI hygiene

Goal: `pipx install` of a locally built wheel produces a working install
with correct entry points + data files. Fix whatever metadata blocks it.

- **Verify + fix `pyproject.toml`**:
  - Build: `uv build` (operator env has uv — justfile depends on it) or
    `.venv/bin/python -m pip wheel --no-deps -w dist .`; also an sdist via
    `uv build --sdist`.
  - Inspect wheel contents: `unzip -l dist/sayit_ermane*.whl` must list
    `fluidvoice/assets/sfx/*.m4a`, `assets/icon.png`, `assets/icons/**`
    (incl. `symbolic/actions/*.svg`), `assets/providers/*`. The
    package-data globs live under `"fluidvoice"` with nested paths —
    confirm they land; if any miss, split them per-subpackage
    (`"fluidvoice.assets.sfx" = ["*"]`, …). **Document in the plan
    comments what was found** (the gate needs the check, not a pre-fix).
  - Add `[project.urls]` (`Homepage`, `Issues` → the GitHub repo) — the
    one metadata nicety PyPI/pipx surfaces.
- **`scripts/verify-pipx.sh` (new, small, NOT wired to any CI)** — the
  sandboxed check the done-criteria asks to document:
  ```bash
  set -euo pipefail
  cd "$(dirname "$0")/.."
  uv build --wheel  # or: pip wheel --no-deps -w dist .
  WHEEL="$(ls -t dist/sayit_ermano-*.whl | head -1)"
  PIPX_BIN="${PIPX_BIN:-$HOME/.local/pipx/venvs/sayit-ermano/bin}"
  pipx install --force "$WHEEL"       # or: pipx install --force --python .venv/bin/python "$WHEEL"
  "$PIPX_BIN/sayit-ermano" --version            # == pyproject version
  "$PIPX_BIN/python" -m fluidvoice --version    # module path works too
  "$PIPX_BIN/python" -c "from fluidvoice.update import detect_install_method as d; \
      m=d(); assert m['method']=='pipx', m; print('method:', m)"
  "$PIPX_BIN/python" -c "import fluidvoice.ui, importlib.resources as r; \
      print(list(r.files('fluidvoice.assets.sfx').iterdir()))"   # data files present
  pipx uninstall sayit-ermano
  ```
  (This doubles as the proof that pipx detection works end-to-end.) If pipx
  is absent in the sandbox, the script falls back to
  `python -m venv /tmp/sayit-pipx-check && …/bin/pip install "$WHEEL"`
  with the same assertions — keep that branch in the script.
- **README**: new "### pipx / pip (any distro)" subsection under
  Installation: `pipx install sayit-ermano` (after PyPI publish — note
  publishing is manual and may not have happened yet, so also show
  `pipx install git+https://github.com/acailic/SayItErmano.git@linux` and
  `pipx install ./dist/sayit_ermano-…whl` from a local build), the mention
  that upgrades are `pipx upgrade sayit-ermano`, and the bundled-venv pip
  one-liner for user installs
  (`~/.local/share/sayit-ermano/venv/bin/pip install -U sayit-ermano`).

Gate: unit suite green; `scripts/verify-pipx.sh` run end-to-end by the
builder, output pasted into the PR/commit notes; README section merged.

## Phase 5 — AUR recipe (instructions only, we do NOT publish)

- **`packaging/aur/PKGBUILD` (new)** — a `-bin` package from the release
  asset (matches "source = release asset"):
  ```bash
  # Maintainer: upstream does not publish to the AUR (project rule: manual
  # releases only). This recipe is maintained here for whoever wants to
  # adopt it — see packaging/aur/README.md.
  pkgname=sayit-ermano-bin
  pkgver=0.5.0
  pkgrel=1
  pkgdesc="Local voice dictation with AI polish (community Linux port of FluidVoice)"
  arch=('x86_64')
  url="https://github.com/acailic/SayItErmano"
  license=('GPL-3.0-or-later')
  depends=('python' 'pipewire-audio-utils' 'xdotool' 'xclip' 'libnotify'
           'python-gobject' 'gtk4' 'libadwaita')
  provides=('sayit-ermano' 'fluidvoice-linux')
  conflicts=('sayit-ermano' 'fluidvoice-linux')
  replaces=('fluidvoice-linux')
  source_x86_64=("${pkgname}_${pkgver}.deb::${url}/releases/download/v${pkgver}/sayit-ermano_${pkgver}-1_amd64.deb")
  sha256sums_x86_64=('SKIP')   # fill from the release asset digest when bumping
  noextract=("${pkgname}_${pkgver}.deb")
  package() {
      bsdtar -xOf "${srcdir}/${pkgname}_${pkgver}.deb" data.tar.\* | bsdtar -xJf- -C "${pkgdir}"
  }
  ```
  (bsdtar handles the deb's data.tar.zst; verify the exact tarball flavor
  with `dpkg-deb --info dist/*.deb` while building and adjust the flag.
  If `data.tar.zst` needs zstd support, `depends+=('zstd')`.)
- **`packaging/aur/README.md` (new)**: maintainer instructions — how to bump
  (`pkgver`, asset URL, `sha256sums` from the release page), regenerate
  `.SRCINFO` with `makepkg --printsrcinfo > .SRCINFO` (committed for
  adopters), lint with `namcap PKGBUILD` when available, an explicit "not
  published by upstream — manual releases only (project rule)" note, and a
  pointer back to the README install section.
- **`packaging/aur/.gitignore`**: `pkg/`, `src/`, `*.deb`, `*.pkg.tar.*`.
- **README**: one line in the install area — "Arch: an AUR recipe is
  maintained in `packaging/aur/` (`sayit-ermano-bin`); community-adopted,
  not published by us."

Gate: PKGBUILD linted (`namcap packaging/aur/PKGBUILD` if namcap exists —
else the manual-review note goes in the PR description covering: correct
sha256 handling, no `/etc/xdg/autostart` surprises (the deb ships it —
call it out in the AUR README as intended), arch/deps mapping sanity) and
`makepkg --printsrcinfo` parses. Unit suite green (nothing code-side).

## Phase 6 — docs wrap-up

- **`docs/ROADMAP.md`**: tick both Later items with DONE notes in the
  established style (`[x] Auto-updater … — DONE: check-and-assist updater
  (fluidvoice/update.py; daily GitHub check, one notification, copy-paste
  upgrade command per install method, doctor drift WARN; no silent
  self-update)`; `[x] Packaging: AUR + pipx … — DONE (nix still open)`).
- **README**: `sayit-ermano update` in the "Useful commands" block; a short
  "Updates" paragraph (what checks happen, how to disable, `--dismiss`).
- Final gate: full unit suite
  (`.venv/bin/python -m pytest -q tests --ignore=tests/integration`) green;
  optional: `-m "integration and not desktop"` with
  `SAYITERMANO_SKIP_UPDATE_CHECK` left *unset* in one manual run to prove
  the live path works against the real GitHub API (manual, documented).

---

## Done-criteria checklist (maps 1:1 to the request)

- [ ] Phased plan under `specs/`; each phase leaves the unit suite green.
- [ ] Network mocked → a newer release produces **exactly one**
      notification; `status` shows `update_available` (+url);
      `sayit-ermano update` prints a correct copy-paste command for BOTH
      install methods (deb + user-venv), unit-detected via marker
      injection.
- [ ] No network / API failure degrades anything: one WARN in doctor at
      most; daemon startup never blocks; CLI `update` still prints the
      method block.
- [ ] `scripts/verify-pipx.sh` (documented sandboxed check) — wheel builds,
      pipx install, `--version` succeeds, detection returns `pipx`, data
      files present.
- [ ] PKGBUILD lints (namcap or documented manual review note); README
      AUR + pipx sections merged; onboarding has the one updates sentence.
- [ ] No CI/publish automation added anywhere.

## Key files touched (summary)

| file | change |
|---|---|
| `fluidvoice/update.py` | NEW — semver, fetch, detection, commands, UpdateChecker, state |
| `fluidvoice/paths.py` | + `update_state_file()` |
| `fluidvoice/config.py` | `[updates]` in DEFAULTS/TEMPLATE/whitelists/bools |
| `fluidvoice/daemon.py` | checker thread, status fields, shutdown |
| `fluidvoice/doctor.py` | `_update_lines` + section + duplicate-layout WARN |
| `fluidvoice/cli.py` | `update` subcommand (+`--dismiss`, `--json`), status line |
| `fluidvoice/gtkui/main_window.py` | status-row update label |
| `fluidvoice/gtkui/settings_window.py` | About "Update" row |
| `fluidvoice/gtkui/onboarding.py` | the one updates sentence |
| `tests/test_update.py` | NEW — all unit coverage listed per phase |
| `tests/test_gtkui.py` | status/onboarding assertions |
| `tests/integration/conftest.py` | `SAYITERMANO_SKIP_UPDATE_CHECK=1` |
| `scripts/verify-pipx.sh` | NEW — sandboxed pipx verification |
| `packaging/aur/{PKGBUILD,README.md,.gitignore}` | NEW — AUR recipe |
| `pyproject.toml` | urls + any data-file fixes found in Phase 4 |
| `README.md`, `docs/ROADMAP.md` | pipx/AUR/update docs + ticks |
