# SayItErmano: product excellence first, sustainable sales second

- Date: 2026-09-11
- Status: PLANNED — investigation and execution plan, not a completed audit
- Code baseline inspected: `68ef282` on `linux`
- Owner: project maintainer; implementation work to be assigned after investigation

## Objective and scope

Make SayItErmano so useful, dependable, and easy to adopt that a one-time
purchase feels like an obvious choice for its intended users. Earn that
position by demonstrating less effort and time spent producing usable text.
Feature count, test count, and resemblance to another app are supporting
evidence, not the product's success measure.

This document plans a whole-project investigation, the improvements that
follow from it, and subsequent monetization research. The preliminary review
covered repository history, existing plans, release policy, packaging,
evaluation documentation, and selected application code. It did not run the
application, benchmark real speech, complete a security audit, interview
customers, or verify release artifacts. Proposed targets below are hypotheses
to calibrate against a measured baseline, not current performance claims.

Working audience hypothesis: Linux professionals who write messages,
documents, and developer prompts daily, value local speech processing, and
dislike recurring fees. Include multilingual users and people who rely on
dictation for accessibility in discovery. Choose one initial buying audience
from evidence; do not promise every language, desktop, and workflow at launch.

## What already exists, and what must be verified

| Observed foundation | Implication for this plan | Repository evidence |
|---|---|---|
| Local and remote speech backends, preview, AI polish, vocabulary, history, rewrite, command mode, and native GTK settings already exist. | Improve the complete daily experience; do not restart feature development from an old checklist. | [README](../../README.md), [status ledger](../STATUS.md), [request briefs](../../requests/) |
| Typed backend contracts, the configuration registry, and daemon coordinators are present. | Review their contracts and failure handling; further restructuring needs a demonstrated benefit. | [backend types](../../fluidvoice/backends/base.py), [config](../../fluidvoice/config.py), [capture](../../fluidvoice/capture.py), [engine manager](../../fluidvoice/engine_manager.py), [command coordinator](../../fluidvoice/command_coord.py) |
| Context providers and per-app profiles landed in `b1e9011`; chunked file transcription is marked SHIPPED and landed before this baseline. | Reconcile the older roadmap with commits before creating work. Validate these capabilities rather than scheduling their original implementation again. | [context design](../dev/context-seam.md), [chunking brief](../../requests/chunked-file-transcription.md), `git log` |
| Required live GNOME Wayland/sway/context matrices are explicitly NOT DONE. | Desktop compatibility is a release-evidence gap. Do not infer parity from mocked tests. | [live smoke matrix](../dev/wayland-smoke-matrix.md) |
| The evaluation harness measures errors and latency, but its bundled corpus is synthesized tones/silence, not recorded speech. | Build representative speech evidence using the existing harness. | [evaluation guide](../eval/README.md) |
| Onboarding already checks devices/model/hotkeys and offers a three-second tryout that does not insert into another app. | Test the transition from successful tryout to successful real-world insertion. | [onboarding](../../fluidvoice/gtkui/onboarding.py) |
| Manual release gates and a narrow Ubuntu deb compatibility contract exist. | Exercise and prove installation, upgrades, and release reproducibility under that contract. | [release gates](../dev/release-gates.md), [deb contract](../../packaging/deb/README.md) |
| The project declares GPL-3.0-or-later and credits reused upstream prompts and sounds. | Commercial packaging needs a rights inventory and a GPL-compatible offer. | [project metadata](../../pyproject.toml), [license](../../LICENSE), [port provenance](../../README.md) |

The [existing reliability program](2026-09-10-reliability-first-improvement-program.md)
remains technical context. This plan adds customer outcomes, measured quality,
and commercial validation. Its first deliverable reconciles that program and
the [roadmap](../ROADMAP.md) with current code; neither old plans nor this
preliminary inspection should be treated as a fresh defect list.

## Sequence and decision gates

Indicative effort assumes one implementer with access to test machines and
volunteer testers. These are planning ranges, not delivery commitments.
Calendar delays for recruitment and desktop access are separate.

