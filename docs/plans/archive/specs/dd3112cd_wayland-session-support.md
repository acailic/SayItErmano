# Wayland Session Support — ROADMAP v0.3 (phased plan)

**Spec:** `specs/dd3112cd_wayland-session-support.md`
**Goal:** the daemon becomes genuinely useful on a Wayland session — insertion via
wtype/ydotool, clipboard via wl-clipboard with restore, DE-shortcut hotkey assist,
notification preview, doctor capability matrix — while the X11 experience stays
byte-identical. Wayland is **additive**: no X11 code path is rewritten, only gated.

---

## 0. Verified current state (planning recon, read from the code 2026-09-XX)

The task prompt's premise "daemon start on a Wayland session dies in hotkey setup"
is **stale — the daemon already survives**. Verified first-hand:

- `daemon.py:_start_hotkey` (~:751-822) catches `HotkeyError` per listener,
  logs `WARN hotkey unavailable: …` and notifies *"Bind a DE shortcut to
  `sayit-ermano toggle` instead"* — startup continues. The error text raised at
  `hotkey.py:391` / `:797` is `cannot open X display ({e}) - is this an X11 session?`.
  Mouse-PTT setup (`daemon.py:~874-906`) degrades the same way.
- `overlay.py:FluidOverlay.__init__` (~:799-838) catches `_setup` failure →
  tears down the X display → `using_overlay == False` → every `show()` goes to
  `NotifyPreview` (`preview.py:397-437`), which is pure `notify-send`
  (display-server-neutral). `_start_preview` (`daemon.py:1292-1355`) is also
  try/except-wrapped.
- `tray.py` is StatusNotifierItem over D-Bus — display-server-neutral, already
  best-effort ("running headless" without a tray host).
- **The real Wayland failure is insertion.** With no xdotool/xclip:
  `insert_typed` → `_run(["xdotool", …])` → `InsertError("required tool not
  found: xdotool")` → `DictationPipeline._insert` (daemon.py:~212-218) notifies
  "Could not type text" and calls `insertion.clipboard_fallback(text)` →
  `copy_to_clipboard` → `xclip` missing → **silent no-op; text only survives in
  history**. Same for `press_key` (spoken-send Enter), `copy_to_clipboard`
  (paste-last / always-copy), and `rewrite.capture_selection`
  (xclip-based, rewrite.py:46).
- `insertion.active_window_class()` returns `None` without xdotool → terminal
  detection (`general.terminal_apps`) and per-app prompt matching silently
  degrade on Wayland.
- `doctor.py` already prints `session:` + `WAYLAND_DISPLAY` (:373-380) and a
  one-line Wayland hint, and force-sets `ok=False` for any wayland session
  (:378-379); the tools list checks xdotool/xclip unconditionally (:405-421).
- No session probing anywhere else (repo-wide grep: only doctor + tests read
  `XDG_SESSION_TYPE` / `WAYLAND_DISPLAY`).
- Packaging gotcha: `scripts/install.sh:20-32` and
  `systemd/sayit-ermano.service:16-17` bake `Environment=DISPLAY` into the
  user unit — on a Wayland login the unit can carry a stale `DISPLAY` even
  though the session is wayland. The probe precedence (invariant 1) tolerates
  this (`XDG_SESSION_TYPE` / `WAYLAND_DISPLAY` win over a bare `DISPLAY`), but
  Phase 1 must confirm under the real systemd unit that those vars propagate
  past the baked line; if they do not, add them to the unit (additive only).

So Phase 1 is *formalizing* degradation into a declared capability model, not
resurrecting a dead daemon. A live confirmation of all of the above is item 1 of
the smoke checklist (§9) — do it first, note results in STATUS.md.

### Hard invariants (apply to every phase)

1. **Gate on the session probe only.** A Wayland branch is taken iff
   `session.current()` says wayland (see precedence below). Never branch on
   "xdotool missing" or "DISPLAY unset" — headless CI and the whole existing
   test suite rely on the xdotool code paths being taken when the env is
   X11/unknown. Probe precedence: explicit `XDG_SESSION_TYPE` → else
   `WAYLAND_DISPLAY` set → wayland; else `DISPLAY` set → x11; else `unknown`
   → behave exactly as today (legacy X11 attempt).
