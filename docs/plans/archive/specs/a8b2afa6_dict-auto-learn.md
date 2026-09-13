# Plan: Dictionary auto-learning from post-insertion corrections

Request: `requests/dictionary-auto-learn.md` (roadmap "Later" item, ROADMAP.md:86;
UPSTREAM-TRACKING.md:169 lists upstream's version as ⏳). The user's own inline
repairs (history.update_text, unblocked by aa24ebf) become the signal that feeds
**suggested** custom-dictionary entries. Upstream semantics were verified against
`altic-dev/Fluid-oss` branch `b/automatic-custom-dictionary` @ `ae9b71a` (merged
into `upstream/main`; the 1.6.3 "Auto-suggested custom-dictionary replacements"
feature) — quotes below.

Test gate for every phase: `.venv/bin/python -m pytest -q tests --ignore=tests/integration`.

## Upstream semantics (verified, quoted)

Source: `git show ae9b71a:Sources/Fluid/Services/AutomaticDictionaryCorrectionTracker.swift`
(and sibling files on that branch).

1. **Suggest, never silent auto-add.** `evaluate()` ends in
   `AutomaticDictionarySuggestionPolicy.shared.shouldShow(candidate)` →
   `DictionaryCorrectionOverlayController.shared.show(candidate:)`
   (AutomaticDictionaryCorrectionTracker.swift:676-691) — a floating overlay with
   Accept/Dismiss that auto-times-out after 5 s
   (AutomaticDictionaryCorrectionOverlay.swift:16-23,
   `displayDurationNanoseconds = 5_000_000_000`); a timeout counts as a dismissal.
2. **Threshold = 2 occurrences.** `struct DictionarySuggestionPolicyConfig`:
   `requiredOccurrences = 2`, `occurrenceWindow = 7 * 24 * 60 * 60`
   (AutomaticDictionaryCorrectionTracker.swift:233-235), gated in
   `shouldShow` at :291-309. Additional pacing upstream: `globalCooldown` 10 min,
   `dismissedPairCooldown` 7 days, `maximumPairDismissals = 3`,
   `maximumSessionIgnores = 3`, `retentionDuration` 30 days,
   `maximumStoredPairs = 200` (:236-241).
3. **Where surfaced:** the correction site (overlay over the edited app), with
   global Escape-to-dismiss wired into the cancel hotkey (ContentView.swift:3470,
   commit `ae9b71a "fix(dictionary): dismiss suggestions globally"`), plus a
   settings toggle "Auto-learn words while typing", **default true**
   (CustomDictionaryView.swift:363; SettingsStore.swift:3984
   `as? Bool ?? true`).
4. **Candidate shape:** each side ≤ 3 words (`maxWords = 3`, :25), ≤ 40 chars,
   combined ≤ 70 (:23-24); both sides ≥ 2 alphanumeric chars incl. ≥ 1 letter
   (`isMeaningfulCorrection`, :215-231); **case-only changes are excluded**
   (`heard.caseInsensitiveCompare(corrected) != .orderedSame`, :111); pairs whose
   trigger is already in the dictionary are skipped (`isAlreadySaved`, :684,
   :698-705, case-insensitive trigger match).
5. **Accept = merge without duplicating triggers:**
   `CustomDictionaryTrainingMerge.mergedEntries` (CustomDictionaryView.swift,
   `mergedEntries(current:replacement:triggers:)`, ~:2710-2786): find entries
   whose replacement matches case-insensitively, combine + normalize + dedupe
   triggers, keep the stored replacement text. On macOS accept additionally runs
   a "Train by Voice" pronunciation session (AutomaticDictionaryTrainingSession.swift:8-20).
6. **Signal source:** AX (Accessibility) observation of the focused field for
   30 s after insertion (`beginObservingInsertion`, tracker:~414ff). The Linux
   equivalent is AT-SPI — explicitly out of scope here; our signal is history edits.

## Decisions and deliberate divergences (record in docs/STATUS.md §"Intentional divergences")

| Decision | Upstream | Ours | Why |
|---|---|---|---|
| Signal | live AX field observation | `entry["edited_from"]` kept by `history.update_text` | AT-SPI out of scope; inline repair already centralizes edits |
| Surface | 5 s auto-dismissing overlay at the correction site | persistent Suggestions group in Settings (list is passive, user-paced) | no interruption ⇒ no cooldowns / session-ignore pacing needed; research §5 trust |
| Threshold | 2 occurrences in a 7-day window | **2 occurrences** (matches), no time window | counts derive from history itself; 5000-entry cap bounds them |
| Case-only edits | rejected (tracker:111) | **accepted** as case-suggestions ("miro board" → "Miro board" is the canonical dictionary case in config.py:66 and settings UI copy) | the port's headline dictionary use is a case fix |
| Dismiss | resurfaces after 7-day cooldown until dismissed 3× | permanent (pair never resuggested) | prompt requirement; simplest trust model |
| Accept | overlay + voice-training samples | button → dictionary merge only | Train-by-Voice is a separate upstream feature (still ⏳) |
| Config toggle | `automaticDictionaryLearningEnabled`, default on | **no new config key**, always on | upstream's toggle gates an interruptive overlay; a passive list that only records what the user already typed needs no gate; history.save=false already disables the whole signal (daemon.py:220 never appends) |

**Prompt ambiguity resolved:** the request lists "both sides identical after
case-fold" as a noise exclusion while also requiring the "miro board" → "Miro
board" case-only suggestion. Resolution: case-only differences are valid
candidates (case-suggestions). The exclusion applies to spans that are identical
after case-fold **and** stripping non-letters — i.e. pure punctuation/spacing
rewrites ("miro  board" → "miro board", "Miro, board" → "Miro board"), which
carry no dictionary lesson. This is encoded in the test table below.

## Design

### 1. Signal capture — `fluidvoice/history.py` (`update_text`, currently :159)

Inside the matched-entry branch:

```python
old = entry.get("text")
if old != text:
    entry["edited_from"] = old
entry["text"] = text
```

- edited_from = the text immediately before the most recent user edit
  (a re-edit overwrites it with the latest pre-edit text; the last correction
  is the signal).
- A no-op save (text unchanged) must NOT set edited_from.
- Reader tolerance (verified): `read_all`/`tail` `json.loads` pass arbitrary
  keys through; `_rewrite`/`update_text`/`export_zip` re-serialize whole entry
  dicts, so edited_from survives deletes, caps and export. Entries predating the
  feature simply lack the key — all consumers use `.get`.

### 2. Extraction — new `fluidvoice/processing/dict_learn.py`

```python
MIN_OCCURRENCES = 2
```

`extract_candidates(edited_from: str, text: str, fillers: list[str] | None = None) -> list[tuple[str, str]]`

- Tokenize both strings on whitespace; `difflib.SequenceMatcher(None, old, new,
  autojunk=False).get_opcodes()`.
- A candidate is one `replace` op `(old_tokens, new_tokens)` where:
  - `1 <= len(old_tokens) <= 3` and `1 <= len(new_tokens) <= 3` (an op that
    rewrites more than 3 tokens on either side is editing, not correcting —
    excluded by construction);
  - every token on both sides, after trimming edge punctuation (reuse the
    `unicodedata`-category approach from processing/fillers.py `_trim_punct`),
    is pure letters (`token.isalpha()`-equivalent via regex
    `[^\W\d_]+(?:['’\-][^\W\d_]+)*` — internal apostrophe/hyphen allowed);
    this rejects pure-punctuation and numeric spans ("q4", "20", ",");
  - no token on either side is in the filler list, case-folded (fillers default
    `config.DEFAULT_FILLERS`, honoring the user's `processing.filler_words`);
  - the sides differ verbatim (`old != new` — equal sides never produce a
    `replace` op, restated for clarity);
  - NOT noise-by-identity: if the sides are equal after case-fold **and**
    stripping every non-letter character, drop the span (pure punctuation /
    spacing rewrite).
- Case-only differences pass (case-suggestion) per the decision table.
- Return `(heard, corrected)` joined-token pairs, one per qualifying op;
  duplicate pairs within one entry collapse to one (caller counts per entry).

### 3. Suggestion store — JSON beside the config

New `paths.dictionary_suggestions_file() -> config_dir() / "dictionary-suggestions.json"`
(config_dir already handles XDG override + legacy migration).

Format (minimal — counts come from history, the store only records decisions):

```json
{"dismissed": [["miro board", "Miro board"]],
 "accepted":  [["flud voice", "fluid voice"]]}
```

- `load_store(path) -> dict` — missing/corrupt file ⇒ `{"dismissed": [], "accepted": []}`.
- `save_store(path, store)` — atomic write (tmp + replace, mirror
  history._atomic_write style; no secrets ⇒ no chmod needed).
- `dismiss(path_or_store, heard, corrected)` — append to `dismissed` (dedupe),
  save. Permanent: `pending_suggestions` never returns a dismissed pair.
- `record_accepted(store, heard, corrected)` — append to `accepted`, save.

### 4. Pending suggestions — same module

```python
def pending_suggestions(cfg: dict, entries: list[dict],
                        store: dict | None = None,
                        min_occurrences: int = MIN_OCCURRENCES) -> list[dict]
```

- For each entry with `edited_from`, extract candidates; count each distinct
  pair once per entry (an entry is one correction event).
- Skip a pair when: its trigger (case-folded) already appears among any
  dictionary entry's `triggers` (upstream `isAlreadySaved`), or it is in
  `dismissed`, or in `accepted`.
- Keep pairs with `count >= min_occurrences` (2, upstream's number).
- Return `[{"heard": …, "corrected": …, "count": n}]` sorted by count desc,
  then most-recent-entry ts desc.

### 5. Accept — dictionary merge without duplicate triggers

```python
def accept_merge(dictionary: list[dict], heard: str, corrected: str) -> list[dict]
```

Mirror of upstream `CustomDictionaryTrainingMerge.mergedEntries`, simplified:
- If an entry exists whose `replacement` equals `corrected` case-insensitively:
  append `heard` (as typed) to its triggers unless already present
  case-insensitively; keep the entry's existing replacement text.
- Else append `{"triggers": [heard], "replacement": corrected}`.
- Never mutates the input list; returns a new list. `apply_custom_dictionary`
  semantics untouched.

### 6. Client surface — `fluidvoice/gtkui/client.py` (no daemon/socket changes)

Following the existing "direct reads through shared modules" pattern
(cf. `history_update_text`, client.py:139):

- `dict_suggestions() -> list[dict]`:
  `dict_learn.pending_suggestions(load_config(), history_mod.read_all())`.
- `dict_suggestion_accept(heard: str, corrected: str) -> dict`:
  merge via `accept_merge` over the loaded config's `processing.dictionary`,
  then persist **through the existing validated save path**
  `self.set_config({"processing": {"dictionary": merged}})` (daemon live-apply,
  or file-only degraded mode — same as every other settings write), then
  `record_accepted`. Returns `{"ok": bool, "dictionary": merged, …}`.
- `dict_suggestion_dismiss(heard: str, corrected: str) -> None`:
  `dict_learn.dismiss(...)`.

### 7. Settings UI — `fluidvoice/gtkui/settings_window.py`

> The parakeet work may restructure this file; plan against then-HEAD. Today the
> dictionary editor is the "Custom dictionary" `Adw.PreferencesGroup` built in
> `_build_dictation` (settings_window.py:1082-1092) on the Dictation page, fed by
> `_load_dictionary`/`_collect_dictionary` (:908-958). Attach to wherever the
> dictionary group then lives, not to line numbers.

- New group `Adw.PreferencesGroup(title="Suggested words", description=
  "Corrections noticed in your history edits — accept to teach the dictionary")`
  placed immediately **below** the Custom dictionary group (hand-curated entries
  stay primary; the prompt allows above or below — pick below and stay there).
- **Hidden entirely when `dict_suggestions()` returns nothing** (never render an
  empty group; also tolerate ClientError by hiding).
- One `Adw.ActionRow` per suggestion: title `"{heard} → {corrected}"`,
  subtitle `"seen {count}×"`, suffix buttons:
  - Accept — `css_classes=["flat", "suggested-action"]`; calls
    `self.c.dict_suggestion_accept`; on ok: remove the row,
    `self._load_dictionary(resp["dictionary"])` (refresh the editor without
    discarding other unsaved edits — do NOT call full `_load()`),
    `self.toast("Added to dictionary")`.
  - Dismiss — `css_classes=["flat"]`; calls `self.c.dict_suggestion_dismiss`,
    removes the row, no toast.
- Group (re)built during `_load()` and after each accept/dismiss that empties it.

### 8. Doctor — `fluidvoice/doctor.py`

Add a testable helper next to `_formatting_lines`:

```python
def _suggestions_line(cfg: dict) -> str:
    from . import history, paths
    from .processing import dict_learn
    n = len(dict_learn.pending_suggestions(cfg, history.read_all()))
    return f"  dictionary suggestions: {n} pending ({paths.dictionary_suggestions_file()})"
```

Print it in `run()` right after the `history:` line (~doctor.py:120) under a
`print("dictionary learning:")` heading (one line, per the request). Read-only;
never fails doctor (wrap or rely on load_store's corrupt tolerance).

### 9. Config

**No new keys.** No whitelist/ALLOWED_SETTINGS/TEMPLATE changes. (Upstream's
toggle gates an interruptive overlay; ours is passive — decision table above.)

### 10. Docs

- `docs/UPSTREAM-TRACKING.md:169` — flip ⏳ → ✅, note: "suggest-only, from
  history edits (edited_from); see STATUS divergences".
- `docs/ROADMAP.md:86` — mark the Later item done.
- `docs/STATUS.md` — add to §"Text processing" (Done) and one row per divergence
  in §"Intentional divergences": signal source, Settings-list instead of overlay,
  case-only included, permanent dismiss, no config toggle.
- `README.md:285` area shows the dictionary config example — add one sentence:
  suggestions appear in Settings from your history edits. (Optional, low risk.)

## Files to touch

| File | Change |
|---|---|
| `fluidvoice/history.py` | `update_text` keeps `edited_from` (only when text changed) |
| `fluidvoice/paths.py` | `dictionary_suggestions_file()` |
| `fluidvoice/processing/dict_learn.py` | NEW: `MIN_OCCURRENCES`, `extract_candidates`, `load_store`/`save_store`, `dismiss`, `record_accepted`, `pending_suggestions`, `accept_merge` |
| `fluidvoice/gtkui/client.py` | `dict_suggestions`, `dict_suggestion_accept`, `dict_suggestion_dismiss` |
| `fluidvoice/gtkui/settings_window.py` | Suggested-words group + accept/dismiss handlers |
| `fluidvoice/doctor.py` | `_suggestions_line` + one printed line |
| `tests/test_history_audio.py` | `TestUpdateText` additions (edited_from kept / absent on no-op / overwritten on re-edit / round-trip incl. `_rewrite` and export) |
| `tests/test_dict_learn.py` | NEW — tables below |
| `tests/test_gtkui.py` | StubClient + 3 tests (rows render, accept posts+refreshes, dismiss hides) |
| `tests/test_infra.py` | doctor line test |
| `docs/UPSTREAM-TRACKING.md`, `docs/ROADMAP.md`, `docs/STATUS.md`, (README) | as above |

## Phases (each leaves the suite green)

1. **Capture**: history.py `edited_from` + test_history_audio.py additions.
2. **Extraction**: paths.py + dict_learn.py `extract_candidates` + test_dict_learn.py
   extraction tables (pure functions, no store yet).
3. **Store + suggestions + merge**: `load_store`/`save_store`/`dismiss`/
   `record_accepted`/`pending_suggestions`/`accept_merge` + their tests
   (threshold, per-entry counting, dictionary dedupe, dismiss permanence across
   a save/load round-trip, corrupt store).
4. **Surfacing**: client methods + settings group + test_gtkui.py.
5. **Doctor**: `_suggestions_line` + test_infra.py.
6. **Docs + full gate**: UPSTREAM-TRACKING/ROADMAP/STATUS(/README), then the full
   `.venv/bin/python -m pytest -q tests --ignore=tests/integration` run.

## Test tables

Extraction positives (`edited_from` → `text` ⇒ candidate):

| edited_from | text | candidate |
|---|---|---|
| "open the miro board app" | "open the Miro board app" | ("miro board", "Miro board") — case-only, canonical |
| "please send the flud report" | "please send the fluid report" | ("flud", "fluid") |
| "check gnu plot output" | "check gnuplot output" | ("gnu plot", "gnuplot") — 2→1 tokens |
| "its say it ermano again" | "its SayItErmano again" | ("say it ermano", "SayItErmano") — case-only 3 tokens |

Extraction negatives (no candidate):

| edited_from | text | reason |
|---|---|---|
| "hi , there" | "hi, there" | punctuation-only span |
| "meeting at 3 pm" | "meeting at 3PM" | numeric token ("3") |
| "so um yeah" | "so hmm yeah" | filler tokens both sides |
| "miro  board" | "miro board" | identical after case-fold + non-letter strip (spacing-only) |
| "Miro, board" | "Miro board" | punctuation/spacing-only |
| "i want to go now" | "we need to leave now" | 4-token rewrite (editing) |
| "fix it" | "fix the broken thing now" | new side 4 tokens |
| "same text here" | "same text here" | no change (no op) |

Behavior tests:

- Counting: pair in 2 entries ⇒ count 2; pair twice within one entry ⇒ count 1.
- Threshold: 1 occurrence ⇒ absent; 2 ⇒ present; `min_occurrences=1` override works.
- Dictionary dedupe: trigger "Miro Board" already in dictionary ⇒ pair with
  heard "miro board" not suggested (case-insensitive trigger match); a different
  heard for the same replacement still suggests.
- `accept_merge`: appends new entry; same-replacement entry gains the trigger
  once; duplicate trigger (case-insensitive) not added; existing replacement
  text preserved; input list not mutated.
- Accept end-to-end: `apply_custom_dictionary("open the miro board app",
  accept_merge([], "miro board", "Miro board")) == "open the Miro board app"`.
- Dismiss permanence: dismiss → `pending_suggestions` empty; after
  `save_store`+`load_store` (simulated restart) still empty; accepted pair also
  never resuggested.
- Store robustness: missing file, corrupt JSON ⇒ empty decisions, no raise.
- History: edited_from kept on change; absent on no-op save; overwritten on
  re-edit; survives `_rewrite` (delete of another entry) and `export_zip`
  round-trip; entries without the key parse unchanged.
- GTK (StubClient extended with the three methods): rows render with
  "seen N×" subtitle; group absent when no suggestions; Accept removes the row,
  posts through the client and refreshes `_dict_rows` (collected dictionary
  contains the new entry); Dismiss removes the row and records the pair.
- Infra: `_suggestions_line` contains the count and the file path for both the
  0-pending (no files) and N-pending (seeded history) cases.

## Out of scope (unchanged from the request)

n-best/beam vocabulary boosting, AT-SPI live observation, learning from
re-dictation, learning at insertion time, changes to `apply_custom_dictionary`
semantics, per-language dictionaries, Train-by-Voice pronunciation samples,
socket/daemon protocol changes.
