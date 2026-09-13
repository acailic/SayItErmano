# Plan: Insertion hardening — paste verification, clipboard hygiene, terminal paste keys

Session `e5243b82` · repo `/home/nistrator/Documents/github/FluidVoiceLinux` · planned against HEAD `c42879b` (worktree clean; `insertion.py` stable, `config.py` includes the chat-formatting keys — `general.terminal_apps` already exists from spec `3c170bcc`, and we REUSE it: **one key, not two**).

Brief: `requests/insertion-hardening.md`. Every phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green.

---

## 0. Planning-time verification (all live on THIS desktop, 2026-09-04)

The desktop: **GNOME Shell 46.0, X11 (`DISPLAY=:1`)**, `XDG_SESSION_TYPE=x11`.

### 0.1 The clipboard managers actually running

- **CopyQ 7.1.0 (Qt 5.15.12)** — the primary manager: `/usr/bin/copyq` + `copyq --clipboard-access monitorClipboard`. Its own GNOME DBus extension (`com.github.hluk.copyq.GnomeClipboard`) is **not** running (Ping → ServiceUnknown), so the monitor uses the Qt/X11 path.
- **clipboard-indicator@Tudmotu.github.com** — GNOME shell extension, enabled (gsettings `enabled-extensions`). Its source has **no** password-hint support; it reads via the mutter/gnome-shell selection proxy (window `0xa01b5x` in the probes: requests `UTF8_STRING` eagerly at ownership-change, +0.00–0.01 s).

### 0.2 Privacy leak reproduced (scope 2's premise)

`echo "FVTEST-FLASH-7f3a" | xclip -selection clipboard; sleep 0.6` → `copyq read 0` = `FVTEST-FLASH-7f3a` (history item #0; `copyq count` stayed 200 = cap, oldest evicted). **Today's `insert_paste` flash lands verbatim in CopyQ history.**

### 0.3 Hygiene technique — probe-verified against the live CopyQ

Owned CLIPBOARD from python-xlib (already a hard dependency: `python-xlib>=0.33` in pyproject), serving `UTF8_STRING`/`text/plain;charset=utf-8`/`text/plain`/`STRING`/`TIMESTAMP` plus marker targets, then watched every `SelectionRequest`:

1. **`x-kde-passwordManagerHint` = `secret` (mixed-case, in TARGETS, value served): NOT honored by this CopyQ build.** The monitor (window `0x1c00005`) requested only TARGETS + `UTF8_STRING` + `text/plain;charset=utf-8`, never the hint atom, and stored the text. Root cause (traced through CopyQ 7.1.0 + Qt 5.15 sources, clone at `/tmp/copyq-src`): this build's `isHidden()` path never queried the atom (Ubuntu deb appears to run the `DummyClipboard`/`WaylandClipboard` shim — built without KGuiAddons — where the secret detection differs from `X11PlatformClipboard::updateClipboardData`). Qt itself passes atom names through verbatim (`QXcbMime::mimeAtomToString` → `fromLatin1(atomName)`), so this is a CopyQ-build quirk, not a Qt one.
2. **`application/x-copyq-hidden` = `1` (and `application/x-copyq-secret` = `1`) advertised in TARGETS: HONORED — CopyQ fetched `application/x-copyq-hidden` immediately after the text and did NOT store the item** (`copyq read 0` unchanged, marker absent from top-5). Mechanism (source-verified): `ClipboardMonitor` appends `mimeHidden`/`mimeSecret` to the format list it clones from the selection (`src/app/clipboardmonitor.cpp:62`), `isClipboardDataHidden/Secret` (`:38-46`) reroute to `hiddenClipboardChanged`/`secretClipboardChanged`, and the default handler discards (`docs/security.rst`: "silently ignores the clipboard change").
3. CopyQ's monitor is a **poller with a doubling ladder**: first read at ownership-change (+0.00–0.01 s, event-driven via XFixes), re-checks at +0.03, +0.09, +0.25, +0.60 s (interval ×2 + 50 ms — `checkAgainLater`, `minCheckAgainIntervalMs=50`). **No hold duration can outrun the first read** — minimal-flash alone does NOT protect against CopyQ.
4. Residual (documented, not fixable from X11): the **mutter/gnome-shell proxy and clipboard-indicator read the text eagerly at ownership change regardless of markers** — a GNOME-shell-extension history capture remains possible. Recorded in STATUS.md.

**Chosen technique**: advertise all three marker targets alongside the text (`x-kde-passwordManagerHint=secret` for Klipper/GPaste/KeePassXC conventions and other CopyQ builds, plus both CopyQ markers). Verified absent from this machine's CopyQ history; non-CopyQ consumers ignore unknown TARGETS entries (plain pastes still work — terminals pasted fine in §0.4 while markers were advertised).

### 0.4 Terminal paste keys — live matrix (scope 3's "verify, don't guess")

Clipboard set via xclip, keys sent via `xdotool key --clearmodifiers` to an activated window running `cat > file`; `^M` in the capture = the key passed through to the shell's line discipline (lnext `^V` quoted the probe's Return):

