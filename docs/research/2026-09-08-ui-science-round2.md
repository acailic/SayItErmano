# UI/Functionality science — round 2 (2026-09-08)

Companion to ui-hci-papers.md (2026-09-04, which covers latency,
waiting, motion, trust — mostly the PILL's presentation). This round
covers what we have BUILT SINCE and what is planned next: segmented
streaming preview, language cycling, hotwords, spoken-send countdown,
AI polish guards, and the planned n-best / AT-SPI / GAAV work. Each
finding is mapped to a concrete change in our codebase. Verification:
Google CHI'23, Larson & Mowatt and the Ma et al. survey were fetched
and summarized from the source pages; the rest are search-confirmed
against publisher pages (ACM/arXiv/MDPI/journals) as noted.

---

## 1. Live-preview stability: the tail flickers, and users feel it

**Finding (fetched).** A 123-participant CHI study quantified caption
"flicker" (luminance-DFT metric over consecutive frames) and found it
correlates with comfort, ease of reading, ease of following, fatigue
and impaired experience (all p < 0.001, Spearman ≈ 0.3). Stabilized
captions with smooth animation beat raw ASR rendering on five of six
measures. Updating already-displayed text impairs reading; semantic
re-writes of displayed text should be GATED (only when meaning truly
diverges) and new tokens should appear with fade/scroll, never hard
swap. Liu et al., CHI 2023 — "Modeling and Improving Text Stability in
Live Captions". https://research.google/blog/modeling-and-improving-text-stability-in-live-captions/ (paper: olwal.com/projects/research/text_stability/liu_text_stability_chi_2023.pdf)

**Finding (search-confirmed).** Partial-hypothesis reranking reduces
streaming flicker; hypothesis-stability is an explicit optimization
target in production streaming ASR. Bruguier et al. — "Flickering
Reduction with Partial Hypothesis Reranking for Streaming ASR" (Google,
ICASSP-adjacent). https://www.researchgate.net/publication/367481407

> **Implications for us.** Our segmented engine gets COMMITTED text
> right (monotone, never re-decoded) — that is exactly the CHI'23
> prescription. But the live TAIL is re-decoded every tick, so the last
> ~2 s of pill text rewrites itself constantly: that is our flicker.
> Three changes fall out:
> 1. **Render the tail as provisional** — dimmer ink or italic for the
>    uncommitted tail, full opacity on commit. Readers learn which words
>    are stable; the flicker becomes signal, not noise (renderer:
>    `PillRenderer._paint` already styles by state; a per-token alpha
>    pass over the tail substring is small).
> 2. **Smooth-append the tail** — fade/scroll new tail words in instead
>    of hard-swapping the string (same paint path).
> 3. **Measure it** — add `tail_rewrites=N` to the existing per-take
>    `preview stats:` log line (count tail emissions whose text differs
>    from the previous tail beyond a join-prefix). Zero UI risk, gives
>    us the CHI'23 metric's cheap cousin, and lets us A/B any
>    stabilization change.

## 2. Correction UX: alternates beat re-speaking; light lists + fallback win

**Finding (search-confirmed).** In the classic multimodal-dictation
study, choosing from an n-best ALTERNATES list outperformed re-speaking
the same words — re-speaking tends to reproduce the original
recognition error; unimodal repair is slower and less accurate than
multimodal. Suhm, Myers & Waibel — "Multimodal error correction for
speech user interfaces", TOCHI 8(4), 2001. https://dl.acm.org/doi/10.1145/371127.371166 (CMU PDF: cs.cmu.edu/~cpof/papers/suhm_tochi.pdf)

**Finding (fetched).** Users rated a lightweight alternates list the
most SATISFYING correction mechanism even though it was not the most
accurate; when the list missed the target word they smoothly fell back
to re-dictation; a redesigned interface (strong modes, push-to-talk,
"lighter weight alternates list easier to open and dismiss") stopped
users compounding error-on-error. Users spent more time correcting than
dictating new text. Larson & Mowatt — "Speech Error Correction: The
Story of the Alternates List", Int. J. Speech Technology 6(2), 2003.
https://www.microsoft.com/en-us/research/publication/speech-error-correction-the-story-of-the-alternates-list/