| Phase | Work | Deliverable and exit gate | Initial effort |
|---|---|---|---|
| 0. Establish truth | Reconcile code, shipped requests, docs, releases, and known issues. Run the existing offline baseline in isolation. Map the complete user journey and architecture. | Dated baseline, capability/support matrix, and ranked evidence ledger; every finding distinguishes reproduced defect, unverified risk, and idea. | 1–2 days |
| 1. Investigate the whole product | Audit the areas below; observe 8–12 target users attempting realistic tasks; build and run a representative speech/application benchmark. | Reproduction-backed audit, setup funnel, accuracy/latency baseline, top five user obstacles, and chosen initial audience. | 3–5 engineering days plus recruitment |
| 2. Make the core dependable | Fix the highest-impact capture, transcription, insertion, recovery, and installation failures. Verify supported desktop combinations. | Critical failures resolved; agreed reliability gates met; each fix has proportional regression coverage. | 1–2 weeks, revise after audit |
| 3. Make it feel worth paying for | Improve setup, sensible defaults, correction, per-app behavior, accessibility, and everyday polish based on observed friction. | New-user and repeated-use trials meet the usefulness gates below. | 1–2 weeks, revise after audit |
| 4. Validate purchase economics | Complete competitor, customer, licensing, packaging, payment-provider, and support-cost research. Test concrete offers with users. | Written commercial decision: audience, differentiated value, offer, price range, costs, rights constraints, and reasons to proceed or stop. | 3–5 days after quality evidence |
| 5. Prepare a paid release | Implement only the validated purchase/delivery model; rehearse installation, payment recovery, refunds, updates, and support. | Small paid pilot passes product and commercial gates before broader launch. | Estimate after phase 4 |

Light competitor and rights research can inform phases 0–1. Billing work and
paid launch wait for product evidence. At each phase boundary, update the plan
with actual results, adjust scope, and archive hypotheses that did not hold.

## Whole-project investigation checklist

Every area produces findings with severity, user impact, reproduction or
measurement, source file/commit, confidence, proposed fix, effort, and acceptance
criteria. Maintain one prioritized backlog; audit completeness does not require
changing every module.

| Area | Questions and experiments | Starting points |
|---|---|---|
| First use and installation | Can a new user install, select a working mic/language/model, bind a key, and put text into their own editor without developer help? Test clean install, slow/interrupted model downloads, permissions, disk exhaustion, and CPU-only machines. | `packaging/`, `scripts/install*.sh`, `gtkui/onboarding.py`, `doctor.py`, `model_download.py` |
| Capture and lifecycle | Test missing/Bluetooth/unplugged microphones, silence/noise, first-word clipping, rapid start/stop/cancel, suspend/resume, lock/unlock, model changes, long takes, and idle unloading. | `capture.py`, `recorder.py`, `micmon.py`, `hotkey.py`, `runtime_tasks.py`, `engine_manager.py` |
| Speech quality and speed | Compare viable models on real accents, target languages, code-switching, names, numbers, jargon, pauses, and background noise. Measure false hallucinations and valid speech incorrectly suppressed by guards. | `backends/`, `pipeline.py`, `preview.py`, `evalharness/`, existing learning-session scripts |
| Insertion and recovery | Test terminals, browsers, chat, editors, Electron and GTK apps across supported sessions. Exercise focus changes, clipboard managers, selection replacement, Unicode, paste timeout/duplication, spoken-send, and failed insertion recovery. | `insertion.py`, `selection.py`, `context/`, `profiles.py`, existing smoke matrices |
| Correction and daily workflows | Time dictation plus correction against typing and an alternative tool. Test vocabulary suggestions, undo/recovery, punctuation, rewrite accuracy, per-app settings, and finding/reusing a transcript. | `processing/`, `rewrite.py`, `history.py`, `gtkui/` |
| UI and accessibility | Observe status comprehension, confusing settings, keyboard-only use, focus order, screen-reader labels, scaling, contrast, reduced motion, and errors that tell users how to recover. | `gtkui/`, `overlay.py`, `tray.py`, screenshots as historical references only |
| Privacy and security | Trace mic audio, retained recordings, surrounding text, clipboard, API keys, logs, exports, remote STT, AI requests, command execution, socket/MCP access, and updates. Verify network behavior in local mode and disclosure when cloud processing is selected. | `paths.py`, `config.py`, `history.py`, `command.py`, `control_server.py`, `mcp_server.py`, `update.py` |
| Architecture and maintenance | Trace ownership, cancellation, configuration changes, backend capabilities, UI/daemon protocol boundaries, and duplicated policy. Sample large modules for concrete change hazards; do not use file length alone as a refactor justification. | `daemon.py`, coordinators, `config.py`, contracts and tests |
| Testing and distribution | Identify mocked-only paths, flaky tests, missing real-model coverage, package integrity checks, release provenance, upgrade migration, rollback, uninstall, and supportable distro boundaries. | `tests/`, `.github/workflows/`, `justfile`, `packaging/`, release policy |
| Positioning and support | Can users explain the benefit after one session? Are promises supported by the current compatibility evidence? Which recurring support requests could the app solve itself? | README, website/demo if present, comparison docs, issues, onboarding and doctor output |

