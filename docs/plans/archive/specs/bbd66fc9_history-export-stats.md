# Plan: History ZIP export + Today-usage stats

Implements the two deferred items from the native-app spec's out-of-scope list
(`docs/superpowers/specs/2026-09-02-native-settings-app-design.md`, line 200:
"ZIP export, stats page"). Command mode has landed (HEAD 49ef209); these are next.

Suite gate for every phase: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
(382 green at HEAD; GTK tests self-skip without a display).

Out of scope (unchanged from the spec): speaker labeling/diarization, chunked
uploads, any HTTP API, zipping anything beyond history+audio, remote/cloud storage.

## Decisions (locked, don't re-litigate)

- **Both features are local-file only.** No new socket actions beyond one extra
  field in the existing `status` response; no HTTP anywhere.
- **`today_stats` counts every history entry** recorded since local midnight —
  including command/rewrite-mode entries (they are real dictations; minutes come
  from their `duration_s`, words from their text).
- **Local midnight** = `time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))`
  (DST-safe via `mktime`). The pure function takes `now` explicitly so tests are
  deterministic.
- **Audio safety rule for export:** an entry's audio file is included only if
  `Path(entry["audio"]).resolve()` is relative to `paths.audio_dir().resolve()`
  (Python ≥3.11 already required). Anything outside is refused; anything missing
  is skipped. Both emit a note; neither raises.
- **Export runs synchronously on the GLib main thread** (per request), with a
  visible busy state: the handler sets busy state and returns, and the actual
  write happens in a `GLib.idle_add` callback so one frame paints before the UI
  blocks on local disk I/O.
- **One shared formatter** (`history.format_today`) produces
  `"N dictations, M:SS minutes, K words"`; CLI prefixes `today: `, GTK uses the
  same string verbatim.
- **Zip layout:** `history.jsonl` (one JSON line per entry, re-serialized —
  corrupt lines in the live file get dropped, which is fine) + `audio/<basename>`
  for each retained file. ZIP_DEFLATED.

---

## Phase 1 — pure functions in `fluidvoice/history.py` (+ unit tests)

Files: `fluidvoice/history.py`, new `tests/test_history_export_stats.py`.

### 1a. Make the full-file reader public

Rename `_read_all()` → `read_all()` (same body) and update its internal callers
(`search`, `audio_path_for`, `_rewrite`). `tail()` only reads the last 128 KB,
so it can silently truncate; today-stats and export must use `read_all()`.
(The file is capped at `MAX_ENTRIES` = 5000 lines, so a full read stays cheap.)

### 1b. `today_stats(entries, now=None) -> dict` (pure)

```python
def today_stats(entries: list[dict], now: float | None = None) -> dict:
    """Dictations/seconds/words since local midnight over `entries`."""
```

- `now = time.time() if now is None else now`; midnight as per the formula above.
- `dictations` = count of entries with `ts >= midnight` (missing `ts` → not today).
- `seconds` = `float` sum of `duration_s` over those entries (missing → 0).
- `words` = sum of `len(str(text or raw or "").split())` over those entries
  (final `text` wins; fall back to `raw`).
- Returns `{"dictations": int, "seconds": float, "words": int}`.

### 1c. `format_today(stats: dict) -> str`

`f"{n} dictations, {m}:{ss:02d} minutes, {k} words"` where `m/ss` come from
`int(total_seconds)` (`75.4s` → `1:15`; no rounding of seconds up).

### 1d. `export_zip(path, on_note=None) -> int`

```python
def export_zip(path: Path, on_note: Callable[[str], None] | None = None) -> int:
    """Zip history + retained audio. Returns the number of entries exported."""
```

- `entries = read_all()`; write every entry as `json.dumps(entry, ensure_ascii=False)`
  + `\n` into the `history.jsonl` member — stream via
  `zf.open("history.jsonl", "w")` + `io.TextIOWrapper` (never buffers all lines).