> **Implications for us.** Our planned n-best pick lists (roadmap,
> backend-blocked) now have a science-backed interaction design to aim
> at: a LIGHT list (few items, dismissable with one key), not a
> heavyweight chooser, with re-dictation as the natural fallback —
> which our flow already half-has (dictating over a bad insertion is
> just another take). History inline-edit is pure keyboard correction —
> the literature's slowest path — so treat it as the deep-edit sink,
> not the primary repair loop. Also: MIT's word-alternates work shows
> per-WORD alternates (tap the wrong word → 3 candidates) beat
> whole-utterance lists for usability (Harwath, Interspeech 2014,
> sls.csail.mit.edu). If faster-whisper ever exposes alternatives,
> design for per-word chips, not sentence re-picks.

## 3. LLM post-correction: big gains on weak models, small on strong — and over-correction is the failure mode

**Finding (fetched).** Survey + experiments on LLM ASR error
correction: N-best input beats 1-best; zero-shot GPT-4 over 10-best
ensembles two ASR systems (4.72 vs 6.90 WER). BUT: unconstrained LLM
correction risks OVER-CORRECTION — replacing correct words with
near-synonyms rather than homophones — plus truncation; constrained
decoding (map output back to the nearest hypothesis) fixes both. Gains
shrink to 1.4% relative when the ASR is already large-v2 Whisper.
Whisper N-bests are also low-diversity (mostly formatting variants).
Ma, Qian, Gales & Knill — "ASR Error Correction using Large Language
Models". https://arxiv.org/abs/2409.09554

> **Implications for us.** Two direct ones:
> 1. **Keep AI polish as a CLEANER, not a corrector.** Our base prompt
>    already frames it as cleanup; the science says correction-mode
>    LLMs swap correct words for synonyms. We should not chase
>    "LLM-corrects-Whisper" as a feature goal on large models.
> 2. **Add an over-correction guard** riding the D5/refusal seam
>    (`is_refusal`/`is_prompt_leak`): if the polished output's
>    word-level diff against the raw transcript exceeds a bound of
>    NON-PHONETIC substitutions (crudely: substitutions whose edit
>    distance > 2 and not numerically/punctuation-shaped), treat it
>    like a refusal — fall back to the raw transcript and notify.
>    That is the same constrained-decoding philosophy (map output back
>    toward the source) implemented as a cheap post-hoc guard, and it
>    directly kills the "polish rewrote my sentence" complaint class.

## 4. Hotwords: biasing helps rare words — but long lists poison the well

**Finding (search-confirmed).** Contextual biasing improves rare-word
accuracy, but large biasing lists DEGRADE overall accuracy and produce
false positives (bias words hallucinated into unbiased speech); only a
small, utterance-relevant subset of a long list is ever useful, and
adaptive/gated biasing exists precisely to mitigate this. "Enhancing
the Robustness of Contextual ASR to Varying Biasing List Sizes"
(arxiv.org/abs/2509.05908); Yolwas et al., Sci. Reports 2025
(nature.com/articles/s41598-025-12121-4); "Spot and Merge", Interspeech
2025; BWER as the rare-word metric (emergentmind.com/topics/biasing-
word-error-rates-bwers).

> **Implications for us.** `model.hotwords` (v0.7.0) allows 128
> entries — the science says that is where harm starts. Actions:
> (a) docs + doctor line should RECOMMEND ≤ 20 focused words and say
>    why (over-biasing hallucinates list words into normal speech);
> (b) v2: per-take adaptive selection — send only the hotwords that
>    plausibly belong to THIS take (recent history vocabulary, active
>    app profile, dictionary triggers) instead of the whole list;
> (c) measure the win: log a hotword-hit-rate per take (share of
>    configured hotwords appearing in the final transcript) so users
>    can see the list earning its keep — the BWER spirit at app scale.

## 5. Multilingual UX: cycling is right, per-app defaults are the next step