Cover CLI, file transcription, and MCP workflows as part of the audit, while
prioritizing the core dictation journey. Candidate features such as diarization,
new model variants, or more platforms enter implementation only when evidence
shows they solve a higher-value problem than remaining core friction.

## Define “so good it is an obvious purchase”

Use these provisional gates to make that ambition testable. Agree on the
hardware, model, language, desktop, application, and task set before measuring;
report subgroup results and failures rather than hiding them in an overall
average. Fix targets before the acceptance run.

| Outcome | Proposed acceptance evidence |
|---|---|
| First useful result | At least 8 of 10 new target users complete setup and insert useful text into a real app without intervention. Target median ≤5 minutes once the required model is available; report model download and total elapsed time separately. |
| Dependable insertion | At least 99% successful insertion across ≥500 scripted/manual supported-matrix attempts. Zero observed wrong-window insertions, unintended command/send actions, or irrecoverable transcript loss; any such event blocks launch. A zero count is test evidence, not a guarantee. |
| Responsive dictation | On the named recommended hardware/model, initial target p95 stop-to-insertion ≤2 seconds for 5–15 second takes with local STT and AI polish off. Separately measure cold start, preview, longer takes, and optional AI/network time. |
| Less work to produce correct text | On matched tasks, target ≥30% lower median completion time including corrections than the participant's normal typing workflow, without reducing final accuracy. Counterbalance task order and record where dictation is worse. |
| Trustworthy speech | Publish WER/CER, vocabulary recall, omissions, hallucinations, guard false positives/negatives, and punctuation/meaning errors separately. Establish language-specific thresholds after baseline; aggregate WER alone is insufficient. |
| Useful enough to keep | In a two-week pilot, target at least 7 of 10 target users voluntarily using it on ≥4 days in week two; at least 5 can identify a repeatable time-saving workflow. Use interviews or voluntarily shared local summaries, preserving the no-telemetry policy. |
| Recoverable failures | Every tested capture/decode/polish/insertion failure ends with a clear state and a recovery action. Text is retained where the chosen privacy policy allows; clipboard and cancellation behavior pass the matrix. |
| Sustainable operation | Existing offline and release gates pass; real-model evaluation and desktop checks are attached to the release SHA. Follow the existing two-hour major-release soak and 24-hour lifecycle-change soak policy. |

Build a first consented/licensed corpus of roughly 150–300 real utterances
across 10–15 speakers, including a held-out set never used to tune guards.
Stratify by the selected audience's languages and hardware; add silence/noise
cases separately. Record backend/version/settings and audio provenance.
Keep private audio outside git. Reuse the existing harness, adding only missing
measurement adapters. Report its speed ratio explicitly: it currently defines
real-time factor as audio duration / processing time, with higher being faster.

Small pilot counts guide decisions; they do not establish market-wide demand
or statistically prove reliability. Expand the sample before broad claims.

## Improvement bets to validate first

1. **A complete first-use path:** extend the existing setup/tryout into a
   verified real-app success, with model recommendations grounded in the
   machine and language, clear download progress, and actionable permission
   guidance. Measure abandonment before adding more settings.
2. **Text that arrives safely:** finish supported desktop validation and
   address any reproduced clipping, wrong-language, duplication, focus,
   clipboard, and recovery failures. Preserve terminal and command safeguards.
3. **Fast correction that improves tomorrow:** make existing history,
   dictionary suggestions, vocabulary hints, and per-app profiles easy to
   discover and control. Measure whether corrections actually decrease;
   additions must not silently learn unintended replacements.
4. **Defaults for everyday work:** validate a small set of message, document,
   and developer-prompt workflows, including multilingual speech. Prioritize
   predictable formatting and optional AI meaning preservation.
5. **A product users can maintain:** clear updates, reliable installation,
   useful diagnostics with private content redacted, recovery instructions,
   and an honest supported-platform promise. Measure support burden.

Rank findings by frequency × severity × reach × evidence confidence, weighed
against effort. Safety/data-loss defects take precedence regardless of score.
Deliver small changes with a before/after observation; do not batch an
unrelated architecture rewrite into UX improvements.

## Monetization research and one-time purchase design

The initial evidence is kept in the companion
[commercial-model research note](2026-09-11-commercial-model-evidence.md).
It is a starting point, not a price recommendation or proof of demand.

Research four questions after the usefulness baseline:

1. **Who pays, and for what?** Interview the initial audience about their
   existing workflow, failed alternatives, time saved, privacy requirements,
   setup tolerance, and past software purchases. Compare live trials with
   Linux-native alternatives and commercial dictation products. Refresh the
   existing comparison from official sources and hands-on tests; distinguish
   advertised features from tested behavior and non-Linux products.
