# Improvement audit — 2026-09-08

> **Status update (same day, second pass):** C1 (socket-steal refusal +
> `SAYITERMANO_SOCKET`), C3 (`.monitor` escape), C4 (config meta-tests),
> C6 (doctor module line), D1 (ruff, minimal ruleset), D4 (standalone
> server smoke + banner flush), E1 (AUR recipe @0.7.0 + digest), E3
> (installer autostart skip — live-verified), E6 (already existed:
> `SAYITERMANO_NO_UPDATE=1`), E2 (`scripts/release.sh`), F4
> (`docs/dev/e2e-sandbox.md`) — all SHIPPED. Open: C2 (moot — C1 covers
> the class), C5 (monolith split), D2/D3 (xdist, gtkui speed), E4 (deb
> slimming), E5 (CHANGELOG), plus the section-A feature items.

Full sweep of improvement opportunities after shipping v0.7.0: product
gaps, upstream signals, robustness/architecture, testing, distribution,
UX. Each item carries its evidence. Quick wins marked ⚡.

## A. Product/feature gaps (restated with current priority)

1. **AT-SPI caret-context smart typing + GAAV** — unlocks upstream-parity
   smart caps (their #840 misfires; we can do better) and the continuous
   dictation formatting chain. Biggest remaining feature item.
2. **Per-app behavior profiles** — unify per-app prompts, terminal rules
   and insertion mode into one editor; the app hint is already captured.
3. **Diarization for file transcription** — upstream shipped it v1.6.8;
   their most-reacted issue #18.
4. **n-best pick lists** — still blocked on backends exposing alternates.
5. **Parakeet Realtime / Nemotron streaming** — true streaming preview
   (the segmented engine approximates it today at constant cost).
6. **Chunked file transcription** — inputs >25 MB warn and decode
   monolithically (`transcribe` CLI + the new socket route); chunking
   would lift the 200 MB API cap too.
7. **Wayland live smoke per compositor** — blocked on this desktop (no
   compositor, no sudo; noted in STATUS). Needs a real sway/GNOME-Wayland
   session.
8. **nix flake** — last packaging surface not covered (deb/AUR/pipx/one-
   shot all shipped).

## B. Upstream tracking (fresh as of 2026-09-08)

Latest upstream release **v1.6.9 (2026-08-18)**, but the repo moved after
it — refresh `docs/UPSTREAM-TRACKING.md` (`scripts/upstream-diff.sh`) and
review:

- **#950 "Reduce dictation latency and add pipeline evaluation" (09-07)**
  — they built a *pipeline evaluation harness*. We have none; our only
  levers are the first-word probe test + preview stats line. A small
  eval harness over the fixture corpus would make changes like the B7
  countdown measurable.
- **#947 "refine 1.6.10 intelligence and dictation UX" (09-04)** — v1.6.10
  is in the works; watch for UX changes worth porting.
- **PR #939 mouse-tap-split + follow-up hotkey fixes (09-03)** — "stop
  interrupted mouse holds", "preserve mouse press lifecycle": check
  whether these fix classes apply to our mouse-PTT port (P8).

## C. Robustness / architecture (observed live this session)

1. **Control-socket collision** ⚡-ish — `paths.socket_path()` is global
   to XDG_RUNTIME_DIR, so any second (e.g. sandboxed) daemon *unlinks and
   steals the production socket* while its own lock (`daemon.lock` under
   XDG_CONFIG_HOME) happily passes. Observed twice during v0.7.0 smokes.
   Fix: `control.serve()` should probe the existing socket first and
   refuse to bind when a live daemon answers (or namespace the socket by
   config dir).
2. **Lock vs socket isolation mismatch** — same root cause as C1: one
   path is XDG_CONFIG_HOME-scoped, the other runtime-scoped. Aligning
   both fixes the whole class.
3. **`.monitor` sources are excluded from mic listings** (micmon.py:37,
   tray.py:61) — right for real users, but virtual-mic workflows (testing,
   monitoring a sink) need the `module-virtual-source` workaround we used
   in smokes. An explicit `recording.device` override that bypasses the
   listing filter (or a doctor hint) would remove the trap.
4. **Config key registration sprawl** ⚡ — every new key must land in 6+
   parallel structures (DEFAULTS, RANGES/ENUMS/BOOLS, ALLOWED_SETTINGS,
   _SAVE_WHITELIST, sometimes ENGINE_KEYS + TEMPLATE). Nothing
   cross-checks them today (only per-key asserts). A meta-test asserting
   `DEFAULTS ⊆ ALLOWED_SETTINGS` (minus a declared local-only set) and
   `DEFAULTS ⊆ _SAVE_WHITELIST` would have caught near-misses during the
   remote-stt/B7 work.
5. **Monolith split** — daemon.py 2562 lines (Daemon + DictationPipeline
   + preview wiring + socket dispatch), settings_window.py 2132,
   config.py 1104. Extract `pipeline.py` + socket dispatch; split the
   settings window per page. Pure refactor, suite is the safety net.
6. **cwd resolves different code trees** ⚡ — running doctor/CLI from the
   repo vs elsewhere imports different copies (editable install vs cwd
   shadowing; burned real time twice this session). Have `doctor` print
   the loaded `fluidvoice.__file__`.

## D. Testing / CI

1. **No lint tooling** ⚡ — no ruff/flake8 anywhere (pyproject or venv).
   Source currently has zero TODO/FIXME markers; adding `ruff check`
   (line-length + isort only, to start) keeps it that way cheaply.
2. **No pytest-xdist** — suite is 1712 tests / ~105 s single-proc. xdist
   would roughly halve it; GTK tests need per-worker displays.
3. **test_gtkui is ~half the suite wall time** (several 2+ s window
   constructions, e.g. `test_use_gguf_posts_config` 2.4 s) — build the
   window once per class where state allows.
4. **Standalone-main paths of test helpers are untested** — the fake STT
   server shipped with a crashing banner in error modes (found live,
   fixed); add a tiny test that runs `main()` in `http500` mode.
5. Config-whitelist meta-test — see C4.

## E. Distribution / release

1. **AUR recipe is stale** ⚡ — `packaging/aur/PKGBUILD` says `pkgver=
   0.5.0`, two releases behind. Bump to 0.7.0 and push to the AUR
   (maintainer action; the recipe lives in-repo).
2. **Release is fully manual** — bump two files, build, gate, `gh
   release`, machine refresh, autostart cleanup. A `scripts/release.sh`
   would encode the recipe (incl. the traps: dist/ stale debs, autostart
   re-creation, two-spot version).
3. **One-shot installer re-creates `~/.config/autostart` on every run**
   ⚡ — breaks the "unit owns startup" single-path rule each upgrade
   (observed on the v0.7.0 machine refresh; had to delete it again). The
   installer should skip autostart when the user unit exists and is
   enabled.
4. **deb is 77 MB** — investigate venv slimming behind the clean-archive
   gate (prune `__pycache__`, pip caches, test dirs from the bundled
   venv).
5. **No CHANGELOG.md** — release notes live only on GitHub; a generated
   changelog file would help distro packagers.
6. **`SAYITERMANO_NO_UPDATE` env** ⚡ — sandboxed/dev instances currently
   phone GitHub on every start; an opt-out env var would keep smokes
   fully offline.

## F. UX / product polish

1. **Remote takes show no feedback while POSTing** — no preview by
   construction in v1; a pill "processing" state during the request would
   close the perceived gap.
2. **Refusal-guard patterns are English-only** — accept community
   pattern lists later (STATUS divergence row documents this).
3. **README has no TOC** — it's long now; a small table of contents up
   top would help.
4. **The Xvfb e2e recipe lives only in agent memory** — publish it as a
   repo doc (`docs/dev/e2e-sandbox.md`): the working Xvfb incantation
   (pill needs a 32-bit visual — the default screen config can't render
   it), `xdotool key Control_R` vs bare-Xvfb passive grabs, the
   `module-virtual-source` mic trick, and the pkill self-match trap.

## Suggested order

Quick wins first (C4, C6, D1, D4, E1, E3, E6, F4 — most are < 1 h each),
then C1/C2 (socket safety), B (upstream refresh + eval harness idea),
then the big A items as briefed specs.
