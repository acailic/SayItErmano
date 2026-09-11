# Real-speech corpus specification (phase 0)

- Date: 2026-09-12
- Status: **SPEC — approved for recording; no audio recorded yet**
- Origin: [product excellence plan](../research/2026-09-11-product-excellence-and-monetization-plan.md)
  ("Define so good it is an obvious purchase" → corpus paragraph, and
  phase 0 next-session item 4). This document specifies the corpus; it
  changes no code and records no audio.
- Companion docs: [eval README](README.md) (harness usage, private
  corpora), [manifest schema](MANIFEST-SCHEMA.md) (case fields)

## Why

The committed corpus is synthesized tones/silence. It proves the
harness math, not that dictation is good. This corpus gives the
project its first measured truth about real speech: WER/CER per
language and speaker, omissions, hallucinations on noise, guard
false positives/negatives, and speed — on the audience's actual
languages, accents, and microphones. Guard thresholds, vocabulary
hints, and defaults get tuned on the train split only; the held-out
split stays untouched until acceptance runs.

## Shape at a glance

| dimension | target | minimum |
|---|---|---|
| real speech utterances | 240 | 150 (ceiling 300) |
| speakers | 12–15 | 10 |
| negative (silence/noise) cases | 30 | 20 (NOT counted in the 150–300) |
| languages | 4–5 (§3) | English + 2 |
| mic classes per speaker | 2 (§5) | 1 |
| held-out | ≥20% of utterances, ≥2 whole speakers (§4) | — |

## 1. Consent and licensing

Every recording session starts with the moderator reading a short
brief and collecting a signed/dated consent form (paper or a signed
text file kept with the corpus, never in git). Sketch of the consent
text — adapt wording, keep the numbered substance:

> You are recording short spoken utterances to help evaluate and
> improve SayItErmano, a local speech-dictation application.
> 1. We record your voice saying everyday things: short commands,
>    messages, names, numbers, and technical phrases from prompts we
>    give you. Please avoid personal information you don't want in
>    a transcript.
> 2. Recordings and transcripts are stored locally on the project's
>    test machine, are NOT published or shared, and are not used to
>    train third-party or cloud services.
> 3. We may publish aggregate, anonymous results (error rates,
>    timings). We may later ask you separately to license specific
>    recordings for a public corpus; nothing is published without
>    that separate consent.
> 4. You can withdraw at any time; we delete your recordings and
>    transcripts (speaker id SNN lets us find them) on request.
> 5. Participation is unpaid (thank-you credit in private notes
>    only). The session takes about 45–60 minutes.
>
> Name, date, signature.

Licensing tiers: default tier is **private — do not redistribute**
(manifest `license` field says exactly that). A future public corpus
requires a separate CC0-1.0 or CC-BY-4.0 grant per recording, signed
at that time; nothing in this corpus is assumed redistributable.

Provenance metadata (per case, as extra manifest keys — the loader
tolerates unknown keys; see [MANIFEST-SCHEMA.md](MANIFEST-SCHEMA.md)):

| key | example | meaning |
|---|---|---|
| `speaker` | `"S07"` | pseudonymous id; roster lives outside git |
| `mic_class` | `"headset-usb"` | §5 class label |
| `mic_model` | `"Logitech H390"` | free text |
| `snr_db` | `14.2` | estimated post-hoc (§5), approximate |
| `environment` | `"home-office"` | §5 environment label |
| `taxonomy` | `"t3-jargon"` | §4 stratum id |
| `session_date` | `"2026-09-20"` | recording session date |

Speaker roster (L1, self-described accent, consent reference) is
kept in `eval-private/consent/speakers.toml` — outside git (see §6).

## 2. Where the audio lives — outside git