| target | `ctrl+v` | `ctrl+shift+v` |
|---|---|---|
| gnome-terminal (bare) | NO-PASTE (`^M`) | **PASTED** |
| kitty (bare) | NO-PASTE (`^M`) | **PASTED** |
| alacritty (bare) | NO-PASTE (`^M`) | **PASTED** |
| tmux 3.4 inside gnome-terminal | NO-PASTE (`^M`) | **PASTED** |
| ghostty | not installed on this machine — untested locally; `general.terminal_apps` already lists it, config covers it; recorded in STATUS.md |

Conclusion: in X11 terminals `ctrl+v` is passed to the app (failure mode (d) is real today), `ctrl+shift+v` pastes CLIPBOARD everywhere tested, including under tmux (the emulator handles the chord before tmux sees it).

### 0.5 Paste-verification signal — the ICCCM mechanism

While we own CLIPBOARD, **every read is a `SelectionRequest` event delivered to us with the requestor's window id** (probes logged them all live: manager probes, mutter proxy, poller). So:

- **quiesce window** after taking ownership (~0.25 s): the eager readers (mutter proxy, CopyQ first poll) reveal themselves — record their window ids;
- **after the paste keystroke**: any `SelectionRequest` from a *window not seen during quiesce* = the target app read the clipboard = the paste landed. Poll with a 25 ms event loop, cap 0.60 s (the "0.1→0.6 s backoff ladder" only as the legacy no-ownership fallback).
- **never-reads app** (keystroke lost / focus gone): timeout → restore → `InsertError` → `insert_text` auto-mode falls back to typed; paste-mode surfaces via the daemon's existing `Could not type text` notification.

Constraints discovered by the probes (folded into the design):

- **Max request size 16 777 212 bytes** (`xdpyinfo`) — single-shot property transfers suffice for dictations; no INCR needed (refuse holds > ~16 MB and fall back to the xclip path).
- **The event loop must never block** (probe v1 deadlocked answering its own `xclip -o`): all subprocess calls happen outside the hold, or via non-blocking checks between event drains.
- `SelectionClear` (another client takes ownership mid-hold) is detectable → skip the restore (don't clobber the user's fresh copy) and treat as unverified.
- Atoms are plain ints in python-xlib; `SelectionNotify` construction works with keyword fields (`time/owner/requestor/selection/target/property`) — probe-verified.

---

## 1. Design summary

