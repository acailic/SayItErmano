# Moderated user-test script (phase 1)

- Date: 2026-09-12
- Status: **SCRIPT — drafted in phase 0; not yet run**
- Origin: [product excellence plan](../research/2026-09-11-product-excellence-and-monetization-plan.md)
  phase 1 ("observe 8–12 target users attempting realistic tasks") and
  the usefulness gates ("First useful result", "Less work to produce
  correct text" ≥30 %, "Counterbalance task order").
- One moderator, one participant, **30 minutes**. Sessions are
  in-person on the participant's own laptop where possible (their
  mic, their environment); otherwise on a clean test laptop with
  their mic class reproduced.

## Recruitment (screener)

Target: **8–12 participants** matching the audience hypothesis
(Linux professionals who write daily; multilingual; privacy-aware;
include at least 2 who rely on or strongly prefer dictation for
accessibility, and at least 3 non-native English speakers).

Screener questions (phone/chat, ~5 min; record answers verbatim):

1. Which Linux distro do you use daily, and do you know if your
   session is Wayland or X11? (daily Linux = include)
2. On a typical workday, what do you write with the keyboard —
   messages/chat, documents/email, or prompts/code notes? Which is
   the heaviest? (writes daily = include)
3. Have you used dictation or speech-to-text before? Which tools,
   and what made you keep or drop them?
4. When a tool says "your voice is processed locally, nothing leaves
   your machine" — does that matter to you? Why? (attitude record,
   no wrong answer)
5. What microphone do you normally have available — laptop built-in,
   headset, USB/desktop mic?
6. Which languages do you speak/type in daily?
7. Would you be comfortable thinking aloud / being observed while
   working for 30 minutes? (consent preview)

Exclude: professional speech transcriptionists (not the audience);
no daily writing; cannot attend with their daily hardware.

## Session structure (time budget)

| part | minutes | what |
|---|---|---|
| 0. Intro + consent | 3 | purpose, privacy note, recording consent |
| 1. Setup observation | 8 | install→first insertion, NO help unless stuck |
| 2. Tasks A–C + matched typing pair | 14 | dictation tasks, correction behavior |
| 3. Post-test interview | 5 | value, one-time-purchase reaction, replacement |

## Part 0 — Intro and consent (3 min)

Read aloud: "This is a research session about a Linux dictation app
in development. There are no wrong ways to use it; I want to see
where it's confusing. Everything stays local — the app has no
telemetry; my notes and any recording stay on my machine and are
used only to improve the product. You can stop or skip anything at
any time." Ask consent for: observation notes (always), screen+audio
recording (separate opt-in; if declined, take notes only).

Give the participant: the project homepage/README or the .deb file —
**whatever a real new user would find first** — and then say:

> "Get the app working so you can dictate text into something you
> actually use. Take as long as you need; I won't help unless you
> ask me to or you're truly stuck. Please say what you're thinking
> as you go."

## Part 1 — Setup observation (8 min, extends past 8 only if they're still progressing)

**Rule: no help unless the participant is stuck** (no progress for
~60 s on the same step, or asks to stop). Every intervention is an
abandon-point finding: log the step, the elapsed time, what they
tried, what the app showed them, what finally unblocked them (or
that they gave up — a give-up is a completed data point, end the
part, note the blocker, fix forward in the app, don't rescue the
session).

Timing instrumentation (start a stopwatch at hand-off):

| clock | point | notes |
|---|---|---|
| T0 | hand-off | participant has installer/README in front of them |
| T1 | package installed | app opens/launches |
| T2 | model downloaded | model ready (record download MB + connection type separately — slow installs are their own finding) |
| T3 | **first successful insertion** | dictated text visibly lands in THEIR chosen app |

Record T0→T3 total (the plan's "first useful result" gate: ≥8/10
complete unaided, median ≤5 min **once the model is available** —
so also record T2→T3 separately) plus every abandon point with its
timestamp.

Also observe silently: which mic they pick, which language they set,
whether they find hotkey binding, what the onboarding tryout did,
their first reaction to the live pill/preview.

## Part 2 — Tasks (14 min)