2. **Public API unchanged.** `insert_text`, `insert_typed`, `insert_paste`,
   `press_key`, `copy_to_clipboard`, `active_window_class`, `capture_selection`
   keep their signatures; new behavior enters via keyword args defaulting to
   today's X11 behavior. `DictationPipeline` and its test stubs are untouched.
3. **Each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
   green** and X11 behavior identical (the existing suite is the regression net —
   zero edits to existing assertions except where a doc string/assert explicitly
   gains wayland siblings).
4. Command construction for every external tool lives in one place per tool
   (small `_cmd_*` builders) so live-verified flag fixes are one-line.

### Tool facts to encode (verify each live during the smoke pass, §9)

- `wtype` uses the zwp virtual-keyboard protocol: works on wlroots (sway…) and
  KDE; **GNOME does not implement it** → `auto` resolution must skip wtype when
  `XDG_CURRENT_DESKTOP` contains `gnome`. `wtype -d <ms>` delays keystrokes;
  `wtype -k <keysym>` presses combos. Leading-dash text has the same argv hazard
  as xdotool → route to the paste path (mirror `insertion.py:insert_typed`).
- `ydotool` works on every compositor via uinput, but needs `ydotoold` running
  and `/dev/uinput` permission (socket path env `YDOTOOL_SOCKET`). Key syntax is
  `ydotool key <code>:<state>` (linux key codes) → keep a small spec→codes table
  (ctrl/shift/alt/super + v/a/c/enter/esc/tab…) centrally, unit-tested.
- `wl-copy` / `wl-paste` (wl-clipboard): `wl-paste --no-newline` snapshot,
  `wl-paste --list-types` type probe, `wl-copy --type <t>` restore. wl-copy
  forks and serves (like xclip) — same settle discipline. **No hygiene-marker
  support** (documented divergence: Wayland clipboard managers will see
  dictation flashes). **Paste verification via selection read-observation is
  impossible on Wayland** (no cross-client observation) → fixed delay + doctor
  note, exactly as the prompt requires.

---

## 1. Phase 1 — session probe + honest startup + capability matrix

**New file `fluidvoice/session.py`** (pure, no I/O beyond env/`shutil.which`
injected for tests):

- `@dataclass SessionInfo`: `type: str` ("x11"|"wayland"|"unknown"),
  `desktop: str` (lower-cased `XDG_CURRENT_DESKTOP`, first token),
  `wayland_display: bool`, `x11_display: bool`; property `is_wayland`.
- `probe(env: Mapping = os.environ) -> SessionInfo` — precedence as invariant 1.
- `current() -> SessionInfo` — cheap per-call env read (no caching; must stay
  test-overridable via monkeypatched env).
- `capabilities(info: SessionInfo, which: Callable[[str], str|None] = shutil.which)
  -> dict[str, str]` — one entry per capability, value = resolved backend:
  - `hotkey`: "x11-grab" | "de-shortcut" (assist)
  - `insertion`: "xdotool" | "wtype" | "ydotool" | "wl-clipboard-only" | "unavailable"
    (auto-order: session x11 → xdotool; wayland → tool per
    `insertion.wayland_tool` config + GNOME-wtype exclusion)
  - `clipboard`: "xclip" | "wl-clipboard" | "unavailable"
  - `overlay`: "x11-pill" | "notifications"
  - `preview`: same as overlay
  - `tray`: "sni"
  - `app-hint`: "xdotool-wmclass" | "unavailable" (AT-SPI later)
  Keep it a pure function of (info, which) so it is trivially unit-testable and
  reusable by daemon, doctor and the settings page.

**`fluidvoice/daemon.py`:**

- In `run()`, right after the version log: `self._session = session.probe()`;
  log one line, e.g.
  `session: wayland (gnome) - capabilities: insertion=ydotool hotkey=de-shortcut overlay=notifications clipboard=wl-clipboard`.
  On x11 the line is `session: x11 - capabilities: …` (full experience).
- On wayland only: `session.ensure_toggle_script()` (see paths below) and log
  the script path — this is the command the user binds.