| Piece | Where | Shape |
|---|---|---|
| Selection holder | `fluidvoice/selection.py` (new) | `SelectionHold(data: bytes, hygiene: Sequence[tuple[str, bytes]] = ())` — owns CLIPBOARD, serves text + marker targets, records `(t, requestor, target)` events; methods `quiesce(s)`, `wait_read(timeout, exclude_windows)`, `lost_ownership`, `release()` |
| Verify-then-restore | `fluidvoice/insertion.py` | `insert_paste(text, *, key, verify, on_notice)` state machine replacing the fixed `sleep(0.25)` |
| Terminal paste key | `fluidvoice/insertion.py` | `insert_text` resolves `wm_class` once; `is_terminal_app(wm, cfg)` (existing, shared key) → `ctrl+shift+v` |
| Fallback surfacing | `fluidvoice/daemon.py` | `DictationPipeline` wires `on_notice` → existing `self.notify` path; typed-fallback notice |
| Config | `fluidvoice/config.py` | `insertion.verify_paste = true`, `insertion.terminal_paste_key = "ctrl+shift+v"` (DEFAULTS/TEMPLATE/whitelist/validation) |
| Doctor | `fluidvoice/doctor.py` | `_insertion_lines(cfg)` — one resolution line per key |
| Tests | `tests/test_insertion.py`, `tests/test_selection.py` (new), `tests/test_config_settings.py`, `tests/test_infra.py`, `tests/integration/test_live_x11.py` | faked clipboard/xdotool processes; state-machine scenarios; optional live check |
| Docs | `docs/STATUS.md`, `docs/ROADMAP.md`, `docs/UPSTREAM-TRACKING.md`, `README.md` | residuals recorded honestly (§0.3/§0.4 evidence) |

Timing constants (module-level, read at call time so tests can monkeypatch):

```python
PASTE_QUIESCE_S = 0.25          # eager readers land here (observed +0.00..0.01)
PASTE_VERIFY_TIMEOUT_S = 0.60   # post-keystroke read cap
PASTE_POLL_INTERVAL_S = 0.025   # event-loop granularity during waits
RESTORE_SETTLE_S = 0.12         # xclip fork serve latency after restore write
LEGACY_SETTLE_S = 0.25          # today's fixed sleep (verify_paste = false)
VERIFY_LADDER_S = (0.10, 0.20, 0.30)  # legacy fallback when ownership unavailable
RESTORE_VERIFY_RETRIES = 1
HYGIENE_TARGETS = (
    ("x-kde-passwordManagerHint", b"secret"),  # Klipper/GPaste/KeePassXC + CopyQ docs
    ("application/x-copyq-secret", b"1"),      # CopyQ 7.1.0 — live-verified honored
    ("application/x-copyq-hidden", b"1"),      # CopyQ 7.1.0 — live-verified honored
)
```

`insert_paste` flow (verify=True):

1. `_clipboard_snapshot()` — previous bytes (`xclip -o`) + one `xclip -o -t TARGETS` probe → `prev_is_text` (TARGETS contains `UTF8_STRING`/`text/plain`/`STRING`). Non-text previous (image etc.): restore stays today's blind byte round-trip and read-back verify is skipped (never fail an insert for a non-text clipboard).
2. `hold = _make_hold(text.encode(), HYGIENE_TARGETS)` → `SelectionHold` or `None` (no DISPLAY / Xlib error / data > 16 MB → legacy path).
3. `hold.quiesce(PASTE_QUIESCE_S)` — drain + answer events, collect known requestor windows.
4. `xdotool key --clearmodifiers <key>` (unchanged failure → `InsertError`).
5. `verified = hold.wait_read(PASTE_VERIFY_TIMEOUT_S, exclude=known)` — any request from a new window. If `hold.lost_ownership` before verify: unverified.
6. `hold.release()` (disown). If ownership was lost to someone else mid-hold, skip the restore (their content wins) and log.
7. Restore: previous `None` → nothing (clipboard was empty); else `_clipboard_write(previous)` + settle, and when `prev_is_text`: read back; mismatch → one retry; still mismatch → `on_notice("Clipboard restore could not be verified")` — **not** an `InsertError` (the paste already landed; raising would re-type and double-insert).
8. If `not verified`: `raise InsertError("paste not verified: target did not read the clipboard")` — after the restore, so the clipboard is clean. In `insert_text` auto mode this is caught → `on_notice("Paste did not land — typing instead")` → typed insertion; in explicit paste mode it propagates to the daemon's existing `_insert` handler (notification + `clipboard_fallback`).

