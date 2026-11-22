# Flathub submission — owner checklist (#12)

Everything the **human owner** does to get SayItErmano onto Flathub
stable, in order. Verified against the Flathub docs on 2026-11-21
(submission, requirements, verification, quality guidelines — links
inline); requirements/runtimes re-checked 2026-11-22 for the section-0
items below. Where a rule is quoted, re-check the page before relying
on it; Flathub policy evolves.

> **Generative-AI policy — read first.**
> Flathub's [Generative AI policy](https://docs.flathub.org/docs/for-app-authors/requirements#generative-ai-policy)
> says, in effect:
>
> - manifests **must not contain AI-generated or AI-assisted
>   content** (disclosure does not exempt them);
> - AI tools or agents must not open or automate submission PRs, or
>   generate their commit messages, descriptions, review comments or
>   replies;
> - known AI-generated material anywhere in the app or its Flathub
>   packaging must be disclosed, and reviewers may reject on that
>   basis.
>
> Consequence for this repo: the manifest, metainfo and desktop file
> under `packaging/` are working documents (agent sessions have
> touched them). **Author every file you put into the flathub PR by
> hand** — type the manifest out yourself, understand every line,
> and write the PR text, comments and replies in your own words.
> This checklist and the notes it links are repo-side planning
> material, not PR copy.

## 0. Blockers — land these on `linux` first

- [ ] **#11's engine slice**: the manifest must ship a working
      speech engine (whisper.cpp Vulkan / faster-whisper) — a
      dictation app that cannot transcribe out of the box is a
      "non-functional submission" and gets rejected. The remote-STT
      fallback alone is not enough for review.
- [x] **Manifest source shape for Flathub** (landed 2026-11-22):
      the submission-shaped copy lives at
      `packaging/flatpak/io.github.acailic.SayItErmano.flathub.yml` —
      zero local sources (metainfo/desktop/icons install from the
      `sayitermano` git module; the wheels are remote PyPI
      `url`+`sha256` entries pinned to the very artifacts vendored
      for CI). It diverges from `packaging/io.github.acailic
      .SayItErmano.yml` **only in source locations** — that
      divergence rule is pinned by `tests/test_flatpak_manifest.py`.
      For the PR itself: re-type the manifest by hand (Generative-AI
      policy above) and copy it to the top level of the fork as
      `io.github.acailic.SayItErmano.yml`.
- [x] **License files** (landed 2026-11-22): every module in both
      variants installs its license to
      `$FLATPAK_DEST/share/licenses/$FLATPAK_ID` (per-module
      subdirectories; wheel licenses extract from each wheel's
      `.dist-info`). Enforced by
      `test_every_module_installs_its_license`.