- `handle_request` `"status"` action (~daemon.py:1110): add
  `"session": {"type":…, "desktop":…}` and `"capabilities": capabilities(...)` —
  additive keys, JSON consumers unaffected.
- No other daemon change: hotkey/overlay/tray already degrade (§0).

**`fluidvoice/paths.py`:** add `bin_dir() -> data_dir()/"bin"` and
`toggle_script() -> bin_dir()/"sayit-ermano-toggle"`.

**`fluidvoice/session.py::ensure_toggle_script()`** (or a small helper there):
idempotent best-effort write of a `#!/bin/sh` wrapper for `sayit-ermano toggle`
— resolution order: sibling `sayit-ermano` next to `sys.executable` →
`shutil.which("sayit-ermano")` → `"<sys.executable> -m fluidvoice"`. chmod 0755.
Never raises.

**`fluidvoice/cli.py`:** `_describe(resp)` gains a
`session: wayland — insertion: wtype — hotkey: DE shortcut — overlay: notifications`
line when `resp["session"]["type"] == "wayland"` (nothing new on x11).
`--json` gets the fields for free.

**`fluidvoice/doctor.py`:**

- New helper `_session_matrix_lines(cfg) -> list[str]` replacing the current
  session block (:373-380): per-capability rows with the resolved backend and
  per-tool found/missing, e.g.
  `insertion: ydotool (wtype missing, xdotool n/a on wayland)` /
  `insertion: UNAVAILABLE - install wtype or ydotool`.
- Tools list (:405-421): add `wtype`, `ydotool`, `wl-copy`, `wl-paste` rows
  (why-text mentions compositor fit; ydotool row notes `ydotoold` + uinput).
- Exit-code honesty: `ok=False` for wayland **only when** the insertion
  capability is `unavailable` (replacing the blanket `ok=False` at :378-379);
  everything else informational.
- Doctor line for the GNOME/KDE/COSMIC shortcut instructions (content shared
  with the settings page, §3 — put the per-DE text in one helper both use).

**`tests/conftest.py`:** extend the existing session XDG isolation block with
`os.environ["XDG_SESSION_TYPE"] = "x11"` (+ `WAYLAND_DISPLAY` popped) so the
entire existing suite pins the X11 branch regardless of the dev machine's real
session. One-line change, heavily commented (this is what keeps invariant 1
true for tests).

**New tests (`tests/test_wayland_capabilities.py`):**
probe precedence (5 cases: explicit wayland / explicit x11 / wayland-display-only
/ display-only / neither); capabilities() matrix for
x11+xdotool, wayland+wtype, wayland+ydotool-only, wayland+neither
(→ `wl-clipboard-only`, then `unavailable`), wayland+gnome-desktop ignores
wtype in auto; `ensure_toggle_script` content + idempotency (tmp XDG dirs);
daemon status carries session/capabilities (stub-daemon pattern from
test_daemon.py); doctor matrix lines via the `_session_matrix_lines` helper
(test_infra.py pattern); CLI `_describe` line.

**Gate:** suite green; on an X11 box nothing observable changed except the new
`session:` log line and additive status keys.

---

## 2. Phase 2 — insertion (highest value)

**`fluidvoice/insertion.py`** (all wayland code additive, X11 paths untouched):

- `_resolve_wayland_tool(cfg, which=shutil.which) -> tuple[str|None, str]`
  returning `(tool, reason)`: honors `insertion.wayland_tool`
  (`auto|wtype|ydotool`, default `auto`), applies the GNOME-wtype exclusion,
  falls back `wtype → ydotool → None`. The `reason` string feeds doctor/notice.
- Command builders (single source of truth, unit-asserted):
  `_wtype_type_cmd(text, delay_ms)`, `_wtype_key_cmd(spec)`,
  `_ydotool_type_cmd(text, delay_ms)`, `_ydotool_key_cmd(spec)` (uses the
  spec→code table), `_wl_copy_args(type_)`, `_wl_paste_args()`.
  Spec→ydotool table covers the specs the codebase can emit today:
  `ctrl+v`, `ctrl+shift+v`, `enter`, `shift+enter`, `ctrl+enter`, `escape`.
  Unknown spec → `InsertError` (loud, not a wrong keystroke).