Legacy path (verify=False, or hold unavailable): today's `xclip` write + keystroke + sleep (`LEGACY_SETTLE_S`; ladder when verify=True but ownership failed) + blind restore. Hygiene markers are not possible there — documented.

`insert_text(text, cfg, wm_class=None, on_notice=None)` changes: resolve `wm = active_window_class() if wm_class is None else wm_class` **once, before strategy choice** (needed for both the terminal paste key and the existing trailing-space rule); choose `key = cfg["insertion"].get("terminal_paste_key", "ctrl+shift+v") if wm and is_terminal_app(wm, cfg) else "ctrl+v"`; pass `verify=cfg["insertion"].get("verify_paste", True)` and `on_notice` into `insert_paste`. Signature stays 2-arg compatible (`Inserter` type unchanged; new params keyword-only with defaults).

---

## 2. Phases

### Phase 1 — config surface

**`fluidvoice/config.py`**
1. `DEFAULTS["insertion"]` += `"verify_paste": True`, `"terminal_paste_key": "ctrl+shift+v"`.
2. `TEMPLATE` `[insertion]` block: `# Verify the paste landed (selection read) before restoring the\n# clipboard; false = legacy fixed-delay restore` + `verify_paste = true`, and `# Keystroke used to paste in terminal apps (general.terminal_apps);\n# X11 terminals need ctrl+shift+v` + `terminal_paste_key = "ctrl+shift+v"`.
3. `_SAVE_WHITELIST["insertion"]` += both keys; `SETTING_BOOLS` += `("insertion", "verify_paste")`; `SETTING_RANGES[("insertion", "terminal_paste_key")] = ("str", 32)`; `ALLOWED_SETTINGS["insertion"]` += both (socket-settable; no GTK UI rows — "no UI change", same decision as spec `3c170bcc`).

**`tests/test_config_settings.py`** — new class `TestInsertionHardeningKeys`: defaults present/correct; `apply_settings` accepts `verify_paste=False` and `terminal_paste_key="ctrl+shift+v"`/`"ctrl+v"`, rejects non-bool / non-str / >32-char / leading-dash values; `save_config` round-trips both.

