# Plan: whisper.cpp GGUF auto-download + model manager

UPSTREAM-TRACKING / STATUS.md v0.4 row: *"whisper.cpp GGUF auto-download; model
manager"*. Today `model.whispercpp_model` must be a manual local path and the
GTK Models section only knows faster-whisper (FW) models. After this work,
choosing and using whisper.cpp is as easy as FW: pick a curated GGUF in
Settings → Models, it downloads with progress, and "Use" switches the backend.

Suite at HEAD `6981a2d`: **439 passed** with
`.venv/bin/python -m pytest -q tests --ignore=tests/integration`.
Every phase below leaves that command green (test count only grows).

## Design decisions (made here, builder does not re-litigate)

**D1 — The GTK app owns downloads (direct, app-side). No new daemon action.**
Justification:
- The app shares the daemon's filesystem and `paths.models_dir()` cache; a
  GGUF is just a file. No daemon state or privilege is needed.
- The existing degradation philosophy: settings work file-only when the daemon
  is down — app-side download keeps that true (download works offline-daemon).
- The daemon already has exactly the right surface for the *other* half:
  `set-config` validates `model.whispercpp_model`/`model.backend` (both in
  `ENGINE_KEYS`, `fluidvoice/config.py`) and daemon `_set_config` reacts to
  engine-key changes by reloading the backend in a warmup thread
  (`fluidvoice/daemon.py` ~line 726) — hot-swap parity with `select-model`
  for free.
- A `download-model` socket action would add a thread + progress plumbing to
  the daemon for zero capability gain.
So: `select-model` stays FW-only and untouched; the daemon changes **not at
all** in this plan (it validates config via the existing path).

**D2 — Catalog key = the plain file name** (`ggml-base.bin`, …). The config
value is then unambiguous: value containing `/` (or starting with `~`) = literal
path, passthrough; bare name in catalog = managed model living at
`models_dir()/whisper.cpp/<file>`; bare unknown name = clear error listing the
catalog. Deterministic, testable, backwards compatible with existing paths.

**D3 — Download mechanics**: stdlib `urllib.request`, streaming to
`<final>.part` in the same dir, `os.replace` on success, `.part` deleted on any
failure (no resume, no checksums — both out of scope). Progress callback
`(bytes_done, total_or_None)`; the UI stores it in a plain dict polled by a
`GLib.timeout_add` timer (never touch GTK from the worker thread).

**D4 — Cache layout**: `~/.cache/fluidvoice/models/whisper.cpp/<ggml-*.bin>`
(alongside `models/faster-whisper/`). Source: HuggingFace
`ggerganov/whisper.cpp` repo, `resolve/main/<file>` URLs (urllib follows the
CDN redirect).

## Curated catalog (exact content — copy verbatim)

| key (file name) | size label | langs | note | URL |
|---|---|---|---|---|
| `ggml-base.bin` | ~142 MB | 99 | fast; CPU-friendly | `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin` |
| `ggml-base.en.bin` | ~142 MB | en | fast; English only | `…/ggml-base.en.bin` |
| `ggml-small.bin` | ~466 MB | 99 | balanced | `…/ggml-small.bin` |
| `ggml-small.en.bin` | ~466 MB | en | balanced; English only | `…/ggml-small.en.bin` |
| `ggml-medium.bin` | ~1.5 GB | 99 | accurate, heavier | `…/ggml-medium.bin` |
| `ggml-medium.en.bin` | ~1.5 GB | en | accurate; English only | `…/ggml-medium.en.bin` |
| `ggml-large-v3.bin` | ~2.9 GB | 99 | best accuracy (no .en upstream) | `…/ggml-large-v3.bin` |

`langs` uses the same convention as `MODEL_CATALOG` ("99" multilingual, "en"
for the English-only variants). Same base URL for every row.

---

## Phase 1 — GGUF catalog + downloader (pure addition, nothing else uses it yet)

### `fluidvoice/model_catalog.py` (extend)

Keep `MODEL_CATALOG`/`model_downloaded` untouched. Add:

```python
# whisper.cpp ggml models (huggingface.co/ggerganov/whisper.cpp) —
# key = file name as stored under models_dir()/whisper.cpp/
GGUF_CATALOG: dict[str, dict[str, str]] = {
    "ggml-base.bin": {"size": "~142 MB", "langs": "99",
                      "note": "fast; CPU-friendly",
                      "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"},
    ...  # all 7 rows from the table above
}

GGUF_DIR_NAME = "whisper.cpp"

def gguf_dir() -> Path:
    return paths.models_dir() / GGUF_DIR_NAME

def gguf_path(name: str) -> Path:
    return gguf_dir() / name

def gguf_downloaded(name: str) -> bool:
    """True when the catalog model's file exists in the managed cache."""
    return name in GGUF_CATALOG and gguf_path(name).is_file()
```

No import changes needed (`paths` already imported). `gguf_downloaded`
mirrors the spirit of `model_downloaded` but is exact (single known filename).

### `fluidvoice/model_download.py` (new)

```python
"""Streaming GGUF downloads for the whisper.cpp backend (stdlib only)."""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__, model_catalog, paths

Progress = Callable[[int, "int | None"], None]
CHUNK_BYTES = 64 * 1024
CONNECT_TIMEOUT_S = 30  # per-read socket timeout: fails stalled transfers


def download_gguf(name: str, progress: Progress | None = None) -> Path:
    """Fetch a GGUF_CATALOG model into models_dir()/whisper.cpp/.
    Returns the final path; no-op when the file already exists."""
    if name not in model_catalog.GGUF_CATALOG:
        raise ValueError(
            f"unknown gguf model '{name}' "
            f"(choose from {sorted(model_catalog.GGUF_CATALOG)})")
    dest = model_catalog.gguf_path(name)
    if dest.exists():
        return dest
    return download_file(model_catalog.GGUF_CATALOG[name]["url"], dest,
                         progress=progress)


def download_file(url: str, dest: Path, progress: Progress | None = None) -> Path:
    """Stream url -> dest via a sibling .part renamed on success.
    Any failure deletes the .part and re-raises; the final file is never
    left half-written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(
        url, headers={"User-Agent": f"FluidVoiceLinux/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT_S) as resp:
            raw = resp.headers.get("Content-Length")
            total = int(raw) if raw and raw.isdigit() else None
            done = 0
            if progress:
                progress(0, total)
            with open(tmp, "wb") as fh:
                while chunk := resp.read(CHUNK_BYTES):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if total is not None and done != total:
            raise OSError(f"truncated download: {done}/{total} bytes")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest
```

Notes: no cycle risk — `model_catalog` imports `backends`+`paths`, neither
imports `model_download` at module level. `while chunk := resp.read(...)`
guards against a zero-length final chunk returning `b""`.

### Tests — `tests/test_model_catalog.py` (new)

Fixture pattern for cache isolation (no tmp monkeypatching of `paths` needed —
it re-reads env): `monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))`.

- `test_gguf_catalog_completeness`: exactly the 7 keys; each value has
  `size`, `langs`, `note`, `url`; every url == the shared
  `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/` base + key.
- `test_gguf_path_is_managed_cache`: `gguf_path("ggml-base.bin")` ==
  `models_dir()/whisper.cpp/ggml-base.bin`.
- `test_gguf_downloaded_true_false`: create the file → True; absent → False;
  unknown name → False.

### Tests — `tests/test_model_download.py` (new)

Fake transport — monkeypatch `urllib.request.urlopen` (module attr in
`fluidvoice.model_download`'s namespace is the same module object; patch
`urllib.request.urlopen` itself) with a context-manager fake:

```python
class FakeResp:
    def __init__(self, chunks, length=None):
        self._chunks, self.headers = list(chunks), {}
        if length is not None:
            self.headers["Content-Length"] = str(length)
    def read(self, n):
        return self._chunks.pop(0) if self._chunks else b""
    def __enter__(self): return self
    def __exit__(self, *a): return False
```

Cases (all offline):
1. happy path: 3 chunks, Content-Length set → final file has exact bytes,
   no `.part` left, progress sequence is monotonic ending `(total, total)`
   starting `(0, total)`, returns dest.