**Finding (search-confirmed).** Multilingual users report friction
constantly managing language settings and wish they could "use any
language as it comes to mind"; parallel-language support (multiple
active languages) is the direction voice products are pushed toward.
"Voice Assistants Have a Plurilingualism Problem", CUI 2022
(dl.acm.org/doi/10.1145/3543829.3544526); "I wish I could use any
language as it comes to mind", JASIST 2025 (asistdl.onlinelibrary.
wiley.com/doi/10.1002/asi.24964). Within-utterance code-switching
remains an open ASR problem (MDPI Appl. Sci. 12(19):9541).

> **Implications for us.** Our runtime cycle + whitelist guard is the
> honest 2026 answer for a Whisper-based stack — and the announce-badge
> on every switch matches the trust principle (visible language state).
> The science points at the NEXT increment: **per-app language
> defaults** (fold into the planned per-app behavior profiles: e.g.
> `apps.zed.language = "en"`, terminal stays `sl`) — that removes most
> cycle presses for the two-language daily driver. Within-sentence
> code-switching we should keep declining explicitly (model-hard), as
    upstream's users keep rediscovering.

## 6. GAAV / smart formatting: sentence-level beats token-level

**Finding (search-confirmed).** Sentence-level decoding of noisy input
— inferring spaces, capitalization and punctuation over the WHOLE
sequence rather than token-by-token — significantly reduced error rates
at equal speed (VelociTap, CHI 2015, dl.acm.org/doi/10.1145/2702123.
2702135); word-prediction studies show speed gains but not accuracy
gains and large individual differences (arxiv.org/html/2602.06489v1;
Heliyon 2024, sciencedirect.com/science/article/pii/S2405844024126849).

> **Implications for us.** When AT-SPI caret context lands, implement
> GAAV as a SINGLE reformat of the dictated span against its
> surrounding sentence (caret text before + dictated text + sentence
> end), not per-word capitalization fixes. Our current per-entry GAAV
> (lowercase-first, trailing-period strip) is token-shaped; the AT-SPI
> version should recompute caps/punct for the whole affected sentence —
> that is also exactly what upstream's #840 (misfiring first-word caps)
> gets wrong by acting token-locally.

---

## Ranked actions (highest leverage first)

1. **Provisional-tail rendering + smooth append** in the pill (§1) —
   visible reading-comfort win, contained renderer change, backed by a
   123-participant CHI study. Add `tail_rewrites` to preview stats.
2. **Over-correction guard** on AI polish (§3) — rides the existing
   refusal-guard seam; kills the "polish rewrote my sentence" class;
   constrained-correction philosophy, zero new deps.
3. **Hotwords guidance + hit-rate telemetry** (§4) — docs/doctor "≤ 20
   focused words", per-take hit-rate in the stats log; v2 adaptive
   selection once profiles exist.
4. **Per-app language defaults** (§5) — folds into per-app behavior
   profiles; removes most cycle presses.
5. **n-best interaction design spec** (§2) — when backends expose
   alternatives: per-word chips, ≤ 3 items, one-key dismiss, re-dictate
   fallback; require hypothesis DIVERSITY (sampling), not Whisper's
   formatting-variant n-bests (§3 finding).
6. **Sentence-level GAAV spec** (§6) — the design constraint for the
   AT-SPI implementation.

Not actionable now: within-utterance code-switching (model-hard);
LLM-as-corrector on large models (§3 says the ceiling is ~1%);
adaptive endpoint timing beyond our speak-to-cancel countdown (no
user-study backing found — left out on purpose).

## Verification notes

- Fetched and summarized from source pages: Google CHI'23 text
  stability (research.google blog), Larson & Mowatt 2003 (microsoft.com
  research page), Ma et al. LLM-EC survey (arXiv HTML).
- Search-confirmed only (snippet-level against publisher pages):
  Suhm/Myers/Waibel TOCHI 2001 (ACM DL snippet + CMU PDF link),
  Harwath Interspeech 2014 (MIT CSAIL PDF), biasing robustness set
  (arXiv/Sci.Reports/ISCA), CUI 2022 plurilingualism, JASIST 2025,
  VelociTap CHI 2015, word-suggestion studies.
- The flicker metric in §1 is adapted (we count text rewrites, not
  luminance DFT) — labeled as the "cheap cousin" on purpose.