Each task: read the card, let them re-read. They choose the app.
Dictate, then **produce a result they'd actually send** — corrections
count as part of their time. Log per task: start, first-insertion,
completion ("I'd send this now"), corrections made (type: retype /
re-dictate whole / edit in place / vocabulary-add / give up on
accuracy), and any insertion failure or wrong-window event.

**Task A — message reply (matched pair, ~5 min).** Two comparable
reply cards (A1/A2 below), one dictated, one typed. Same participant,
order per the counterbalance table.

- A1 (dictation): "Reply to a teammate in your chat app: the deploy
  is delayed to Friday because of a failing migration test; you'll
  rerun it tonight and update the ticket."
- A2 (typing): "Reply to a teammate: the review is done, two small
  comments left, you'll merge after the CI run finishes."

**Task B — document paragraph (~4 min, dictation only).** "Open a
text editor and dictate one paragraph for a status report: what you
worked on this week, one thing that went wrong, and what's next."
Watch: long-take behavior, punctuation, capitalization, whether they
dictate punctuation or correct after.

**Task C — developer prompt with jargon (~5 min, dictation only).**
"Dictate a prompt for an AI assistant: ask it to explain why the
flaky pytest in `tests/test_insertion.py` hangs on Wayland, mention
the wl-copy timeout is 5 seconds, we're on GNOME 46, and ask for a
minimal repro." Watch: identifiers, file paths, numbers, mixed
language if they slip into their L1, and what they do when jargon
comes out wrong.

## Part 3 — Post-test interview (5 min, ask in this order)

1. In one sentence: what is this for? (comprehension check)
2. What was the most annoying moment? What was the moment it felt
   fastest/most useful?
3. Would you use this tomorrow for real work? For which of your
   daily writing? What would stop you?
4. **One-time purchase framing** (read neutrally): "The plan being
   considered: a one-time purchase — you pay once, the app keeps
   working, updates included for the first year, optional paid major
   upgrades after; local processing, no subscription, no cloud
   requirement." Then: What would you expect to pay for that, given
   what you just experienced? At what price would it feel too
   expensive? Too cheap to trust? (record numbers, don't suggest)
5. What does this replace for you — typing? another dictation tool?
   nothing yet? ("would-replace-what")
6. Anything you expected to find but didn't?

Close: thank them, state the two-week pilot invitation if they
opted in, restate deletion rights.

## Counterbalancing

- **Task order**: A-B-C, C-B-A alternating across participants
  (reverse every other session).
- **Typing vs dictation (the ≥30 % gate)**: within Task A, half the
  participants dictate first (A1 then A2), half type first (A2 then
  A1) — alternate every session and record the arm. The comparison
  per participant is dictation total time (speak→sent-ready,
  corrections included) vs typing total time on the matched card,
  with final accuracy noted (a faster but wrong transcript fails the
  gate). With 8–12 paired samples we report the median ratio and
  per-participant detail — paired, not pooled — and we report where
  dictation was worse (which task, which language, which speaker).
- **Mic class and language** spread across the sample per the
  corpus spec's stratification so the timing sample isn't all one
  laptop mic.

## Data captured per session (one sheet, filled live)

- Screener answers; session date; hardware (laptop model, mic class,
  distro, session type Wayland/X11); model + language chosen.
- Clocks: T0, T1, T2, T3; per-task start / first-insertion /
  completion; correction count and types per task.
- Abandon-point log (step, time, symptom, resolution).
- Insertion failures, wrong-window events, crashes (these also feed
  the finding ledger).
- Interview answers verbatim; price reaction numbers.
- Consent status (notes / notes+recording).

**Privacy note**: no telemetry exists in the app and none is added
for tests; all data above stays in the moderator's local session
notes (and the optional local recording, deleted after synthesis
into the user-test report). Participants' names live only in the
consent forms; notes use participant IDs P01–P12. Nothing is shared
that identifies a participant. Aggregate results are published in
the phase 1 user-test synthesis linked from the plan.

## Analysis mapping (after all sessions)

- First-use gate: fraction unaided to T3; median T2→T3.
- ≥30 % gate: median paired completion-time ratio (Task A arms);
  accuracy parity check.
- Top obstacles: ranked abandon points + correction-type frequency
  (feeds the plan's "top five user obstacles" deliverable).
- Price/offer evidence: interview questions 4–5 feed the phase 4
  commercial research (stated intent, not purchases — labeled as
  such).