2. no Content-Length → every progress call has `total=None`, download still
   succeeds.
3. mid-stream failure: `read` raises `OSError` after chunk 1 → exception
   propagates, **no final file, no `.part`** anywhere in the dir.
4. `urlopen` raises `urllib.error.HTTPError` (404) → propagates, nothing
   written, parent dir still created.
5. truncated: declared total > bytes actually served → `OSError("truncated")`,
   no final file.
6. `download_gguf("nope.bin")` → `ValueError` matching "unknown gguf model".
7. `download_gguf` when the file already exists → returns path immediately,
   `urlopen` never called (assert via flag).
8. URL fidelity: capture the `Request.full_url` from the fake → equals the
   catalog url for that name; User-Agent header mentions FluidVoiceLinux.

---

## Phase 2 — backend name resolution + config/README docs

### `fluidvoice/backends/whisper_cpp.py` (change `__init__` only)

Replace the current `self.model = (...)` block with resolution + existence
validation:

```python
from .. import model_catalog  # module-level import; no cycle (see Phase 1 note)

raw = (cfg["model"].get("whispercpp_model") or "").strip()
if not raw:
    raise RuntimeError(
        "model.whispercpp_model is required for the whisper.cpp backend "
        "(a catalog name like 'ggml-base.bin' or a path to a ggml/gguf file)")
if "/" in raw or raw.startswith("~"):
    path = Path(raw).expanduser()
    self.model = str(path)
    if not path.is_file():
        raise RuntimeError(f"whisper.cpp model not found: {path}")
else:
    if raw not in model_catalog.GGUF_CATALOG:
        raise RuntimeError(
            f"unknown whisper.cpp model '{raw}' — catalog names: "
            f"{', '.join(sorted(model_catalog.GGUF_CATALOG))}, or give a full path")
    self.model = str(model_catalog.gguf_path(raw))
    if not Path(self.model).is_file():
        raise RuntimeError(
            f"whisper.cpp model '{raw}' not downloaded yet "
            f"(expected at {self.model}) — download it in "
            f"Settings → Models, whisper.cpp GGUF")
```

`self.binary` and language handling stay as-is. `Path` is already imported.
Existing test `tests/test_backend_segments.py::TestWhisperCppSegments` builds
via `object.__new__` and sets `be.model` directly — unaffected.

`fluidvoice/backends/__init__.py`: **no logic change**; the auto-selection guard
`if _whispercpp_binary() and cfg["model"].get("whispercpp_model")` already
accepts names; a stale name now surfaces the clear constructor error (same as a
bad path today). Optionally update the module docstring's whisper.cpp line to
say "external binary + ggml/gguf model (catalog name or path)".

### Config docs

- `fluidvoice/config.py` `DEFAULTS["model"]["whispercpp_model"]` comment →
  `# catalog name (ggml-base.bin...) or path to a ggml/gguf model for whisper.cpp`.
- `fluidvoice/config.py` `TEMPLATE` (line ~179) comment likewise; keep the
  `whispercpp_model = ""` default untouched.
- Validation (`SETTING_RANGES`/`ALLOWED_SETTINGS`) is already `("str", 4096)` —
  catalog names and paths both pass. **No behavior change needed.**
- `README.md`: in the Configuration block, extend the `[model]` sample with
  `# backend = "whisper.cpp"` + `whispercpp_model = "ggml-base.bin"  # catalog
  name or path — download via Settings → Models`; adjust the nearby "A whisper
  model is downloaded on first use" sentence to mention whisper.cpp GGUFs are
  one-click in Settings → Models.

### Tests — extend `tests/test_backends_selection.py` (new class `TestWhisperCppModelResolution`)

Fixture: `monkeypatch.setattr(wc, "_whispercpp_binary", lambda: "/fake/whisper-cli")`
(`import fluidvoice.backends.whisper_cpp as wc`) +
`monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))`.

1. absolute path that exists (`tmp_path/"m.bin"` written) → `be.model` is that
   path unchanged (passthrough).
2. path that does NOT exist → `RuntimeError` matching "not found".
3. `"ggml-base.bin"` with the managed file created under
   `tmp_path/models/whisper.cpp/` → `be.model` resolves there.