Following the plan ("Keep private audio outside git") and the
[harness §Private corpora](README.md#private-corpora) flow:

```
eval-private/                    # gitignored (already in .gitignore)
  corpus-v1/
    manifest.toml                # cases, as documented in MANIFEST-SCHEMA.md
    splits.toml                  # train/heldout assignment (§4)
    speakers.toml                # NON-identifying roster summary (id, L1,
                                 #   mic classes, dates) — contact data and
                                 #   signed consents stay OUTSIDE the repo,
                                 #   e.g. ~/.local/share/sayit-ermano-eval/
                                 #   consent/ (never committed)
    audio/                       # 16 kHz mono s16 WAV evaluation copies
    masters/                     # 48 kHz FLAC archive of raw captures
~/.local/share/sayit-ermano-eval/
  consent/                       # signed forms + contact info (never in repo)
```

Runs use `python -m fluidvoice.evalharness run --external
eval-private/corpus-v1/manifest.toml …` exactly as documented. Case
ids embed speaker and stratum (`S07-T3-ENCS-012`, uppercase; unique
across the merged corpus — the loader rejects duplicates).

## 3. Speakers and languages

Audience hypothesis (plan): Linux professionals who write messages,
documents, and developer prompts daily, value local processing —
multilingual, including non-native English. `KNOWN_LANGUAGES`
(config.py) lists 30 codes; the corpus does NOT cover all of them.
Stratify to the **chosen initial audience** (phase 1 picks it); the
default below stands until then and rebalances by one rule: *each
language the audience actually uses daily gets ≥3 speakers; add one
speaker per additional candidate language.*

Default stratification (12–15 speakers):

| stratum | speakers | notes |
|---|---|---|
| English L1 | 3 | mixed regional accents (e.g. US, UK, IE/AU) |
| English L2, technical | 4 | L1 ∈ {German, Slovenian, Russian, Spanish…}; daily English use; accented |
| Audience's other primary language | 3–4 | strongest candidate by discovery (e.g. `sl`, `de`); full core taxonomy in-language |
| Generality probe | 1–2 | one or two further languages from KNOWN_LANGUAGES |

Every non-English speaker also records English material (this is the
code-switching signal: same speaker, both languages). Diversity axes
that are NOT sampled for their own sake (per task): age spread within
working adults, gender balance roughly even, varied accents within
each language — but no child speech, no 4g-child-style breadth; the
audience is working Linux professionals. Accessibility-reliant
dictation users are recruited for the user tests (see
[user-test-script.md](../dev/user-test-script.md)), not required
here.

## 4. Utterance taxonomy and counts

Target 240 speech utterances (per-speaker load ≈ 16–20). Strata are
labeled per case (`taxonomy` key) so reports subgroup cleanly (§8).

| id | stratum | what | length | count | languages |
|---|---|---|---|---|---|
| T1 | short commands | app/OS commands, one-liners: "open the downloads folder", "commit and push" | ≤6 words | 40 | all |
| T2 | chat messages | informal replies, standups, emails: 1–3 sentences, contractions, disfluencies allowed | 5–20 s | 60 | all core |
| T3 | jargon & code-switching | identifiers, CLI flags, package names, tech nouns; intra-utterance language switches (e.g. English jargon inside a German sentence) | 5–15 s | 45 | all core + mixed |
| T4 | names & numbers | person/place names, filenames, IPs, versions, dates, prices, spelled acronyms | 3–10 s | 30 | all core |
| T5 | long-form | document paragraphs, 3–6 sentences, formal register | 30–120 s | 20 | en + 1 |
| T6 | developer prompts | LLM-style prompts describing a coding task, containing jargon, paths, code words | 10–30 s | 45 | en + 1 |
| — | **speech total** | | | **240** | |

T1–T4 are the "core" set replicated per language stratum; T5–T6 run
in English plus the audience's strongest other language. Prompt
sheets are cued-spontaneous: the speaker gets a task ("reply to your
colleague that the deploy is delayed to Friday") and speaks
naturally; only T4 items are read verbatim (precision items).
Prompts live in `eval-private/corpus-v1/prompts/` with the corpus.

Negative cases (separate, not counted in the 150–300; ~30 cases):

| id | case | expected |
|---|---|---|
| N1 | digital silence | 0.5 s, 2 s, 5 s — empty ref, guard flag |
| N2 | room tone per environment | per recording room — empty ref |
| N3 | noise-only | keyboard, fan, street, cafe at 2 levels — empty ref |
| N4 | noise + faint speech | speech ≥10 dB under noise — whatever survives, guard measured |
| N5 | non-speech voice | cough, throat-clear, "uhh…" only — empty ref |

Negative audio may come from the same sessions (capture lead-in/out
room tone deliberately) or be synthesized/collected separately; the
manifest marks `taxonomy = "negative-*"`.

## 5. Recording requirements

**Format.** Capture raw at the mic's native rate (≥44.1 kHz), archive
lossless FLAC as master, and derive the evaluation copy as **16 kHz
mono s16 WAV** — byte-compatible with what `pw-record` feeds the
pipeline in production (recorder.py default). The harness reads WAV
durations; masters are for re-derivation only.

**Mic classes** (each speaker records on ≥2; the class mix across the
corpus should be roughly even):

| class | examples | why |
|---|---|---|
| `laptop-array` | built-in laptop mics | most common first-use hardware |
| `headset-3.5mm` / `headset-usb` | gaming/office headsets | the enthusiast default |
| `usb-desk` | USB conference/desk mics | quiet-office alternative |
| `bluetooth-hfp` | BT headset (optional, small subset) | telephony narrowband; known capture risk |

**SNR targets.** Estimated post-hoc (noise floor from lead-in/out
silence vs speech RMS; the estimate is recorded, the harness doesn't
need it):

- ≥20 dB quiet room — ~50% of utterances
- 10–20 dB typical office — ~35%
- 5–10 dB noisy (fan, keyboard, cafe) — ~15%, concentrated in T1/T2

**Environment notes.** Home office (primary), shared/open office,
public (cafe) for the noisy subset; one environment per session,
labeled. Speakers sit as they normally would; no pop filter theater.

**Session hygiene.** 45–60 min max per speaker; prompt sheets in
advance; each take starts and ends with ≥1 s of silence (lead-in for
SNR estimation and first-word-clipping analysis); retakes allowed,
final selection recorded in the manifest (one file per utterance).

## 6. Held-out split rule

1. Assignment is deterministic: utterances are grouped by
   (speaker, taxonomy); within each group, sorted by id, every 5th
   case goes to `heldout` (target ≥20% overall). The assignment is
   written once to `eval-private/corpus-v1/splits.toml` and never
   edited afterwards.
2. At least **2 whole speakers** (one non-English L1, one English)
   are assigned entirely to `heldout`, chosen to preserve the
   language/mic/taxonomy strata — testing speaker generalization,
   not just utterance generalization.
3. **The held-out split is never used to tune anything**: guard
   thresholds, vocabulary/hotword hints, per-language defaults,
   prompt wording, model choice. Tuning of any of those happens on
   `train` only; runs against `heldout` are acceptance/report runs
   and are labeled as such.
4. Reports always state which split they cover; never merge splits in
   a headline number.

## 7. Evalharness gap analysis — adapters needed (NOT implemented here)

What exists today (fluidvoice/evalharness/): WER + CER with pinned
normalization (metrics.py `wer`/`cer`), hotword recall over case tags,
real-time factor defined as **audio duration / processing time —
higher is faster** (metrics.py `real_time_factor`, direction restated
in every report), first-preview and final latency (transcriber
reported), guard confusion TP/TN/FP/FN (metrics.py
`guard_category`/`guard_counts`), private external manifests, soak
summarization, per-case JSON + Markdown reports.

Missing — the list to implement when the corpus lands (design only
here, per phase 0 rules):

| # | gap | detail | plan hook |
|---|---|---|---|
| G1 | **subgroup aggregation** | `_summarize`/report produce corpus-wide means only; need group-by (language, `speaker`, `mic_class`, `taxonomy`, SNR bucket) tables in report.md/json | "report subgroup results… not an overall average" |
| G2 | **omission rate** | deletions only (reference words absent from the hypothesis) / reference words; needs an alignment-returning Levenshtein, reported alongside WER | "omissions" listed separately |
| G3 | **hallucination word rate** | on negative cases (N1–N5): hypothesis token count per audio second with empty reference; today only binary guard ok/flag exists | "hallucinations", silence/noise cases |
| G4 | **punctuation/meaning errors** | normalization strips punctuation by design, so punctuation is currently unmeasurable; needs a punctuation-preserving comparison (terminal punctuation, sentence-start capitals) as a separate metric family that never feeds WER | "punctuation/meaning errors" |
| G5 | **language confusion** | manifest has `language` per case but no detected-vs-expected comparison; transcriber adapters should report detected language (raw key) and the harness should emit a language confusion table | wrong-language guard |
| G6 | **guard-score sweep support** | threshold tuning needs per-case guard scores (not just ok/flag) preserved under `raw` and summarized per split; convention + summary, train-only | "held-out set never used to tune guards" |
| G7 | **name/number formatting recall** | partially covered by hotword tags; adopt the convention that every T4 case tags its name/number tokens so recall is comparable across languages | "vocabulary recall" |

Also explicit non-goals for the harness: end-to-end
stop-to-insertion latency (measured in the smoke matrices and user
tests, not here — the harness measures decode-side latency only).

## 8. Report format for results

Every evaluation run over this corpus produces (existing `run`
machinery + G1):

- Header: date, harness version, app version/commit, model backend +
  version + settings (language pin, guard config), split identity
  (`train` / `heldout` / full), manifests used.
- Summary block with the RTF direction note, as today.
- **Subgroup tables, mandatory**: per language, per taxonomy, per
  mic_class, per SNR bucket, per speaker — each with case count,
  WER/CER mean, omission rate, hotword recall, RTF, latencies, and
  guard FP/FN counts. No aggregate-only reporting anywhere.
- Negative-case table: per N-case hypothesis token count, guard
  outcome; hallucination word rate summarized per noise type.
- Worst-case list: the 10 highest-WER cases with id, speaker,
  language, taxonomy — failures are named, not averaged away.
- Case-level detail remains in report.json as today.

Baseline runs (first measurement) are attached to the plan's phase 1
deliverable; subsequent runs attach deltas to the previous release's
report and to the release SHA (release-gates policy).
