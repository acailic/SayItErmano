# Native GTK settings & history app — verification & gap-closure plan

Session: `06ad7630` · Repo: FluidVoiceLinux, branch `linux` · Spec:
`docs/superpowers/specs/2026-09-02-native-settings-app-design.md` (authoritative)

## 0. Critical context — read this first

**The task prompt is stale.** It was authored while the tree still carried uncommitted
WIP (per-app prompts + MPRIS pause) and a live `fluidvoice/webui.py`. Between then and
now, the entire spec was implemented and committed:

- `1ce8734` — *feat!: native GTK settings/history app replaces the web UI* — implements
  the spec end to end (its message explicitly says it absorbed the in-flight per-app
  prompts / MPRIS-pause WIP): `config.apply_settings` with the full validator tables,
  socket actions `get-config`/`set-config`/`select-model`/`mics`, `status.warmup`,
  `fluidvoice/gtkui/` (application / main_window / settings_window / onboarding /
  client), tray + first-run + CLI spawning, `webui.py` + `[server]` section deleted,
  doctor GTK check, deb Depends, docs ledger, tests.
- `922584a` — *settings v1.1* — added the custom-dictionary editor, filler-word editor,
  and language picker (originally out-of-scope v1; landed by a follow-up request).

Default suite as of planning: **329 passed** in ~13 s
(`.venv/bin/python -m pytest -q tests --ignore=tests/integration`).

Three read-only recon passes (daemon/config, gtkui, CLI/tray/doctor/packaging/docs)
verified every spec item against the tree: **all core items CONFIRMED**, with only the
small residuals this plan closes. Therefore:

> **Do NOT re-implement, revert, or "clean up" anything from `1ce8734` / `922584a`.
> This plan is a gap-closure + final-verification pass, in the spec's build order.**

### Ground rules

- Test gate after every phase (must stay green):
  `.venv/bin/python -m pytest -q tests --ignore=tests/integration`
  → expect 329 passing now; 331–333 after Phases 1–2 additions.
- Leave untouched: `requests/` (untracked request record), `adws/`, `.claude/`,
  `docs/superpowers/specs/` (historical records), `.venv/`.
- Never commit build outputs (`build/`, `dist/`) or `__pycache__`.
- The `requests/` dir stays untracked — do not add it to the commit.

---

## Phase 0 — Baseline (no changes)

1. `git log --oneline -3` → HEAD is `1c83709`, with `922584a` and `1ce8734` beneath.
2. `git status --short` → only `?? requests/` untracked. Anything else unexpected:
   STOP and reassess before editing.
3. Run the test gate → 329 passed.

## Phase 1 — Daemon/config residuals (spec build-order step 1, tail end)

Everything in this step already landed and is tested *except* two coverage holes found
by recon. Add only tests here — no production-code changes.

**File: `tests/test_daemon.py`** (class `TestSocketConfigActions`, after the existing
`test_select_model_warms_and_hot_swaps` / `test_status_includes_warmup`, ~line 688):

1. `test_mics_action` — monkeypatch `fluidvoice.tray.list_microphones` (note: the
   daemon imports it *inside* the handler at `fluidvoice/daemon.py:664`, so patch the
   `fluidvoice.tray` source, e.g. `monkeypatch.setattr("fluidvoice.tray.list_microphones",
   lambda: ["Default", "USB Mic"])`) → `d.handle_request({"action": "mics"})` →
   assert `{"ok": True, "mics": ["Default", "USB Mic"]}`.
2. `test_select_model_failure_rolls_back` — fake backend factory whose `warmup()`
   raises; start with cfg `model.name == "small"`; request `select-model` for a valid
   catalog name (mirror `test_select_model_warms_and_hot_swaps`, tests/test_daemon.py:648–686)
   → wait for the warmup thread with the existing idiom
   (`while d.warmup["running"] and time.monotonic() < deadline: …` — see :681) →
   assert `d.warmup["error"]` is truthy, `d.cfg["model"]["name"] == "small"` (rolled
   back, `fluidvoice/daemon.py:609–613`), and `d.backend` was not hot-swapped.

**Artifact cleanup (git-ignored, no commit):**

```
rm -rf build dist
find . -path ./.venv -prune -o -name "webui*.pyc" -print -delete
find tests -name "test_webui*.pyc" -delete -o -name "test_daemon_http*.pyc" -delete
```

