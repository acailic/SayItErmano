# Polish-quality baseline — local llama.cpp (2026-11-21)

First run of the polish-quality bench (issue #21 gate) with the bundled
local polish stack, on this repo's CI/dev machine (16-thread x86_64
CPU, no GPU, Debian 12). The cloud side of the gate needs a cloud key
and was not run here — this page records the local numbers, the
procedure, and the no-polish floor; the gate itself compares whatever
two providers you point it at on the same corpus.

Stack under test:

- chat model: `qwen3-4b-instruct-2507-q4_k_m` (Qwen3-4B-Instruct-2507,
  Q4_K_M, 2.50 GB, sha256-pinned)
- server: llama.cpp `b12606` official ubuntu-x64 build (digest-verified)
- flags: `--host 127.0.0.1 -c 4096 -t 8 -np 1 --no-webui`

Command:

```bash
python -m fluidvoice.evalharness polish --local --out polish-report
```

## Results (16-case corpus, margin 0.05)

| provider | similarity mean | content recall mean | exact | hard failures |
|---|---|---|---|---|
| no-polish (baseline) | 0.628 | 0.846 | 0/16 | 0 |
| local (llama.cpp qwen3-4b) | **0.866** | **0.874** | 6/16 | 0 |

Local polish clearly clears the only fixed floor we can define without
a cloud key — the raw-transcript floor: +0.24 similarity, +0.03 content
recall over no polish, no refusals, no empty outputs, and the flagship
case (the README's *"um lets meet on tuesday around 3 no wait 4 p.m."*)
comes back **character-exact**: *"Let's meet on Tuesday at 4 p.m."*

6/16 exact matches; representative non-exact outputs:

- `count-pages` → "The report came out to 312 pages, which is way more
  than we expected." — exact.
- `address` → "The new office is at 200 Market Street, Suite 410." —
  exact (number + address formatting).
- `call-back-time` → "Call me back around 4:45 at the latest." — same
  meaning as the reference's "a quarter to five"; scored as wording
  drift (similarity), not meaning loss. Reference-wording bias like
  this hits both gate sides equally, which is why the gate compares
  providers rather than pinning absolute scores.
- `date-correction` → "The date is the 15th of March." — correct
  resolution of the self-correction, but drops "Let's aim" (recall
  0.571). The content-recall metric catches exactly this class.

## Resource budget (measured, same machine)

| measure | value |
|---|---|
| disk (model + runtime) | 2.52 GB |
| cold start (spawn → /health) | 2.5–3 s |
| first polish after cold start | 7.7 s |
| warm polish (mean of 16 cases) | ~2 s |
| server RSS at ctx 4096 / 8 threads | 4.59 GB |
| toggle off / idle-stopped | 0 processes, 0 RSS |

## Running the full gate (local vs your cloud)

```bash
export GROQ_API_KEY=...   # or OPENAI_API_KEY / ZAI_API_KEY / ...
python -m fluidvoice.evalharness polish --local \
  --cloud-base-url https://api.groq.com/openai/v1 \
  --cloud-model llama-3.3-70b-versatile \
  --cloud-api-key-env GROQ_API_KEY --require-gate
```

PASS requires the local content-recall and similarity means to be
within 0.05 of the cloud baseline's, and zero local hard failures.
Artifacts: `polish-report.json` + `polish-report.md` in `--out`.