- `insert_typed(text, delay_ms, *, tool: str|None = None)` — `tool=None` keeps
  today's xdotool path; `tool="wtype"`/`"ydotool"` builds the tool command.
  Leading-dash text still routes to paste (same guard as X11, extended to both
  tools).
- `press_key(spec, *, tool: str|None = None)` — same pattern; spoken-send,
  paste-last and command-mode reruns get Wayland for free.
- `copy_to_clipboard(text, *, wayland: bool = False)` / `clipboard_fallback` —
  `wl-copy` branch (stdin bytes, settle like `_clipboard_write`).
- `_insert_paste_wayland(text, *, key, tool, on_notice)`: snapshot
  (`wl-paste --no-newline` + `--list-types` text probe, mirroring
  `_clipboard_snapshot` semantics), `wl-copy` the text, keystroke via the tool,
  **fixed delay** (`WAYLAND_PASTE_SETTLE_S ≈ 0.45`, module-level like the other
  constants), restore via `wl-copy --type <previous-type>`; no read-observation
  (impossible), no hygiene markers (divergence) — the docstring says both and
  doctor repeats it. Restore is best-effort blind; `on_notice` on failure.
- `insert_text(text, cfg, wm_class=None, on_notice=None)` — unchanged
  signature; first line becomes
  `if session.current().is_wayland: return _insert_text_wayland(text, cfg, on_notice)`.
  `_insert_text_wayland` mirrors the mode/threshold/leading-dash routing of the
  X11 body (mode from `cfg["insertion"]`, paste if `mode=="paste"` or
  `len>threshold` or leading dash; `wm_class` is always None on wayland →
  terminal quirks inert — documented). Degradation ladder:
  tool+type → tool+wl-paste → `wl-copy` + `on_notice("Copied to clipboard —
  paste manually")` + return `"clipboard-fallback"` → (no wl-copy either)
  raise `InsertError` (pipeline notifies; history still saved).
  Strategy strings stay `"typed"` / `"paste"` / `"clipboard-fallback"` so
  history/UI need no changes.
- `active_window_class()`: on wayland return `None` immediately (avoids
  Xwayland giving a misleading WM_CLASS of an X11 window while the focus is
  elsewhere).

**`fluidvoice/rewrite.py`:** `capture_selection(tool-aware)`: wayland branch —
ctrl+c via the resolved tool, settle, `wl-paste --no-newline`, restore via
`wl-copy`. X11 path byte-identical.

**`fluidvoice/config.py`:** add `insertion.wayland_tool = "auto"` — DEFAULTS
(:126-135), `_SAVE_WHITELIST` (:404), `ALLOWED_SETTINGS` (:575), choice map next
to `("insertion","mode")` (:525), template comments in `write_template`
([insertion] block ~:298-310).

**Tests** (in `tests/test_wayland_capabilities.py`, using the `runner` fake
pattern from tests/test_insertion.py — monkeypatch `insertion._run`,
`insertion.subprocess.Popen`, `insertion.time.sleep`, and pin
`XDG_SESSION_TYPE=wayland` per-test):
tool resolution matrix incl. GNOME exclusion + explicit override;
wtype typed argv; ydotool typed argv; leading-dash routes to paste;
paste sequence argv order (snapshot → wl-copy text → key cmd → sleep → wl-copy
restore) with sleeps recorded (no hold); terminal key never chosen (no
wm_class); `press_key` translation table (all 6 specs + unknown-spec error);
`copy_to_clipboard` wl-copy argv; rewrite `capture_selection` wayland sequence;
degradation ladder (tool missing → wl-copy-only notice; wl-clipboard missing →
InsertError); **X11 regression pin**: with `XDG_SESSION_TYPE=x11` and the same
fakes, `insert_text` still emits `xdotool type …` exactly as before.

**Gate:** suite green; `XDG_SESSION_TYPE=wayland` + fake `wtype`/`wl-copy`
shims on PATH in a scratch dir → daemon `insert-text` socket action runs the
constructed wtype command line (unit-asserted; live version in §9).

---

## 3. Phase 3 — hotkey: DE-shortcut assist + optional evdev push-to-talk