2. **What can be sold under the project's rights?** Inventory original and
   upstream code, prompts, sounds, branding, dependencies, model weights, and
   redistribution terms. GPL software can be sold, but recipient freedoms and
   source obligations shape the offer. Do not assume a proprietary fork,
   restrictive end-user license, or dual licensing is available for inherited
   work. Resolve the actual rights and distribution design before implementation.
3. **Does a one-time purchase cover its costs?** Model three price points
   chosen from interviews and competitor evidence, with low/base/high sales,
   refunds, support minutes, processor fees, distribution costs, taxes handled
   by the seller/provider, and a funded maintenance reserve. Calculate net
   contribution per sale and break-even volume; chargeback/fraud costs and
   unpaid support time count. Separate one-off development from recurring costs.
4. **How does buying work?** Compare a hosted merchant-of-record checkout and
   a direct processor using current official documentation. Verify the actual
   seller's country/entity eligibility rather than inferring it from timezone.
   Research receipts, tax handling, refunds, payouts, file delivery, purchase
   recovery, signed entitlements where useful, privacy, and accounting exports.

### Preferred hypothesis to test

Sell a **one-time official desktop package** whose value includes a tested,
convenient installation, clear compatibility guarantees, and a defined support
and maintenance period. The acquired version remains usable indefinitely;
future major-version upgrades can be optional paid purchases. Specify what
updates buyers receive and for how long. Keep any hosted inference or managed
service separately priced or bring-your-own-key so unlimited cloud costs are
not financed by a single desktop payment.

Compare this with a free community build plus paid installation/support bundle,
and a one-time purchase with a fixed update window. Evaluate whether customers
will pay for official distribution and convenience when source and
redistribution rights remain available. Source openness is a business-design
constraint; a removable activation check cannot be the main value proposition.

For any purchase entitlement, define offline use, machine replacement,
reinstallation, lost receipts, version eligibility, refund handling, and what
happens if the vendor stops operating. Do not require a licensing outage to
disable existing local dictation. Avoid promises of lifetime human support,
lifetime all-version updates, or unlimited hosted AI unless the economics
explicitly justify them.

### Commercial decision gate

Before building checkout, produce a concrete offer sheet containing:

- Target user and measured advantage over their current option.
- Included software/service, free-versus-paid boundary, license obligations,
  supported platforms, perpetual-use scope, update term, support term, and
  optional upgrade policy in ordinary language.
- Candidate price/currency, net contribution assumptions, break-even volume,
  support capacity, and sensitivity to refunds and weak repeat sales.
- Verified provider eligibility and a purchase/delivery/recovery design.
- Evidence from at least 10 qualified offer conversations; distinguish stated
  intent from actual purchases. Then target 5–10 paid pilot customers before
  broad launch, with real fulfillment and stated refund terms.

Continue only if product gates pass, buyers understand the offer, license
obligations are met, and conservative economics work. If users love the free
product but will not pay for the proposed bundle, revise the offer or pursue
support/team services; do not manufacture friction in core dictation.

## Execution rules and immediate next session

This saved plan does not implement features, publish a release, contact users,
change licensing, or open a payment account. Those are later execution tasks.
Preserve Linux-only, no telemetry, and no local TCP server decisions unless
explicitly reconsidered by the maintainer.

Follow [AGENTS.md](../../AGENTS.md): one worktree per active agent, preserve
others' WIP, check SHIPPED request status and history, and use the shared venv
from the worktree root. The future offline baseline command is:

```bash
/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python -m pytest -q tests --ignore=tests/integration
```

The next session should execute **phase 0 only**, then begin phase 1 against
that baseline:

1. Rebase the investigation context on the current `linux` SHA and record
   what changed since `68ef282`; reconcile the roadmap with shipped work.
2. Run existing offline checks and save failures/skips/warnings with environment
   details. Do not present historical README test counts as a current run.
3. Map the take lifecycle and install-to-first-insertion journey; create the
   finding ledger and mark known live-test gaps as unverified.
4. Draft the real-speech corpus specification, app/desktop matrix, and a short
   moderated user-test script. Select the initial audience and arrange test
   access before depending on it.
5. Return the five highest-impact findings with evidence, proposed changes,
   and acceptance checks. Turn only selected work into OPEN request briefs.

Suggested future artifacts in `docs/research/`: a baseline/audit report, a
speech-and-workflow benchmark report, a user-test synthesis, and a commercial
decision. Link those reports from this plan as phases complete. Update the
status ledger and roadmap only when supporting evidence changes.