4. `"ggml-base.bin"` missing → `RuntimeError` matching both "not downloaded"
   and "Settings" (the hint).
5. `"ggml-bogus.bin"` → `RuntimeError` matching "unknown whisper.cpp model"
   and listing `ggml-base.bin`.
6. empty value → `RuntimeError` matching "required".
7. `~`-prefixed path (monkeypatch `HOME` via `monkeypatch.setenv`) → expanded.

---

## Phase 3 — Models page: "whisper.cpp GGUF" group (app-side download + Use)

### `fluidvoice/gtkui/settings_window.py`

Imports: `from .. import backends, model_catalog, model_download`.
**Call it as `model_download.download_gguf(...)`** (module-attribute lookup at
call time) so tests can monkeypatch
`fluidvoice.gtkui.settings_window.model_download.download_gguf`.

New state in `__init__` (next to `_model_rows`):
```python
self._gguf_rows: list[dict] = []      # {"row": Adw.ActionRow, ...}
self._gguf_dl: dict[str, dict] = {}   # name -> {"bytes": int, "total": int|None,
                                      #        "done": bool, "error": str|None}
```

`_build_models()` — after `page.add(self.models_group)`:
```python
self.gguf_group = Adw.PreferencesGroup(
    title="whisper.cpp GGUF",
    description="Direct ggml models for the whisper.cpp backend")
page.add(self.gguf_group)
```
Also: update `models_group` description to "faster-whisper models (downloaded
on first use)" and the engine row title to
`"whisper.cpp model — name like ggml-base.bin, or a path"` (key/path handling
from Phase 2 makes names valid there).

`_refresh_models()` — after the FW loop, rebuild the GGUF rows with the same
remove-and-readd pattern (`self._gguf_rows`, `self.gguf_group`):

```python
active = self._active_gguf()
for name, info in model_catalog.GGUF_CATALOG.items():
    row = Adw.ActionRow(title=name,
                        subtitle=f"{info['size']} · {info['langs']} languages · {info['note']}")
    if name == active:
        row.add_suffix(Gtk.Label(label="Active", css_classes=["success", "caption"]))
    elif name in self._gguf_dl and not self._gguf_dl[name].get("done") \
            and not self._gguf_dl[name].get("error"):
        row.set_subtitle(_dl_subtitle(name))   # "downloading… 142 MB / 466 MB"
        row.add_suffix(Gtk.Spinner(spinning=True))
    elif model_catalog.gguf_downloaded(name):
        btn = Gtk.Button(label="Use", css_classes=["suggested-action"])
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", self._use_gguf, name)
        row.add_suffix(btn)
    else:
        btn = Gtk.Button(label="Download & use", css_classes=["suggested-action"])
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", self._download_gguf, name)
        row.add_suffix(btn)
    self.gguf_group.add(row)
    self._gguf_rows.append(row)
```

Helpers:

```python
def _active_gguf(self) -> str | None:
    m = self.cfg.get("model", {})
    if str(m.get("backend", "")) != "whisper.cpp":
        return None
    val = str(m.get("whispercpp_model", "")).strip()
    return val if val in model_catalog.GGUF_CATALOG else None

def _dl_subtitle(self, name: str) -> str:  # module-level helper ok too
    st = self._gguf_dl.get(name) or {}
    b, t = st.get("bytes", 0), st.get("total")
    def mb(n): return f"{n / 1_000_000:.0f} MB"
    return f"downloading… {mb(b)} / {mb(t)}" if t else f"downloading… {mb(b)}"
```

