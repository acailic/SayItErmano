# The ContextProvider seam (P2 — contextual dictation)

Plan source: [research/2026-09-10-reliability-first-improvement-program.md](../research/2026-09-10-reliability-first-improvement-program.md)
(P2). This note records the seam, its privacy invariants, the
per-app behavior profiles, the legacy migration, and what is
deliberately left to the manual smoke matrix.

Status: **prototype-grade, shipped OFF by default** (`context.enabled =
false`). Turning the default on is gated on the live smoke matrices
([wayland-smoke-matrix.md](wayland-smoke-matrix.md)) —
REQUIRED-BEFORE-PARITY.

## The seam

`fluidvoice/context/` — one value, one read, two providers:

- **`FocusContext`** (`base.py`, frozen dataclass): `app_id`,
  `window_title`, `accessible_role`, `selection_text`,
  `preceding_text`, `provider_name`, `stale`, `missing`.
- **`ContextProvider`** protocol: `available()` (cheap dependency
  probe) and `read_focus_context(max_preceding)` (the one data read).
- **`X11Provider`** (`x11_provider.py`): the existing
  xdotool/xprop WM_CLASS + window-title probing, lifted behind the
  seam. X11 has no accessibility text surface — role, selection and
  preceding text are always `None` (identity-only context).
- **`AtspiProvider`** (`atspi_provider.py`): the freedesktop
  accessibility bus. python-atspi or GIR `Atspi` is imported **lazily,
  once**; a missing/broken import is cached and the provider reports
  unavailable — never an exception in the take path. There is no
  direct "give me the focus" query in at-spi2, so the read walks
  desktop → application → ACTIVE window → FOCUSED descendant under
  strict `ReadLimits` (apps/windows/children/depth/node budget). When
  no FOCUSED descendant is found, the ACTIVE window is returned as an
  **identity-only, `stale=True`** context: its app IS the focused
  app, but role/selection/preceding are withheld (they would not be
  provably about the focused field).

Factory (`__init__.py`): `reader_for(cfg)` returns a **zero-arg
callable or `None`** — not a provider handle. `context.provider`
(`auto` default) picks AT-SPI on Wayland sessions, the X11 provider
on X11/unknown; `atspi` works on X11 too (early adopters get
role/selection/preceding there); `none` disables. Any
unavailable provider (missing xdotool, no atspi bindings) yields
`None`: the take then runs with exactly today's behavior.

## Privacy invariants (each pinned by tests)

1. **Insertion-time only.** The pipeline calls its reader exactly
   once, in `DictationPipeline.run`, immediately before `_insert`
   (`tests/test_context_consumers.py::TestReadDiscipline` — not on
   empty transcriptions, not in rewrite/command mode, after polish).
   The factory hands out a one-shot callable, so there is no handle
   to pre-read with, and nothing is cached.
2. **Bounded.** `FocusContext.__post_init__` truncates selection and
   preceding text to `MAX_PRECEDING_CHARS` (500) no matter what a
   provider returns; the user knob (`context.max_preceding_chars`,
   default 120) can only go lower. AT-SPI truncates at the source.
3. **Never persisted.** The FocusContext (any field — including the
   app identity) never reaches a history entry, the config file or a
   log line. History keeps exactly today's row shape (`app` = the
   take-start hint only); `FocusContext.__repr__` prints lengths, not
   content, so an accidental `log(f"{ctx}")` leaks nothing. Pinned by
   `TestPrivacy`.
4. **Ephemeral by construction.** The value lives in one local of
   `run()`; the only derived state kept is `_focus_identity` (the app
   NAME — the same class of data the daemon already logs at take
   start), reset every take.

## Consumers

When context is usable (identity consumers) or field-usable
(role/text consumers):

- **Sentence capitalization + spacing** (`base.apply_continuation`,
  applied in `_apply_focus_formatting`): one leading space iff the
  field's bounded preceding text ends mid-line; first letter
  capitalized iff the field is blank or visibly ends a sentence
  (`. ! ? … 。 ！ ？`, trailing whitespace ignored). Never lower-cases
  (mid-sentence polish casing is kept — downcasing would mangle
  proper nouns). Unknown preceding (`None`) changes nothing.
