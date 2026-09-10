# Evaluation manifest schema (plan P1.4)

A manifest is a TOML file (repo convention: config is TOML) named
`manifest.toml` (or passed explicitly) with one `[[cases]]` table per
case. Loader: `fluidvoice.evalharness.manifest.load_manifest` /
`load_manifests`; CLI: `check` validates without running anything.

## Fields

| field | type | required | meaning |
|---|---|---|---|
| `id` | non-empty string | yes | stable case identifier; unique across the merged corpus |
| `audio` | path (string) | yes | audio file, resolved relative to the manifest's directory |
| `reference_text` | string | yes | ground-truth transcript (empty string allowed, e.g. silence cases) |
| `language` | string | yes | language code (BCP-47 / whisper style, e.g. `en`, `sl`) |
| `tags` | list of non-empty strings | yes | doubles as the case's hotword vocabulary — hotword recall scores the tags the reference contains; may be empty |
| `expected_guard` | `ok` \| `flag` \| `none` | yes | expected hallucination-guard outcome; `none` = case is excluded from guard scoring |
| `license` | non-empty string | yes | license of the *audio* (e.g. `CC0-1.0`) — must be redistributable for committed manifests |
| `source` | non-empty string | yes | provenance (who/what produced the audio) |
| `synth` | table | no | regenerable fixture recipe (see below) |

Unknown extra keys are tolerated (private corpora evolve ahead of this
schema); required keys must be present and well-typed. Validation
errors name the manifest path, the case index and the `id`:

```text
manifest.toml: cases[2] (id 'tone-en-basic'): missing or empty required
field 'license' (None)
```

## The `synth` recipe (optional)

| key | type | default | meaning |
|---|---|---|---|
| `kind` | `tone` \| `chirp` \| `silence` | — | 440 Hz sine / 300→1200 Hz sweep / digital silence |
| `seconds` | number > 0 | — | clip length |
| `rate` | int > 0 | `16000` | sample rate (16-bit PCM mono) |

When a case's `audio` file is missing and it has a `synth` recipe, the
harness generates the wav deterministically (same recipe → identical
bytes; `fixtures` subcommand or any `run` materializes it). Missing
audio with no recipe is a hard error — the harness never invents audio.

## Example: committed synthetic case

```toml
[[cases]]
id = "tone-en-basic"
audio = "tone-en-basic.wav"
reference_text = "hello world"
language = "en"
tags = ["hello"]
expected_guard = "ok"
license = "CC0-1.0"
source = "synthetic (fluidvoice.evalharness.synth; no recorded speech)"
synth = { kind = "tone", seconds = 1.0 }
```

## Example: private corpus case (never committed)

```toml
# eval-private/manifest.toml  —  gitignored
[[cases]]
id = "real-speech-de-001"
audio = "clips/2026-09-10-utterance-001.wav"
reference_text = "Guten Morgen, wie spät ist es?"
language = "de"
tags = ["spät"]
expected_guard = "ok"
license = "private — do not redistribute"
source = "recorded by <you>, 2026-09-10"
```

Merge it with `--external eval-private/manifest.toml`; duplicate `id`s
across manifests are rejected. Real-speech evaluation is the manual
representative-model step described in
[README.md](README.md) §Plugging a real backend and §Release policy.