Gate: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`.

### Phase 2 — `fluidvoice/selection.py` (holder, not yet wired)

`SelectionHold` per §1. Implementation notes (probe-validated patterns):
- `display.Display()` + `root.create_window(-1, -1, 1, 1, 0, X.CopyFromParent)`; `win.set_selection_owner(CLIPBOARD, X.CurrentTime)`; assert `get_selection_owner` == our window else raise `SelectionUnavailable`.
- Serve `TARGETS` → atom list `[UTF8_STRING, text/plain;charset=utf-8, text/plain, STRING, TIMESTAMP] + hygiene atoms`; `TIMESTAMP` → `INTEGER/32/[now]`; text targets → data (8-bit); hygiene targets → their marker bytes; unknown target → refuse (`property=0` in the `SelectionNotify`). `MULTIPLE` → refuse politely. Reply with `Xlib.protocol.event.SelectionNotify(time=…, owner=win.id, requestor=…, selection=…, target=…, property=…)` + `disp.send_event(requestor, n, event_mask=X.NoEventMask)`, then `disp.flush()`.
- Event drains in `quiesce`/`wait_read`: `while disp.pending_events(): …` + `time.sleep(PASTE_POLL_INTERVAL_S)` between polls; record `(monotonic, requestor_id, target)`; track `SelectionClear`.
- `release()`: `set_selection_owner(X.NONE)`, `flush()`, `close()`.
- Guard: `len(data) > 16_000_000` → `SelectionUnavailable` (caller falls back); never spawn subprocesses while holding.
- Pure helpers kept separate for hermetic tests: `_new_reader(events, since, exclude) -> int | None` (first requestor not in `exclude` after `since`), `_is_text_target(target) -> bool`.

**`tests/test_selection.py`** (new, hermetic — no X connection): drive the pure helpers with synthetic event tuples (new-window detection, exclusion set, ordering); `SelectionHold` object-level tests construct it with a monkeypatched `display.Display` returning a fake with scripted `pending_events/next_event` — assert the SelectionRequest answering logic (targets list contains hygiene atoms, refuse-unknown) and `lost_ownership` on `SelectionClear`. Import guard: if `Xlib` import fails the module degrades to `SelectionUnavailable` (test via `sys.modules` stub).

Gate: pytest green.

### Phase 3 — verify-then-restore + terminal key in `insertion.py`

1. Add constants (§1) at module level.
2. `_clipboard_snapshot()` (bytes | None, prev_is_text) — extends `_clipboard_read` with the TARGETS probe.
3. Rewrite `insert_paste(text, *, key="ctrl+v", verify=True, on_notice=None)` per the §1 flow; keep the `shutil.which("xclip")` guard.
4. `insert_text`: resolve `wm` once up front; terminal key selection; pass through `verify`/`on_notice`; keep the leading-dash → paste and threshold logic and the existing typed-path trailing-space rule unchanged.

**`tests/test_insertion.py`** — update the `runner` fixture: fake `_run` answers `xclip -o -t TARGETS` with `b"UTF8_STRING\ntext/plain"` (text clipboard) and plain `-o` with `b"previous clipboard"`; fake `Popen` as today. New class `TestPasteVerification` (monkeypatch `insertion._make_hold` → `FakeHold` with scriptable request times; monkeypatch `insertion.time.sleep`):
- fast app: read at 0.02 s → `"paste"`, restore written once, read-back matches → no retry.
- slow app: read at 0.5 s (< 0.60 cap) → `"paste"`.
- never-reads: timeout → auto mode returns `"typed"` (InsertError caught) and `on_notice` got the fallback message; paste mode raises `InsertError`.
- restore mismatch: read-back wrong once, right after retry → success, single retry.
- restore still wrong → success paste + `on_notice` clipboard warning, no raise.
- non-text previous (TARGETS without text atoms) → no read-back verify, restore still attempted (today's behavior).
- `hold.lost_ownership` before the keystroke → InsertError/typed fallback; lost after verify → restore skipped, paste still `"paste"`.
- `verify_paste=False` → exactly two xclip writes (text + restore), `LEGACY_SETTLE_S` sleep, no hold created (today's behavior, key still honored).
- `_make_hold` returns None (no X) + verify=True → ladder sleeps, blind restore, `"paste"` result.

New class `TestTerminalPasteKey`: `insert_text("x"*2000, cfg, wm_class="kitty")` sends `ctrl+shift+v`; `wm_class="firefox"` sends `ctrl+v`; custom `terminal_paste_key` honored; `terminal_apps=[]` → `ctrl+v`; explicit paste mode in terminal also uses the terminal key; typed path unaffected.

Adjust existing assertions that count writes/keys (`test_long_text_uses_paste`, `test_paste_mode_restores_clipboard`) for the read-back verify (text previous → 2 writes + reads) — keep them meaningful, not just bumped counts.

Gate: pytest green.

### Phase 4 — daemon wiring + doctor

**`fluidvoice/daemon.py`** — in `DictationPipeline.__init__`, after `self.notify` is assigned: if `inserter is insertion.insert_text`, wrap as
`self.inserter = lambda t, c: insertion.insert_text(t, c, on_notice=lambda m: self.notify("SayItErmano", m))`
(stub inserters in tests are untouched; default path surfaces paste-fallback and restore warnings through the existing notification path). No pill/UI change.

**`fluidvoice/doctor.py`** — `_insertion_lines(cfg)` after `_formatting_lines`, section in `run()`:

```
insertion hardening:
  paste verification: on                (insertion.verify_paste)
  terminal paste key: ctrl+shift+v      (insertion.terminal_paste_key)