Download (worker thread + UI polling — D3):
```python
def _download_gguf(self, _btn, name: str) -> None:
    if name in self._gguf_dl and not (self._gguf_dl[name].get("done")
                                      or self._gguf_dl[name].get("error")):
        return  # already running
    self._gguf_dl[name] = {"bytes": 0, "total": None, "done": False, "error": None}
    self._refresh_models()

    def work():
        st = self._gguf_dl[name]
        try:
            model_download.download_gguf(
                name, progress=lambda b, t: st.update(bytes=b, total=t))
            st["done"] = True
        except Exception as e:  # noqa: BLE001 - surfaced as a toast
            st["error"] = str(e)[:300]
    threading.Thread(target=work, daemon=True).start()
    GLib.timeout_add(400, self._poll_gguf_dl, name)

def _poll_gguf_dl(self, name: str) -> bool:
    st = self._gguf_dl.get(name)
    if st is None or not (st.get("done") or st.get("error")):
        # still running: refresh the row subtitle cheaply
        for row in self._gguf_rows: ...
        # simplest correct: self._refresh_models() (rows are rebuilt anyway)
        return True
    self._refresh_models()
    if st.get("error"):
        self.toast(f"download failed: {st['error']}")
    else:
        self.toast(f"{name} downloaded — click Use to switch")
    return False  # stop the timer
```
While running, `_poll_gguf_dl` returning `True` after a
`self._refresh_models()` gives live subtitles (400 ms cadence; download state
survives the rebuild because it lives in `self._gguf_dl`, not the rows).

Use (goes through validated config per D1 — the daemon hot-reloads the engine
because both keys are ENGINE_KEYS; file-only when the daemon is down):
```python
def _use_gguf(self, _btn, name: str) -> None:
    if not model_catalog.gguf_downloaded(name):
        self.toast(f"{name} is not downloaded yet"); return
    try:
        resp = self.c.set_config({"model": {
            "backend": "whisper.cpp", "whispercpp_model": name}})
    except Exception as e:
        self.toast(str(e)); return
    if resp.get("rejected") or resp.get("errors"):
        self.toast("Rejected: " + ", ".join(
            (resp.get("rejected") or []) + (resp.get("errors") or []))); return
    self.toast(f"Switching to whisper.cpp ({name})…")
    self._load()                      # resync cfg + rows
    GLib.timeout_add_seconds(1, self._poll_model)   # existing warmup poll
```
`Client.set_config` already falls back to file-only writes when the daemon is
down — no client change needed.

### Tests — extend `tests/test_gtkui.py` `TestSettingsWindow`

Reuse `StubClient` (its `set_config` records into `self.saved`; `status`
reports warmup idle). Pump with the existing `pump_until` helper.

1. `test_gguf_group_rows_built`: window builds; assert
   `len(w._gguf_rows) == len(model_catalog.GGUF_CATALOG) == 7` and every row
   title is in `GGUF_CATALOG`.
2. `test_active_gguf_marker`: StubClient `get_config` overridden to return
   DEFAULTS with `model = {**DEFAULTS["model"], "backend": "whisper.cpp",
   "whispercpp_model": "ggml-small.bin"}` → `w._active_gguf() ==
   "ggml-small.bin"` and that row's subtitle/`Gtk.Label` shows "Active"
   (assert via `w._active_gguf()` plus scanning the row suffix children, or
   simply the helper + no Use button on it — keep it smoke-level).
3. `test_download_flow_uses_worker_and_polls`: monkeypatch
   `fluidvoice.gtkui.settings_window.model_catalog.gguf_downloaded` →
   False initially; fake `download_gguf` records calls, invokes
   `progress(50, 100)` then `progress(100, 100)`, returns the path; click via
   `w._download_gguf(None, "ggml-small.bin")`; `pump_until(lambda:
   w._gguf_dl["ggml-small.bin"].get("done"))`; assert fake got the name and
   `st["total"] == 100`; then monkeypatch `gguf_downloaded` → True and
   `w._refresh_models()` → the row now offers "Use".
4. `test_download_failure_toasts`: fake raising `OSError("net down")`;
   `pump_until(... error ...)`; assert `st["error"] == "net down"` and a toast
   fired (monkeypatch `w.toast` to append to a list).
5. `test_use_gguf_posts_config`: monkeypatch `gguf_downloaded` → True;
   `w._use_gguf(None, "ggml-base.bin")`; assert
   `c.saved[-1]["model"] == {"backend": "whisper.cpp",
   "whispercpp_model": "ggml-base.bin"}`.
6. `test_use_gguf_rejected_toasts`: StubClient variant returning
   `{"ok": False, "rejected": ["model.backend"], ...}` → toast contains
   "model.backend", `c.saved` untouched.