No global grabs exist on Wayland — this phase ships the assisted alternative.

**Settings — `fluidvoice/gtkui/settings_window.py`:** new `_build_wayland()`
page (registered in `__init__` next to `_build_dictation()`, name/icon
`"wayland"`), three groups:
1. **Session & capabilities**: rows from `session.probe()` +
   `session.capabilities()` (plain labels; updates on window open).
2. **Bind your hotkey**: per-`XDG_CURRENT_DESKTOP` instructions (shared helper
   `session.de_shortcut_instructions(desktop, script_path)` — same text doctor
   prints), a read-only row with the generated script path + "Copy" button, and
   an "Open DE shortcut settings" button (`gnome-control-center keyboard` on
   GNOME, `systemsettings kcm_keys` on KDE, else hidden). COSMIC/others: the
   copy-command + generic instructions.
3. **Insertion & push-to-talk**: `_combo("insertion", "wayland_tool", …)`
   (auto/wtype/ydotool), `_switch("hotkey", "wayland_evdev", …)`, device-name
   `_entry`, evdev key `_entry` (see below).
   *Deviation from the task text (documented in STATUS):* a literal KDE
   shortcut-file import was evaluated and rejected — Plasma 6 custom command
   shortcuts live in `kglobalshortcutsrc` managed by kglobalacceld with no
   supported import format; writing it blind is fragile. The bindable script +
   open-panel + per-DE instructions deliver the same outcome honestly.

**Optional evdev push-to-talk — new file `fluidvoice/evdev_ptt.py`:**
`EvdevPTT(device_substr: str, key_name: str, *, on_press, on_release, log)`
— lazily `import evdev` (new optional extra in pyproject:
`[project.optional-dependencies] wayland = ["evdev"]`); scans
`/dev/input/event*` by device name substring (open failure → WARN + disabled,
never fatal); hold-mode state machine from the raw event stream (press/release;
auto-repeat filtered like `hotkey.py` does); `start()/stop()` thread lifecycle
mirroring `HotkeyListener`. Reading `/dev/input` needs the `input` group —
privileged path, said plainly in README/doctor. Default off.

**`fluidvoice/config.py`:** `hotkey.wayland_evdev = false`,
`hotkey.wayland_evdev_device = ""` (name substring),
`hotkey.wayland_evdev_key = "KEY_RIGHTCTRL"` (mirrors the X11 default) — all
four registration points + template comments (as in Phase 2).

**`fluidvoice/daemon.py`:** in `run()` on wayland, if enabled:
construct + start `EvdevPTT` (callbacks → `self.toggle`/`self.cancel`),
HotkeyError-style degradation on any failure. `hotkey.*` config changes already
restart hotkey listeners (`daemon.py:~976`) — extend that hook to restart the
evdev listener too.

**`fluidvoice/doctor.py`:** evdev block — `import evdev` presence,
`/dev/input` readability (`os.access`), user-in-`input`-group check, matching
device names found (`glob /dev/input/event*` + evdev names when importable);
per-compositor shortcut instructions (shared helper).

**Tests:** `EvdevPTT` driven by a fake device object streaming queued events
(press/release/repeat/unplug) — lifecycle, callbacks, repeat-filtering,
device-not-found degradation (no real /dev/input touched);
`de_shortcut_instructions()` per-desktop content (gnome/kde/cosmic/unknown);
daemon wiring with a stub listener; config round-trip of the three keys;
settings-page helper functions without GTK where possible (GTK page test added
to `tests/test_gtkui.py` under its existing display guard — it already runs
under Wayland since it checks `DISPLAY or WAYLAND_DISPLAY`).

**Gate:** suite green; with `XDG_SESSION_TYPE=wayland` the settings window
constructs with the Wayland page and renders the bindable command (GTK-guarded
test); X11 machines see the new page too (it is informational) but nothing
changes functionally.

---

## 4. Phase 4 — overlay: notifications are the Wayland preview

No new rendering work — declaration and guarantees:

- `doctor.py` matrix row: `overlay: notifications (pill = layer-shell, future)`;
  same for `preview`.
- `README.md` + `docs/STATUS.md` state plainly: the X11 pill is not possible on
  GNOME-Wayland v1; wlroots layer-shell pill is future work (out of scope).