- [x] **Runtime freshness** (checked 2026-11-22): GNOME **51** is the
      latest stable — [release.gnome.org](https://release.gnome.org/)
      lists 51 as the current release, and runtime branches track
      GNOME releases with EOL on the next stable
      ([runtimes doc](https://docs.flathub.org/docs/for-app-authors/runtimes)).
      Both manifests bumped 48 → 51; note 51's freedesktop-sdk 26.08
      base ships Python 3.14, so the pillow wheel is now the cp314
      build. **Re-check the branch again on submission day** — EOL
      runtimes are rejected.
- [x] **Release tag** (landed 2026-11-22): the `sayitermano` git
      source is pinned to tag `v0.9.0` in BOTH variants (the
      `TODO(submission, #12)` resolved). The pin is
      version-following: `tests/test_flatpak_manifest.py` asserts it
      equals `v<pyproject version>`, so release-prepare's version
      bump fails the suite until the pin moves — bump both manifests
      when tagging a release.
- [ ] **Screenshots**: captured per
      [`packaging/flatpak/screenshots/SHOT-LIST.md`](../../packaging/flatpak/screenshots/SHOT-LIST.md),
      committed on `linux` so the metainfo URLs resolve.
- [ ] **Demo video**: current per
      [`packaging/flatpak/screenshots/DEMO-VIDEO.md`](../../packaging/flatpak/screenshots/DEMO-VIDEO.md)
      — the submission template asks for it.

Remaining owner-only work in this section: the engine slice (#11),
the screenshot PNGs (GUI capture), the demo video — and the
submission PR itself (section 2), which by the Generative-AI policy
must be hand-authored throughout.

## 1. Local dry run (expedites review)

- [ ] Build with Flathub's own builder:
  `flatpak install -y flathub org.flatpak.Builder` then
  `flatpak run --command=flathub-build org.flatpak.Builder --install <manifest>`
- [ ] `flatpak run io.github.acailic.SayItErmano` — full
  quick-start pass (hotkey, dictation, polish, history, settings)
  on a clean VM or clean user account, matching the README.
- [ ] Linter, fix everything it flags:
  `flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest <manifest>`
  (and `... repo repo` after a build). Certain errors need a
  documented exception instead of a fix.

## 2. The submission PR (human-only)

Fork → branch → PR mechanics (per the
[submission guide](https://docs.flathub.org/docs/for-app-authors/submission)):

1. Fork [flathub/flathub](https://github.com/flathub/flathub) with
   *"Copy the master branch only"* **unchecked** (you need `new-pr`).
2. `git clone --branch=new-pr git@github.com:<you>/flathub.git &&
   git checkout -b sayitermano-submission new-pr`
3. Add **only** `io.github.acailic.SayItErmano.yml` at the top level
   (plus `flathub.json` only if limiting architectures).
4. Open the PR against the **`new-pr`** base branch (never `master`),
   titled `Add io.github.acailic.SayItErmano`. Keep the PR template;
   PRs that strip it get closed unreviewed.
5. In the PR body (your own words):
   - what the app is + why it belongs on Flathub;
   - **a justification per finish-arg** — source material:
     [flatpak-permissions.md](flatpak-permissions.md) table
     (`wayland`, `pulseaudio`, `dri`, `network`; permissions are
     [reviewed separately](https://docs.flathub.org/docs/for-app-authors/maintenance#permission-review-on-pull-requests));
   - the demo video link;
   - monetisation disclosure if reviewers would hit it: license keys
     exist ([PRICING](../PRICING.md)) — offer a reviewer test key;
     core dictation must stay fully usable unpaid.
6. The **submission-checker bot** runs roughly hourly on the PR;
   fix what it flags. After reviewer comments resolve, comment
   `bot, build` for a test build. (Screenshots only render from
   *official* builds, not test builds — don't panic.)
7. Answer every reviewer question. Never close the PR to address
   feedback; never merge `master` into the submission branch.

App-ID sanity (already correct): `io.github.acailic.SayItErmano`
matches the GitHub host `github.com/acailic/SayItErmano`
(`com.github.*` is banned for new apps); the metainfo `<id>`,
desktop filename and manifest `id` are identical.

## 3. Policy compliance map

| Rule (requirements page) | Our position |
|---|---|
| Tray-only apps rejected | Full GTK Settings + History windows; tray is an *opt-in* override — see flatpak-permissions.md |
| Host-dependent core function rejected | Hotkey + insertion go through the freedesktop portals (GlobalShortcuts, RemoteDesktop/EIS) — no host tools by default; uinput/ydotool are documented last-resort overrides, not requirements |
| Portals over static permissions | Background portal for autostart; portals carry input; static set is the four-arg baseline |
| Stable-first | New apps go to **stable** only; a `beta` branch may be created **after** acceptance (maintenance guide) and is not listed on the website |
| Localisation | Complete English UI (plus sr/sr-Latn/bs); machine-quality translations not allowed — ours are human |
| License | GPL-3.0-or-later, declared in metainfo and matching pyproject; redistribution legal |

## 4. Verification (the checkmark)

For `io.github.*` app IDs, Flathub verifies via the **code-hosting
login provider**: log in to
[flathub.org](https://flathub.org/login) with the GitHub account
that **owns** `acailic/SayItErmano`, Developer Portal → the app →
Verification. No domain file is involved for this ID shape — the
`.well-known/org.flathub.VerifiedApps.txt` token route applies to
apps whose ID sits on a domain the submitter owns, which is not our
case (and `github.io` pages can't verify `io.github.*` IDs). Do
this right after accepting the repo-access invite.

## 5. After acceptance

- [ ] Accept the flathub org repo invite (needs 2FA; expires in a
      week).
- [ ] Verify (above).
- [ ] Updates never re-enter the submission flow: PRs against
      `flathub/io.github.acailic.SayItErmano`.
- [ ] Beta channel (optional, post-acceptance only): create a
      `beta` branch on that repo per the
      [maintenance guide](https://docs.flathub.org/docs/for-app-authors/maintenance#creating-new-git-branches-for-publishing).
- [ ] Watch search placement for the six queries
      (`dictation`, `voice typing`, `speech to text`, `whisper`,
      `transcription`, `voice input`) once indexed — the keyword set
      in the metainfo exists to carry exactly these.

## 6. Listing copy — source notes

Facts for the listing/PR (already encoded in the metainfo
description; rewrite anything you post in your own words):

- **Community port**: SayItErmano is the unofficial community Linux
  port of FluidVoice (macOS, GPL-3.0); not built by or affiliated
  with the upstream authors; independent Python implementation of
  the same behavior (evidence: `docs/BEHAVIOR-SPEC.md`). Flathub's
  trademark rule requires this distinctness to stay visible —
  the name, icon and description already carry it.
- **GPL-3.0-or-later**: project license (LICENSE, pyproject,
  metainfo `project_license` all agree). The one AGPL-3.0-only
  vendored dep (`global-shortcut-portal`) combines lawfully — the
  FSF one-way GPLv3↔AGPLv3 compatibility note in
  flatpak-permissions.md's engine-scope section is the reference if
  a reviewer asks.
- **Privacy line**: speech-to-text runs locally; the optional AI
  polish uses a provider + key the user configures; no telemetry
  (`docs/research/2026-11-21-network-egress-audit.md` is the
  evidence if asked).