(`build/lib/fluidvoice/webui.py` and the stale `.pyc`s of deleted modules are the only
places the old web UI still exists on disk; deleting prevents accidental resurrection.
`build/`/`dist/` are regenerable outputs.)

**Gate:** suite green (expect 331).

## Phase 2 — gtkui residuals (spec build-order step 2, tail end)

**One real gap:** the Settings **About** section lacks the spec's "backend, CUDA" rows
(spec: "About (version, backend, CUDA)"). Version + file paths exist
(`fluidvoice/gtkui/settings_window.py`, `_build_about` ~:833).

**File: `fluidvoice/gtkui/settings_window.py`**

- In `_build_about`, add two rows after "Version": `self.about_backend_row = Adw.ActionRow(title="Backend", subtitle="—")`
  and `self.about_gpu_row = Adw.ActionRow(title="GPU (CUDA)", subtitle="—")`, added to
  the group like the existing rows.
- Populate them from the status poll that already exists: `_refresh_models`
  (:403, which already calls `self.c.status()` — see :425) — set backend subtitle from
  `st.get("backend")` and GPU from `"yes" if st.get("cuda") else "no"` (same wording as
  the History header, `main_window.py:351–352`); when status is `None`/empty leave
  "—" (daemon offline). Keep it to the two `set_subtitle` calls + guards — no new
  polling loop.

**File: `tests/test_gtkui.py`**

- Extend the settings-populate test (the one asserting ≥40 rows / families, ~:122–136)
  to assert the About page has rows titled "Backend" and "GPU (CUDA)" whose subtitles
  reflect `StubClient.status()` (`backend: "faster-whisper"`, `cuda: True` → "yes").
  Runs under the existing offscreen skip guard (:14–21) — do not touch the guard.

**Optional (only if trivial, must keep suite green):** `TestClientFileOnlyMode`
(~:252–281) has no GTK dependency (`client.py` is GTK-free) but is display-gated by
living in this module; relocating it to a new `tests/test_gtkui_client.py` (plain
imports, no display guard) lets headless boxes run it. Skip if anything fights back.

**Gate:** suite green (expect 332, or 334 with the relocation);
`xvfb-run -a .venv/bin/python -m pytest -q tests/test_gtkui.py` if xvfb is available —
otherwise note the skip.

## Phase 3 — Web-UI retirement residue: docs (spec build-order step 3, tail end)

Packaging is already correct (deb `Depends` gains `python3-gi gir1.2-gtk-4.0
gir1.2-adw-1` at `packaging/build-deb.sh:85`; venv `--system-site-packages` at :27;
`.desktop` `Exec=/usr/bin/fluidvoice settings` + `StartupNotify=true` at
`packaging/fluidvoice-linux.desktop:7,12`) — **no packaging changes**. Only these doc
fixes (each is the only remaining place the deleted web UI is described as current):

1. **`README.md:106–110`** — delete the web-era hardening sentence ("The page is
   hardened against cross-site requests (Host/Origin checks, JSON-only POSTs, 64 KB
   body cap) and the stored key is only ever attached to the endpoint host you saved —
   a website can't use it as an exfiltration relay."). Keep the surrounding true
   sentences (strict whitelist / API keys never exposed / 0600 file perms) and, if a
   replacement is wanted, one sentence: settings talk to the daemon over the
   user-owned unix control socket — no network listener exists.
2. **`docs/UPSTREAM-TRACKING.md:170`** — v1.6.0 onboarding row: replace
   "`/onboard` web page opens once on first launch" with "native onboarding window
   (`fluidvoice app --onboard`) opens once on first launch"; keep the rest of the note
   (mic/engine/hotkey checks + real 3 s tryout).
3. **`docs/UPSTREAM-TRACKING.md:92`** — settings-chrome changelog row: flip 🚧 → ✅,
   note: "native Settings window (Adw Preferences pages) covers the same knobs — v1.1
   adds dictionary/filler editors + language picker; richer upstream provider profiles
   remain on the roadmap" (parity target of "same knobs" is met; roadmap remainder
   stays visible, matching how the theming row at :170 reads).