- The fallback test already exists: `TestFluidOverlayFallback`
  (tests/test_overlay.py:166-181) monkeypatches `Xlib.display.Display` to
  raise and asserts the notify degradation — confirm it, extend only if it
  lacks an explicit wayland-session case; no new test is expected here.
  (test_gtkui.py already runs under `DISPLAY or WAYLAND_DISPLAY`, so the
  new Wayland page test needs no new guard.)
- Log line on wayland daemon start already says `overlay=notifications`
  (Phase 1) — confirm the preview log reads `preview started (notify, …)` on a
  wayland session (it does: `actual = "notify"` when `using_overlay` is False,
  daemon.py:1318).

**Gate:** suite green; no behavioral code change beyond the explicit fallback
test.

---

## 5. Phase 5 — docs

- **README.md**: replace the Requirements "X11 session" paragraph with a
  **"Wayland support"** matrix section:

  | Capability | X11 | Wayland |
  |---|---|---|
  | Global hotkey | XGrabKey | DE shortcut → `sayit-ermano toggle` (Settings assists; optional evdev push-to-talk) |
  | Text insertion | `xdotool type` | `wtype` (wlroots/KDE) or `ydotool` (any; needs `ydotoold` + uinput) |
  | Paste mode | verified read-observation + restore | `wl-clipboard` + fixed delay + restore (verification impossible on Wayland) |
  | Live preview | X11 pill | notification bubble (layer-shell pill: future) |
  | Tray | SNI | SNI (same) |
  | App hints / terminal quirks | WM_CLASS | unavailable (AT-SPI: future) |

  plus tool install commands (`wtype`, `ydotool`, `wl-clipboard`) and the
  ydotool permission note.
- **docs/ROADMAP.md**: tick the three v0.3 items as phases land (with phase
  references).
- **docs/STATUS.md**: rewrite "Wayland parity (v0.3)" with what shipped, the
  two deliberate divergences (no paste verification; no clipboard hygiene
  markers) and the KDE-import deviation; add smoke-checklist results (§9).
- **docs/UPSTREAM-TRACKING.md**: "Smart typing" row → `✅ xdotool on X11,
  wtype/ydotool on Wayland`; "Global push hotkey" note updated.
- **docs/COMPARISON.md**: SayItErmano row `X11 now / Wayland roadmap` →
  `X11 + Wayland (v0.3)`; insertion cell mentions wtype/ydotool.

---

## 6. Files touched (summary)

| File | Phase | Change |
|---|---|---|
| `fluidvoice/session.py` | 1 | **new**: probe, capabilities, de_shortcut_instructions, ensure_toggle_script |
| `fluidvoice/paths.py` | 1 | `bin_dir()`, `toggle_script()` |
| `fluidvoice/daemon.py` | 1,3 | session log line, status fields, ensure_toggle_script call, evdev wiring |
| `fluidvoice/cli.py` | 1 | `_describe` session/capabilities line |
| `fluidvoice/doctor.py` | 1,3,4 | capability matrix, wayland tools, evdev checks, overlay row |
| `tests/conftest.py` | 1 | pin `XDG_SESSION_TYPE=x11` for the suite |
| `fluidvoice/insertion.py` | 2 | tool resolution, command builders, wayland typed/paste/clipboard paths |
| `fluidvoice/rewrite.py` | 2 | wayland `capture_selection` |
| `fluidvoice/config.py` | 2,3 | `insertion.wayland_tool`, `hotkey.wayland_evdev*` keys (4 registration points + template each) |
| `fluidvoice/gtkui/settings_window.py` | 3 | `_build_wayland()` page |
| `fluidvoice/evdev_ptt.py` | 3 | **new**: optional physical push-to-talk |
| `pyproject.toml` | 3 | `wayland` optional extra (`evdev`) |
| `tests/test_wayland_capabilities.py` | 1-3 | **new**: all unit tests below |
| `tests/test_overlay.py` | 4 | explicit notify-fallback test (if missing) |
| `tests/test_gtkui.py` | 3 | Wayland page under existing display guard |
| `README.md`, `docs/{ROADMAP,STATUS,UPSTREAM-TRACKING,COMPARISON}.md` | 5 | matrix + divergence records |

