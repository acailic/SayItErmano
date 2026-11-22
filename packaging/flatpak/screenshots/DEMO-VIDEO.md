# Demo video storyboard — one minute (#12)

The Flathub new-app submission template requires a demo video link
in the PR. The current video is
[youtu.be/HWyjH-VCBAw](https://youtu.be/HWyjH-VCBAw) (linked from
the README). **Re-record before submitting if the UI has drifted**
(it has: the Srpski/Bosnian UI translations landed after that
recording) or if any beat below is missing — reviewers and the
listing both lean on it.

## Recording specs

- 60 s (±5 s), screen recording at ≥ 1080p / 30 fps.
- Clean user session: default theme, neutral wallpaper, no personal
  data (real-ish dictation content, fresh history).
- English UI, one continuous flow where possible; cuts are fine,
  titles/overlays are not — the app speaks for itself.
- Host the result on YouTube (same channel as the current demo) and
  use that link in the submission PR.

## Beats

| Time | Beat | What the viewer sees |
|------|------|----------------------|
| 0–4 s | Hook | The pill appears over a writer/chat app as the hotkey is held — instant "what is this" |
| 4–14 s | Core loop | Hold Right Ctrl, speak a full sentence: waveform + streaming partials in the pill; release — the *polished* sentence types itself into the app |
| 14–22 s | Before/after | Repeat with filler words/rambling input; show the AI-polish cleanup landing (the differentiator vs plain dictation) |
| 22–32 s | Anywhere | Switch target apps (editor → browser box → chat) to show insertion works system-wide |
| 32–40 s | Languages | Cycle languages mid-flow (e.g. en → sr), dictate a short phrase in the second language |
| 40–50 s | History | Open History, search, replay an entry |
| 50–57 s | Local & private | Settings → Models: model downloaded, "everything runs on this machine" beat (no cloud icons, no keys required for the default path) |
| 57–60 s | Close | Pill one last time + app name; no end-card narration needed |

## Checklist before using the link

- [ ] Every beat above present, ≤ 70 s total
- [ ] Current UI (post-translations), English locale for the main
      flow (a non-English beat is fine — it demonstrates the
      feature)
- [ ] No secrets/personal windows visible; fresh test profile
- [ ] Link added to the submission PR (see
      `docs/packaging/flathub-submission.md`) and to the README if
      the video changed