- **GAAV continuous dictation**: an explicit profile
  `formatting_mode = "gaav"` forces GAAV for the app; without a
  profile, a search-like accessible role (`search`) triggers GAAV for
  that take (lowercase first, trailing period stripped, no
  continuation capitalization).
- **Terminal safety / Wayland app hints**: the insertion-time
  identity flows into `insert_text` as `wm_class` (the default
  inserter wrapper threads it; no second xdotool lookup). On Wayland
  this is the first source of app identity — terminal quirks
  (`ctrl+shift+v`, autocomplete trailing space) become ACTIVE when
  AT-SPI identifies a terminal, and stay inert (today's behavior)
  when context is missing.
- **Spoken-send policy**: resolved per-app at phrase-parse time
  (take-start hint) and **re-checked at the Enter keypress** with the
  insertion-time identity — a terminal recognized only at insertion
  (the Wayland case) still suppresses Enter. `spoken_send = "on"`
  explicitly overrides the terminal block.
- **AI-polish prompts**: canonical profile instructions/preset first,
  then the legacy `ai.per_app_prompts` — identical to pre-P2 while no
  canonical rules exist. (Prompt choice happens at polish time, which
  today means the take-start hint; Wayland prompt selection waits for
  a take-start identity source.)

**Compat floor** (the plan's hard rule): whenever context is missing
— feature off, provider unavailable, read failed, `missing`/`stale`
flags — every consumer degrades to byte-identical pre-P2 behavior.
Pinned by `TestMissingContextCompat`, `TestGaav::test_plain_text_role_no_gaav`,
the unchanged `test_insertion.py`/`test_daemon.py` suites, and the
DEFAULTS-off pipeline default.

## Per-app behavior profiles

Canonical `[profiles]` config section (`fluidvoice/profiles.py`):

```toml
[profiles]
rules = [
  { match = ["gnome-terminal", "kgx"], terminal = true, spoken_send = "off" },
  { match = ["zed"], instructions = "Bullet style, no greetings.",
    insertion_mode = "paste", formatting_mode = "gaav" },
]
```

- `match`: case-insensitive substrings of the app identity (same
  convention as `general.terminal_apps` / `ai.per_app_prompts`), `*`
  matches everything, **first match wins**.
- Fields: `terminal` (bool), `prompt_profile` (a named AI prompt
  preset) or `instructions` (inline prompt text — pick one),
  `insertion_mode` (`typed|paste|auto`), `formatting_mode` (`gaav`),
  `spoken_send` (`on|off`). Empty = inherit the global setting.
- Resolution: canonical rule → legacy keys (`general.terminal_apps`
  for terminal-ness, `ai.per_app_prompts` for prompts) → all-inherit
  default. The legacy keys stay read as fallback **until v1.0**.
- The config coercer rejects unknown fields (hand-edit typos surface
  at save time) and one prompt source per rule.

Engineering decision: the plan lists four policy fields (prompt
profile, insertion mode, formatting mode, spoken-send policy);
terminal-ness is carried as a fifth, explicit `terminal` flag because
that is the only faithful way to migrate what `terminal_apps` drives
(paste key + autocomplete space + Enter blocklist) without conflating
"never auto-Enter" with "is a terminal".

## Legacy migration (first settings save)

`profiles.migrate_legacy_profiles` runs inside `config.save_config`:

- Guarded by `profiles.migrated_from_legacy` — one-time, idempotent;
  re-saves never duplicate or rewrite rules.
- `ai.per_app_prompts` → rules `{match = apps, instructions = ...}`.
- A terminal list that **differs from the shipped default** → one
  rule `{match = <the customized list>, terminal = true,
  spoken_send = "off"}`. An uncustomized default list is NOT
  migrated (it keeps applying via the legacy fallback; migrating
  shipped defaults would duplicate noise for every user).
- Existing canonical rules are never clobbered; the legacy keys are
  left untouched in the file (still the read fallback until v1.0, and
  the pre-v1.0 settings UI still edits them).

## Why off by default

The seam's offline tests are complete (fakes only — no test ever
touches a live X/AT-SPI session), but the AT-SPI adapter's live
behavior across toolkits (GTK4/libadwaita, Qt, Electron, terminals)
can only be validated by hand. Until the smoke matrices pass,
enabling `context.enabled` is an early-adopter opt-in, and every
default install keeps byte-identical pre-P2 behavior.