## 7. Test inventory (new, all in `tests/test_wayland_capabilities.py` unless noted)

Phase 1 (≈12): probe precedence ×5; capabilities ×5 (x11/wtype/ydotool-only/
none/gnome-excludes-wtype); ensure_toggle_script ×2; daemon status fields;
doctor matrix helper; CLI describe line.
Phase 2 (≈16): tool resolution ×3; typed argv ×2 (wtype, ydotool);
leading-dash→paste; paste sequence + sleeps ×2 (restore ok, restore fails→
notice); spec table ×7 (6 specs + unknown→InsertError); copy_to_clipboard;
rewrite capture; degradation ladder ×2; X11 regression pin ×1.
Phase 3 (≈10): EvdevPTT fake-stream ×5 (press/release/repeat-filter/unplug/
not-found); instructions ×4 (gnome/kde/cosmic/unknown); daemon wiring; config
round-trip. Plus `tests/test_gtkui.py` page test.
Phase 4 (1): explicit overlay fallback test.

## 8. Verification gates (every phase)

1. `.venv/bin/python -m pytest -q tests --ignore=tests/integration` — green.
2. X11 delta check: run the suite with the real environment (no env pinning
   beyond conftest) on an X11/unknown session — all pre-existing tests
   unmodified and passing.
3. Wayland simulation: `XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=… \
   PATH=<fakedir>:$PATH .venv/bin/python -m pytest -q tests/test_wayland_capabilities.py`
   where `<fakedir>` holds executable `wtype`/`ydotool`/`wl-copy`/`wl-paste`
   shims that log their argv (only needed for the few tests that don't fake
   `_run` — prefer the monkeypatch style throughout).
4. Daemon-level: start the daemon with `XDG_SESSION_TYPE=wayland`,
   `SAYITERMANO_SOCKET=<tmp>` and fake tools; `sayit-ermano status` prints the
   capability line; `insert-text` action produces the constructed tool argv
   (unit-level via the stub daemon; live in §9).

## 9. Live smoke checklist (manual, results recorded in docs/STATUS.md)

Run once per compositor available (priority: GNOME-Wayland, sway; KDE if
possible). GNOME exercises the ydotool path; sway exercises wtype.

1. Baseline confirmation of §0: daemon starts on wayland with **no** tools
   installed — WARN lines only, tray/socket/status alive, dictation transcribes
   and lands in history + "paste manually" notification (wl-clipboard absent →
   the InsertError notification). Run once in the foreground AND once under the
   installed systemd user unit — the unit bakes `Environment=DISPLAY`, so this
   is where a mis-propagated session env would first show (see §0 gotcha).
2. `wtype` installed (sway): typed insertion into a terminal and an editor;
   spoken-send Enter; paste-last; paste mode via `wl-clipboard` — verify the
   pre-paste clipboard content is restored after both paths.
3. `ydotool` (GNOME, `ydotoold` running, uinput perms): same set; note whether
   key-duration tuning was needed → fix the central builders only.
4. Overlay: recording shows the notification preview; confirm no X11 pill
   attempt noise in the log.
5. Doctor on the live session: matrix prints, per-tool found/missing correct,
   exit code 0 when insertion resolves / non-zero when not.
6. Settings → Wayland page: renders, copy-button yields a working script; bind
   it in the DE; toggle dictation via the shortcut.
7. evdev push-to-talk (if `input`-group access): hold-to-talk works; note the
   device-name match.
8. **X11 regression, same build**: full manual pass on an X11 session (hotkey
   grab, pill preview, verified paste, spoken-send, rewrite) — zero deltas.

## 10. Out of scope (recorded as future work)

- Layer-shell pill UI (wlroots compositors) — the notification preview is the
  declared Wayland preview for v1.
- Portal-based global shortcuts (RemoteDesktop/GlobalShortcuts portal).
- Screencast/OCR anything; compositor-specific patches; Wayland CI runners.
- AT-SPI app-hint / smart-typing context (tracked in ROADMAP "Later").
- Any change to X11 code paths beyond the session-type gate (invariant 1).
