# Flathub submission PR — title/body worksheet + owner checklist

The paste-ready artifact pair for section 2 of the runbook
([flathub-submission.md](../../docs/packaging/flathub-submission.md)):
the PR title/body a human takes into flathub/flathub's `new-pr` flow,
and the mechanics around it. The exact manifest bytes the PR must
reproduce are staged on this repo's `flathub-submission` branch —
`io.github.acailic.SayItErmano.yml` at the top level, alone;
`tests/test_flathub_submission_branch.py` pins byte-parity with
[the Flathub-shaped reference](io.github.acailic.SayItErmano.flathub.yml)
and that the branch carries nothing else.

> **Generative-AI policy — this file is a worksheet, not PR copy.**
> Flathub's [Generative AI
> policy](https://docs.flathub.org/docs/for-app-authors/requirements#generative-ai-policy)
> forbids AI-generated or AI-assisted content in manifests, PR
> descriptions, comments and replies. Everything below is source
> material: the owner rewrites each block in their own words before
> posting (the `<...>` placeholders mark the mandatory rewrites), and
> types the manifest into the PR by hand, understanding every line —
> the `flathub-submission` branch is the reference to type from, not
> copy-paste material.

## PR title

```
Add io.github.acailic.SayItErmano
```

Opened against flathub/flathub's **`new-pr`** base branch (never
`master`). Keep Flathub's own PR template headings and slot the
blocks below into them — PRs that strip the template get closed
unreviewed.

## PR body worksheet

### The app — what it is and why it belongs on Flathub

<One short paragraph in your own words. Facts to draw on — metainfo,
README, runbook section 6:>

- Dictation app for Linux: press a key, speak, the polished text
  lands in any app. Speech-to-text runs 100% locally (Whisper;
  models download on first run). Optional AI polish routes through a
  provider + key the user configures; no telemetry.
- Native GTK 4 / libadwaita app (Settings + History windows), not a
  tray-only or wrapper app; input goes through the freedesktop
  portals (GlobalShortcuts hotkey, RemoteDesktop/EIS insertion).
- The unofficial community Linux port of FluidVoice (macOS,
  GPL-3.0-or-later) — not built by or affiliated with the upstream
  authors; an independent Python implementation of the same
  behavior.
- Why Flathub: one build reaching every distro; the app is
  portal-first and sandbox-shaped rather than adapted to it.

### App ID rationale

`io.github.acailic.SayItErmano` follows the `io.github.<owner>.<repo>`
shape for the code host `github.com/acailic/SayItErmano`
(`com.github.*` is rejected for new apps). The metainfo `<id>`, the
desktop file name and the manifest `id` are identical.

### Permissions — a justification per finish-arg

<Keep the table; reword the justifications yourself. Source: the
reviewed ADR-0006 baseline in
[flatpak-permissions.md](../../docs/packaging/flatpak-permissions.md)
— permissions are [reviewed
separately](https://docs.flathub.org/docs/for-app-authors/maintenance#permission-review-on-pull-requests)
on submission PRs.>

| finish-arg | Why it is needed |
|---|---|
| `--socket=wayland` | The app is a GTK 4/libadwaita application rendering on the Wayland display. The dictation hotkey (GlobalShortcuts portal) and text insertion (RemoteDesktop portal + EIS) go through the portals on the session bus — no extra permission; the compositor owns all grabs. |
| `--socket=pulseaudio` | Microphone capture for dictation (PipeWire/PulseAudio). Recording is the app's core function; no portal path covers audio input. |
| `--device=dri` | GPU device nodes for whisper.cpp's Vulkan backend (CPU int8 works without it). NVIDIA users get Vulkan through the `org.freedesktop.Platform.GL.nvidia-*` runtime extension; no CUDA in the sandbox. |
| `--share=network` | First-run speech-model download (Hugging Face/GitHub releases, into the per-app cache) and the release update check. No telemetry; the check is disableable. |
| `--env=SAYITERMANO_FLATPAK=1` | Not a permission: flags the sandbox so the GTK app process owns the daemon + control socket (no systemd user units exist inside). |

What is deliberately absent: `--device=all`, `--socket=fallback-x11`
/ `--share=ipc`, `--talk-name=org.kde.StatusNotifierWatcher` — all
documented opt-in user overrides, not defaults
([flatpak-permissions.md](../../docs/packaging/flatpak-permissions.md)).

### Channel

Stable-first: new apps ship to the **stable** branch only; a `beta`
branch may be created after acceptance and is not listed on the
website. This submission targets stable.

### Demo video

<demo-video-link — paste the link, and say one line about what it
shows. Current candidate: https://youtu.be/HWyjH-VCBAw — re-record
first if the UI has drifted (see
[screenshots/DEMO-VIDEO.md](screenshots/DEMO-VIDEO.md)).>

### Screenshots

<Linked from the PR for the reviewers; the listing itself renders
them from the metainfo. Same raw `linux` URLs the metainfo pins:>

- https://raw.githubusercontent.com/acailic/SayItErmano/linux/packaging/flatpak/screenshots/01-dictation-pill.png
- https://raw.githubusercontent.com/acailic/SayItErmano/linux/packaging/flatpak/screenshots/02-settings-models.png
- https://raw.githubusercontent.com/acailic/SayItErmano/linux/packaging/flatpak/screenshots/03-history.png

### Monetisation disclosure

<In your own words — reviewers hit the paywall, disclose it first.>
License keys exist as an optional purchase (AI-polish conveniences;
see [PRICING](../../docs/PRICING.md)); core dictation is fully
usable unpaid. A reviewer test key is available on request
<reviewer-key-plan>.

### Verification

`io.github.*` app IDs verify through the **code-hosting login
provider**: the GitHub account that owns `acailic/SayItErmano` logs
in to flathub.org, Developer Portal → the app → Verification. No
domain file is involved for this ID shape.

## Owner checklist — fork to checkmark

1. **Pre-flight done** — runbook section 0 blockers cleared
   (engine slice, screenshots, demo video) and the section 1 dry
   run passed the linter.
2. **Fork** [flathub/flathub](https://github.com/flathub/flathub)
   with *"Copy the master branch only"* **unchecked** (you need the
   `new-pr` branch).
3. **Clone the fork and branch**:
   `git clone --branch=new-pr git@github.com:<you>/flathub.git &&
   git checkout -b sayitermano-submission new-pr`
4. **Add only** `io.github.acailic.SayItErmano.yml` at the top level
   (plus `flathub.json` only if limiting architectures). Type it by
   hand from this repo's `flathub-submission` branch / the
   Flathub-shaped reference (policy note above). No source code, no
   binaries — the Flathub hard rule; then run the section 1 linter
   against what you typed.
5. **Open the PR** against the **`new-pr`** base branch, titled
   `Add io.github.acailic.SayItErmano`, keeping the PR template.
   Fill the body from the worksheet above, in your own words.
6. **Bot loop**: the submission-checker bot runs roughly hourly —
   fix what it flags. After reviewer comments resolve, comment
   `bot, build` for a test build (screenshots only render from
   *official* builds, not test builds).
7. **Review**: answer every reviewer question; never close the PR to
   address feedback; never merge `master` into the submission
   branch.
8. **After acceptance**: accept the flathub org repo invite (needs
   2FA; expires in a week), then verify via the login provider
   (section above). Updates from then on are plain PRs against
   `flathub/io.github.acailic.SayItErmano` — they never re-enter the
   submission flow.