- For each entry with a truthy `"audio"`:
  - `p = Path(entry["audio"])`; if `p.resolve()` is NOT
    `.is_relative_to(paths.audio_dir().resolve())` →
    `on_note(f"refused audio outside audio dir: {entry['audio']}")`, skip.
  - elif not a file → `on_note(f"skipped missing audio: {entry['audio']}")`, skip.
  - else `zf.write(p, arcname=f"audio/{p.name}")`, deduped with a seen-set of
    arcnames (timestamped names are unique, but never rely on it).
- `OSError` while writing the zip itself propagates to the caller (CLI/GTK
  handle it); missing/odd audio never raises.
- Returns `len(entries)` (all entries land in `history.jsonl`; the note channel
  tells the caller which audio didn't).

### 1e. Tests — new `tests/test_history_export_stats.py`

Follow the existing monkeypatch pattern from `tests/test_history_audio.py`:
`monkeypatch.setattr(history.paths, "history_file", lambda: tmp_path / "h.jsonl")`
and same for `audio_dir`. Build real WAV bytes with the local `write_wav` helper
(copy the tiny one from `test_history_audio.py` or import-style duplicate).

`TestExportZip`:
1. **Roundtrip**: 3 entries, 2 with audio files that exist inside the patched
   audio dir → returns 3; `zipfile.ZipFile` namelist has `history.jsonl` +
   both `audio/*.wav`; the JSONL lines decode back to the entries; audio member
   bytes equal the source files.
2. **Missing audio skipped**: entry referencing a non-existent file inside the
   audio dir → returns entry count including it, entry present in `history.jsonl`,
   no audio member, `on_note` received a message containing "missing". No raise.
3. **Path-traversal refusal**: (a) entry whose audio is an existing file at
   `tmp_path/"outside.wav"` (absolute, outside audio dir); (b) entry with
   `audio: "../../etc/evil.wav"` style relative escape. Neither appears in the
   zip; notes mention the refusal; entry still exported to `history.jsonl`.
4. **Empty history**: returns 0; zip contains an empty `history.jsonl`.
5. **No audio at all**: entries without `audio` key export cleanly (covered by
   roundtrip mix; assert at least one entry without audio in test 1).

`TestTodayStats` (all pass an explicit `now`; compute midnight in the test with
the same `mktime` formula):
1. **Midnight boundary**: entries at `midnight - 1` and `midnight` → only the
   second counts (`ts >= midnight`, inclusive).
2. **Empty**: `today_stats([], now=NOW) == {"dictations": 0, "seconds": 0.0, "words": 0}`.
3. **Word/duration counting**: two today-entries `"hello world"` (5.5s) and
   `"one two three four"` (1.0s), one with `text` and one with only `raw` →
   dictations 2, seconds ≈ 6.5, words 6.
4. **format_today**: `{"dictations": 2, "seconds": 75.4, "words": 6}` →
   `"2 dictations, 1:15 minutes, 6 words"`; sub-minute case → `0:45`.

**Gate:** suite green.

---

## Phase 2 — daemon `status.today` + CLI wiring

Files: `fluidvoice/daemon.py`, `fluidvoice/cli.py`, `tests/test_daemon.py`,
`tests/test_cli_ui_hotkey.py`.

### 2a. Daemon: `fluidvoice/daemon.py` `handle_request` `"status"` branch

Add one field to the existing response:

```python
"today": history_mod.today_stats(history_mod.read_all()),
```

(`history as history_mod` is already imported at module top.) Nothing else —
no lock, no caching; the read is small and the socket handler already runs off
the audio path.

### 2b. CLI: `fluidvoice/cli.py`

- History parser (currently only `-n`): add
  `p.add_argument("--export", type=Path, metavar="PATH.zip", help="write history + retained audio to a zip")`.
- In the `history` handler, before the tail/print loop:
  ```python
  if args.export:
      def _note(m): print(m, file=sys.stderr)
      try:
          n = history.export_zip(args.export, on_note=_note)
      except OSError as e:
          print(f"error: {e}", file=sys.stderr); return 1
      print(f"exported {n} entries to {args.export}")
      return 0
  ```
- `_describe(resp)`: after the recording-state line, append the today section
  when the daemon provided it:
  ```python
  if "today" in resp:
      from . import history
      text += "\ntoday: " + history.format_today(resp["today"])
  ```
  (`toggle`/`cancel` responses have no `today` key, so they are unaffected;
  `--json` output just gains the dict, no `_describe` involvement.)

### 2c. Tests

`tests/test_daemon.py` (mirror `test_status_includes_warmup`'s construction):
- **test_status_includes_today**: build the daemon with `StubRecorder` +
  `backend_factory=lambda c: None`, `d.backend = StubBackend("x")`;
  `monkeypatch` `paths.history_file` (patch the shared `fluidvoice.paths`
  module functions — daemon's `history_mod.read_all()` reads through them) to a
  fixture with one entry `{"ts": time.time(), "text": "now", "duration_s": 2.0}`
  and one at `time.time() - 86400`. Assert `resp["today"]["dictations"] == 1`,
  `["seconds"] == 2.0`, `["words"] == 1`.

`tests/test_cli_ui_hotkey.py` `TestCliHistory`:
- **test_history_export**: `monkeypatch.setattr(history, "export_zip", fake)`
  recording the path and returning 3 → `cli.main(["history", "--export", str(zip)])`
  returns 0, stdout has `exported 3 entries`. (Keeps the CLI test hermetic.)
- **test_status_prints_today**: `monkeypatch` `control.request` to return
  `{"ok": True, "recording": False, "today": {"dictations": 2, "seconds": 75.0, "words": 6}}`
  → `cli.main(["status"])` exits 0 and stdout contains
  `today: 2 dictations, 1:15 minutes, 6 words`.

**Gate:** suite green.

---

## Phase 3 — GTK: Export… action + stats line

Files: `fluidvoice/gtkui/client.py`, `fluidvoice/gtkui/main_window.py`,
`tests/test_gtkui.py`.

### 3a. Client (`fluidvoice/gtkui/client.py`) — two methods in the history block

```python
def today_stats(self) -> dict:
    return history_mod.today_stats(history_mod.read_all())

def export_zip(self, path) -> tuple[int, list[str]]:
    """(entries exported, notes about skipped/refused audio)."""
    notes: list[str] = []
    n = history_mod.export_zip(path, on_note=notes.append)
    return n, notes
```

(The window never touches `history_mod` directly — it already goes through the
client, which keeps smoke tests daemon-free.)

### 3b. History window (`fluidvoice/gtkui/main_window.py`)

**Stats line.** Append `self.today_lbl = Gtk.Label(css_classes=["dim-label"])`
to the `status` box (after `warmup_lbl`). Add `_update_today()`:

```python
def _update_today(self) -> None:
    try:
        st = self.c.today_stats()
    except Exception:
        return
    self.today_lbl.set_text("today: " + format_today(st))  # import format_today from ..history
```

Call it from `_render()` (runs on every `_load_history`: search, delete,
clear, initial) and from `refresh()` (so it moves after each dictation toggle).
`StubClient` supplies it in tests; `Client` reads the local file.

**Export action.** In the menu (currently only "Clear All…"), add above it:
`menu.append("Export…", "win.hist.export")`, and next to the existing
`install_action("hist.clear", …)`:

```python
self.install_action("hist.export", None, self._on_export)
self._exporting = False
```

- `_on_export`: `Gtk.FileChooserNative.new("Export history", self, Gtk.FileChooserAction.SAVE, "_Export", "_Cancel")`
  with `set_current_name(f"fluidvoice-history-{time.strftime('%Y%m%d-%H%M%S')}.zip")`;
  on `"response"` `== Gtk.ResponseType.ACCEPT` call
  `self._export_to(dlg.get_file().get_path())` (guard None path). `FileChooserNative`
  handles its own loop; keep a reference so the dialog isn't GC'd while open.
- `_export_to(path)`:
  1. busy state: `self._exporting = True`;
     `self.action_set_enabled("hist.export", False)`; `self._toast("Exporting…")`.
  2. Return `GLib.SOURCE_REMOVE`-style: schedule the work with
     `GLib.idle_add(self._export_now, path)` so the busy frame paints; do NOT
     write inside `_export_to` itself.
  3. `_export_now(path)`: try `n, notes = self.c.export_zip(path)` → toast
     `f"Exported {n} entries"` plus `f", {len(notes)} audio files skipped"` when
     notes is non-empty; except `Exception as e` → toast `f"export failed: {e}"`.
     Finally `self._exporting = False`;
     `self.action_set_enabled("hist.export", True)`; return `False` (stop idle).
- Everything stays on the GLib main thread (decision above) — no `threading`.

### 3c. Tests — `tests/test_gtkui.py`

Extend `StubClient` with the two methods so every existing window test keeps
working unchanged:

```python
def today_stats(self):
    return {"dictations": 2, "seconds": 6.5, "words": 9}
def export_zip(self, path):
    self.exported_to = path
    return len(self.entries), ["skipped missing audio: x.wav"]
```

New tests in `TestHistoryWindow` (offscreen, `pump(loop)` like the others):

1. **test_today_line_renders**: `HistoryWindow(client=StubClient(ENTRIES))`,
   present + pump → `w.today_lbl.get_text()` ==
   `"today: 2 dictations, 0:06 minutes, 9 words"` (from the stub). Also after
   `w._load_history()` it still shows (updates on refresh path).
2. **test_export_action_registered**: after present + pump,
   `w.lookup_action("hist.export") is not None` (GtkWidget implements
   `Gtk.ActionMap` in GTK4) and it is enabled.
3. **test_export_smoke**: `w._export_to(str(tmp_path / "h.zip"))`, then pump
   past the idle → `c.exported_to` is the given path; `w._exporting is False`;
   `hist.export` re-enabled (`w.action_set_enabled` round-trip — assert via
   `w.get_action_enabled("hist.export") is True`).
4. **Dialog itself is not driven** (native chooser can't be pumped reliably);
   the handler `_on_export` stays thin — chooser wiring is covered by the
   desktop run-through below, not the unit suite.

**Gate:** suite green (on a GTK box also `.venv/bin/python -m pytest -q tests/test_gtkui.py -rA`
to confirm no skips regressed; optional manual `fluidvoice app` click-through of
Export… — belongs to the builder's desktop verification, not CI).

---

## Phase 4 — docs/ledger (no code)

Files: `docs/UPSTREAM-TRACKING.md`, `docs/STATUS.md`.

- `docs/UPSTREAM-TRACKING.md:74` — "ZIP export ⏳" inside the audio-history row:
  replace with the shipped wording ("…inline replay, delete, clear; ZIP export
  ✅ (history + retained audio)").
- `docs/UPSTREAM-TRACKING.md:75` — "Today-usage stats | ⏳ | not started" →
  "✅ | History window header line, `fluidvoice status` `today:` line (local midnight)".
- `docs/STATUS.md:145` — strike "audio ZIP export; usage stats" from that
  unchecked backlog line, leaving "Dictionary auto-learning; …" items.
- Do **not** edit `docs/superpowers/specs/2026-09-02-native-settings-app-design.md`
  (dated record; its out-of-scope list is historical).

**Gate:** suite green; `grep -rn "ZIP export" docs/` shows no stale ⏳.

---

## Verification (end of each phase + final)

```bash
.venv/bin/python -m pytest -q tests --ignore=tests/integration   # must stay green
```

Final manual sanity (builder's desktop, not CI): `fluidvoice history --export /tmp/t.zip
&& unzip -l /tmp/t.zip`; `fluidvoice status | grep today`; open `fluidvoice app`,
confirm the header stats line and Export… → choose path → toast.

## Rollout order rationale

Phase 1 is pure and fully covered before any caller exists; Phase 2 wires the
same dict into two outputs (socket + CLI) behind one formatter; Phase 3 is the
only UI work and inherits tested plumbing via the client; Phase 4 is docs-only.
Nothing touches the recording pipeline, hotkey path, or config schema.