```

**`tests/test_infra.py`** — `TestDoctorInsertionLines` mirroring `TestDoctorFormattingLines`: defaults → 2 lines with `on`/key + config-key names; disabled → `off`; custom key string shown.

**`tests/test_daemon.py`** — one pipeline test: stub inserter raising `InsertError` in paste mode → existing notification path used (already covered; add the `on_notice` wiring test: default inserter path with `insert_paste` monkeypatched to raise-then-fallback is hard hermetically — instead assert the wrapper: `DictationPipeline` with default inserter + monkeypatched `insertion.insert_paste` that calls `on_notice` then raises `InsertError` in paste mode → notify captured).

Gate: pytest green.

### Phase 5 — docs (residuals recorded with evidence)

1. **`docs/STATUS.md`**: tick the "Insertion hardening" checkbox in Later; add a "Insertion hardening residuals" bullet block: (a) CopyQ 7.1.0 suppression live-verified via `application/x-copyq-secret`/`-hidden` markers + `x-kde-passwordManagerHint` also served (Klipper/GPaste semantics, not honored by this CopyQ build — probe log); (b) **clipboard-indicator/mutter proxy still snapshots flashed text** (GNOME shell extension, no marker semantics) — residual leak; (c) GPaste/Klipper untested (not running here); (d) terminal matrix from §0.4 incl. "ghostty not installed locally — untested, config covers it"; (e) verify-timeout false-negative edge: an app whose own window read during quiesce pasting without a new window id would time out and re-type (documented trade-off). Update the header test count.
2. **`docs/ROADMAP.md`** Later item → `[x]` with a one-line DONE note (markers + verify-then-restore + terminal keys; AT-SPI fallback still later).
3. **`docs/UPSTREAM-TRACKING.md`**: v1.6.7 row "Temporary pasteboard writes hidden from clipboard managers" ⏳ → ✅ (X11 hygiene markers; CopyQ live-verified; GNOME-shell-extension residual in STATUS); v1.6.5 row "Reliable pasting in Ghostty/tmux/terminals" ⏳ → ✅ (verify-then-restore + `ctrl+shift+v` in `general.terminal_apps`; ghostty untested locally).
4. **`README.md`**: `[insertion]` config block gains the two keys with the template comments; one sentence under "Text insertion" (paste is verified via selection reads before the clipboard is restored; terminal apps get `ctrl+shift+v`).

Gate: pytest green + `grep -rn "insertion hardening" docs/` shows no stale ⏳ claims.

### Phase 6 (optional, same gate) — live integration check

**`tests/integration/test_live_x11.py`** — `@requires_x11` `TestSelectionHoldLive`: take a `SelectionHold` with `HYGIENE_TARGETS`, quiesce, fire a background `Popen(["xclip", "-o", "-selection", "clipboard"])` (non-blocking — the deadlock lesson), assert `wait_read` observes it from a new window; then, if `copyq` is on PATH (skip otherwise), assert a marker-tagged hold does not change `copyq read 0`. `pytestmark` already excludes it from default runs (`integration`+`desktop` markers).

---

## 3. Coordination notes

- `general.terminal_apps` is **shared** with spec `3c170bcc` (already shipped at HEAD): the terminal paste key reuses `is_terminal_app` — no second list is introduced.
- All changes in `insertion.py` are additive/param-widening; the `Inserter = Callable[[str, dict], str]` contract is preserved.
- `copy_to_clipboard` (deliberate copies) and `clipboard_fallback` intentionally keep plain writes — hygiene markers apply only to the transient paste.
- Out of scope (unchanged): Wayland insertion, AT-SPI fallback, typed-mode behavior, configuring the user's clipboard managers (CopyQ/clipboard-indicator stay as installed).

## 4. Definition of done

- All phases green at each commit (`.venv/bin/python -m pytest -q tests --ignore=tests/integration`).
- Unit coverage: quiesce/verify/restore state machine (fast, slow, never-reads, restore-retry, still-mismatch notice, non-text previous, lost-ownership), terminal key selection, InsertError fallback path + notification wiring, both config keys, doctor lines, selection pure-logic.
- Hygiene demonstrated against the live CopyQ (absent from `copyq read 0` after a marker-tagged hold) — evidence in §0.3 and STATUS.md; the clipboard-indicator residual documented honestly.
- Timing constants named at module level and read at runtime (tests monkeypatch them); terminal behavior recorded from the live matrix, ghostty flagged untested.