These are skipped on headless boxes exactly like the existing GTK tests —
the suite stays green either way.

---

## Phase 4 — doctor report + ledger/docs

### `fluidvoice/doctor.py`

Load config (`from .config import load_config`) and add a whisper.cpp block
after "speech backends":

```python
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
    have = sorted(p.name for p in model_catalog.gguf_dir().glob("ggml-*.bin")
                  if p.is_file()) if model_catalog.gguf_dir().is_dir() else []
    lines.append("  downloaded ggml models: " + (", ".join(have) if have else "none"))
    return lines
```
`run()` prints `print("\nwhisper.cpp:")` + these lines. Needs `from pathlib
import Path` (add) — the rest of doctor is untouched.

### Tests — `tests/test_infra.py` (new class `TestDoctorWhispercpp`)

Directly test `_whispercpp_lines` (pure function, monkeypatch
`XDG_CACHE_HOME` + `backends._whispercpp_binary`):
- binary missing + empty setting → "not found" + "not set".
- catalog name downloaded (create the file) → "downloaded" + the resolved path.
- catalog name missing → "not downloaded".
- path value existing/missing → "found"/"MISSING".
- unknown bare name → "unknown name".

### Ledger / docs

- `docs/STATUS.md`: strike the v0.4 checklist row `whisper.cpp GGUF
  auto-download; model manager` and fold it into the Done section (one bullet
  under the native GTK app / models area: curated GGUF catalog, streaming
  one-click download with progress, name-or-path `model.whispercpp_model`,
  doctor resolution report). Update the "Last updated" date and test count to
  the post-build reality.
- `docs/ROADMAP.md` line ~41: mark the whisper.cpp GGUF item done (one line).
- README edits are in Phase 2; no BEHAVIOR-SPEC/UPSTREAM-TRACKING edits needed
  beyond STATUS.md (that file holds the tracked checklist).

---

## Verification per phase

After each phase:
```bash
.venv/bin/python -m pytest -q tests --ignore=tests/integration
```
Exit 0 = green (439 at start; ~+22 by the end: 3 catalog + 8 download + 7
backend + 6 gtkui + 5 doctor — exact split may shift, count only grows).
GTK tests require a display; they self-skip headless, which is still green.

Final smoke (optional, no assertions needed):
```bash
.venv/bin/python -c "from fluidvoice import model_download, model_catalog; \
print(sorted(model_catalog.GGUF_CATALOG))"
.venv/bin/python -m fluidvoice doctor | sed -n '/whisper.cpp/,+3p'
```
Do **not** run a real download in CI/tests — network is mocked everywhere
(`urlopen` fakes, `gguf_downloaded` monkeypatched in UI tests).

## File-by-file summary

| File | Change |
|---|---|
| `fluidvoice/model_catalog.py` | + `GGUF_CATALOG` (7 entries), `gguf_dir/gguf_path/gguf_downloaded` |
| `fluidvoice/model_download.py` | NEW — `download_gguf`, `download_file` (.part + os.replace, progress) |
| `fluidvoice/backends/whisper_cpp.py` | name-or-path resolution + clear missing-file errors |
| `fluidvoice/gtkui/settings_window.py` | GGUF group, download worker + GLib polling, Use → set-config |
| `fluidvoice/doctor.py` | `_whispercpp_lines` + printed block |
| `fluidvoice/config.py` | comments only (DEFAULTS + TEMPLATE wording) |
| `fluidvoice/daemon.py`, `fluidvoice/control.py`, `fluidvoice/gtkui/client.py` | **unchanged** (decision D1) |
| `README.md`, `docs/STATUS.md`, `docs/ROADMAP.md` | doc/ledger updates |
| `tests/test_model_catalog.py`, `tests/test_model_download.py` | NEW |
| `tests/test_backends_selection.py`, `tests/test_gtkui.py`, `tests/test_infra.py` | new test classes |

## Out of scope (restated)

sha256/checksums, resumable downloads, converting faster-whisper models,
torch backend models, quantized variants beyond the 7 curated files, remote
model listings, daemon-side download actions, deleting/freeing models.