4. **`docs/STATUS.md:161`** — drop the stale row
   `| Web UI API + security | endpoint tests + CSRF/rebinding/validation matrix + live curl checks |`
   and replace with
   `| Socket config actions (get/set-config, select-model) + apply_settings | unit (fake backend factory) + real-daemon socket integration |`.
5. **`docs/STATUS.md:3`** — refresh the header count to the real numbers (run the gate
   plus `pytest -q tests --collect-only tests/integration 2>/dev/null | tail -1` and
   write e.g. "332 automated tests (330 offline + …)"; keep the existing format).
6. **`docs/STATUS.md:115–123`** ("🚧 Left") — three unchecked bullets are already done
   elsewhere in the ledger/ROADMAP: `:118` live streaming preview, `:122` spoken-send,
   `:123` per-app prompt sets. Delete those three bullets (their done-state is
   recorded in ROADMAP/STATUS Done); leave genuinely-open items untouched.
7. **`fluidvoice/model_catalog.py:1–2`** — docstring says "shared by the web UI today…
   neutral home so webui.py can be deleted" → reword to reflect reality:
   "Model catalog + download cache probe (shared by the native GTK app, CLI, and
   daemon)."

**Gate:** suite green (docs-only, but always re-run).

## Phase 4 — Live desktop verification + ledger sign-off (spec build-order step 4)

This GNOME box has GTK 4.14 / libadwaita 1.5 / PyGObject system-wide.

1. `.venv/bin/python -m fluidvoice doctor` → prints
   `settings app: GTK 4 + libadwaita OK` (`fluidvoice/doctor.py:67–73`).
2. Start the daemon (README/systemd user unit, or `.venv/bin/python -m fluidvoice
   daemon` in a terminal). Then verify, in order:
   - `fluidvoice app` → History window (status header, search, entry cards).
   - `fluidvoice app --open settings` → Settings window; launch `fluidvoice settings`
     again → single-instance: raises the existing window, no second instance.
   - Save a no-op/harmless setting with the daemon up → toast lists changed keys;
     stop the daemon → banner + file-only save path applies on next start.
   - Models page: switch model (if another catalog model is downloaded) → spinner →
     active; History header reflects the new model.
   - Tray: "Settings…" and "History" items spawn/activate the app (no browser).
   - First-run: `rm ~/.local/share/fluidvoice/.onboarded` (path via
     `paths.data_dir()`), restart daemon → onboarding window auto-opens with the 3 s
     tryout; complete it → History raises.
   - History: copy, delete (confirm), clear-all (confirm), audio replay (and the
     "Open audio" xdg-open fallback if GStreamer media init fails).
3. Screenshots of History + Settings windows (GNOME screenshot tool). The spec defers
   README screenshots ("later") — store under `docs/screenshots/` only if trivial;
   not a gate.
4. Optional but recommended (spawns its own daemons, sets `SAYITERMANO_NO_APP_SPAWN`
   itself per `tests/integration/conftest.py:53`):
   `.venv/bin/python -m pytest -q tests/integration/test_daemon_socket.py`.
5. Final full gate + `git diff --stat` sanity: only the Phase 1–3 files changed.
   Commit everything in one commit:

   ```
   chore: native-app spec residuals — About backend/CUDA rows, mics/rollback
   tests, retire stale web-UI doc mentions
   ```

---

## Definition of done (maps to the task's done-criteria)

- Phased, file-level, executable without questions ✔ (each phase above names files,
  anchors, and the exact edits/tests).
- Each phase leaves the default suite green ✔ (gate after every phase).
- Spec build order preserved ✔ (Phase 1 = validation/socket layer tail; Phase 2 =
  gtkui tail; Phase 3 = webui-removal/docs tail; Phase 4 = live verification +
  ledger).
- Unit tests for `apply_settings` ✔ — already exist, comprehensive
  (`tests/test_config_settings.py::TestApplySettings`: full-surface roundtrip incl.
  `pause_media`/`per_app_prompts`, nothing-half-applies, unknown/retired-key
  rejection, `model.name` aliasing, masking); this plan adds the two missing
  socket-action tests (`mics`, select-model rollback) rather than duplicating them.

## Out of scope (unchanged, per spec)

History ZIP export, stats page, Parakeet/streaming models, Wayland insertion,
upstream's `/v1` local HTTP API. (The dictionary/filler editors and language picker
were out-of-scope v1 but landed deliberately in v1.1 — they are kept, not reverted.)
