# Flathub submission — owner checklist (#12)

Everything the **human owner** does to get SayItErmano onto Flathub
stable, in order. Verified against the Flathub docs on 2026-11-21
(submission, requirements, verification) and again on 2026-11-22 for
section 0 (requirements, runtimes, quality guidelines — links
inline). Where a rule is quoted, re-check the page before relying on
it; Flathub policy evolves.

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
> touched them — section 0's engineering items included). **Author
> every file you put into the flathub PR by hand** — type the manifest
> out yourself, understand every line, and write the PR text, comments
> and replies in your own words. The Flathub-shaped manifest below is
> the reference you type from, not PR copy. This checklist and the
> notes it links are repo-side planning material, not PR copy.

## 0. Blockers — land these on `linux` first

Checked items are engineering that landed on `linux`; each says what
is still owner-only.

- [ ] **#11's engine slice**: the manifest must ship a working
      speech engine (whisper.cpp Vulkan / faster-whisper) — a
      dictation app that cannot transcribe out of the box is a
      "non-functional submission" and gets rejected. The remote-STT
      fallback alone is not enough for review. *(Still the first
      blocker; nothing below substitutes for it.)*

- [x] **Manifest source shape for Flathub** — two variants of one
      manifest, and ONLY their `sources:` blocks differ (the
      divergence rule both headers state; `tests/test_flatpak_manifest.py`
      enforces it):
      - `packaging/io.github.acailic.SayItErmano.yml` — the CI bundle
        manifest: keeps the *local* sources (`flatpak/*.xml`,
        `icons/` dir, vendored wheel files) for
        [`packaging/ci-flatpak-bundle.md`](../../packaging/flatpak/ci-flatpak-bundle.md);
      - `packaging/flatpak/io.github.acailic.SayItErmano.flathub.yml`
        — the Flathub shape: **zero local sources**. Metainfo, desktop
        file and icons install from the app's git source at the
        release tag (they live in-repo; "metadata must be integrated
        in the upstream project" — ours is), the wheels are remote
        `url` + `sha256` entries (the
        [flatpak-builder-tools](https://github.com/flatpak/flatpak-builder-tools)
        pip-generator shape; same files and hashes as the vendored
        copies). A Flathub PR may contain **no source code and no
        binary/precompiled files** — only the manifest, top level,
        named after the app ID.
      Owner-only remainder: typing the manifest into the PR by hand
      (policy above); the local dry run (section 1) has not been run
      against the 51 runtime yet.

- [x] **License files** — every module installs its license to
      `$FLATPAK_DEST/share/licenses/$FLATPAK_ID/<name>/` in both
      variants: `sayitermano-data` and `sayitermano` install the
      project `LICENSE`; `deps` copies each wheel's own `.dist-info`
      license (`six`, `python-xlib`, `pillow`, `dbus-next`,
      `global-shortcut-portal`) — the
      [requirements page](https://docs.flathub.org/docs/for-app-authors/requirements)
      asks for exactly this location, one directory per module.
      Fixed on the way: `python-xlib` hard-imports `six`, which the
      runtime's Python does not ship and which was not vendored — pip
      would have had no way to resolve it (flatpak-builder builds run
      without network by default, Flathub's and the CI bundle's
      alike), failing the `deps` module. `six` is vendored now and the
      deps install runs `--no-index`, so a missing dependency fails
      the build instead of ever reaching PyPI.

- [x] **Runtime freshness** — bumped `runtime-version` **48 → 51**
      in both variants on 2026-11-22. Evidence: Flathub's runtimes API
      (<https://flathub.org/api/v2/runtimes>) lists `org.gnome.Platform`
      branches 48–51 with 51 the newest (and the most used); the Flathub
      repo summary (<https://dl.flathub.org/repo/summary>, the metadata
      `flatpak-builder-lint`'s EOL check reads) marks 48 `eol` — "no
      longer supported as of March 24, 2026"; the
      [requirements page](https://docs.flathub.org/docs/for-app-authors/requirements)
      requires "the latest version at that time of submission", and the
      linter reports an EOL runtime three versions behind as an error —
      which 48 now is. Consequence carried along: GNOME 51 sits on
      freedesktop-sdk 26.08, i.e. **Python 3.14** (48 was 3.12), so
      the compiled Pillow wheel moved `cp312` → `cp314`; the unit test
      pins the runtime ↔ wheel-ABI pair. **Re-check at submission
      time** (a new GNOME release lands every six months):
      `.venv/bin/python -m pytest tests/integration/test_flatpak_runtime_freshness.py`
      — networked, fails as soon as Flathub hosts a newer branch.

- [x] **Release tag** — both variants pin `tag: v0.9.0` (the
      `TODO(submission, #12)` is gone); the Flathub variant also
      carries `commit:` (the linter errors on a tag without a commit
      for new submissions; the CI variant deliberately does not —
      see its header). The unit gate pins tag == `v<pyproject
      version>` and, wherever the tag is in the clone, that every
      file the data module installs exists at the tag. "Insufficient
      development history" is a rejection class — the tag history is
      the evidence. Per release from here on: forward-declare the tag
      before `just release-prepare`, set the commit after
      `release-publish` tags ([release-gates](../dev/release-gates.md)).

- [ ] **Screenshots**: captured per
      [`packaging/flatpak/screenshots/SHOT-LIST.md`](../../packaging/flatpak/screenshots/SHOT-LIST.md),
      committed on `linux` so the metainfo URLs resolve. *(owner-only:
      GUI capture)*

- [ ] **Demo video**: current per
      [`packaging/flatpak/screenshots/DEMO-VIDEO.md`](../../packaging/flatpak/screenshots/DEMO-VIDEO.md)
      — the submission template asks for it. *(owner-only)*

Still owner-only after section 0's engineering: the engine slice
(#11), the screenshot PNGs, the demo video, the local dry run
(section 1) and the submission PR itself (section 2).

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

The title/body worksheet and the step-by-step mechanics live in
[`packaging/flatpak/SUBMISSION-PR.md`](../../packaging/flatpak/SUBMISSION-PR.md);
the exact manifest bytes the PR must reproduce are staged on this
repo's `flathub-submission` branch (top-level
`io.github.acailic.SayItErmano.yml`, alone — byte-parity with the
Flathub-shaped reference and the nothing-else tree shape are pinned
by `tests/test_flathub_submission_branch.py`). Both are worksheets,
not PR copy (policy note at the top).

Fork → branch → PR mechanics (per the
[submission guide](https://docs.flathub.org/docs/for-app-authors/submission)):

1. Fork [flathub/flathub](https://github.com/flathub/flathub) with
   *"Copy the master branch only"* **unchecked** (you need `new-pr`).
2. `git clone --branch=new-pr git@github.com:<you>/flathub.git &&
   git checkout -b sayitermano-submission new-pr`
3. Add **only** `io.github.acailic.SayItErmano.yml` at the top level
   (plus `flathub.json` only if limiting architectures). Type it by
   hand from the repo's Flathub-shaped reference,
   `packaging/flatpak/io.github.acailic.SayItErmano.flathub.yml`
   (policy note at the top — the reference is not PR copy), then run
   the linter from section 1 against what you typed.
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
      `flathub/io.github.acailic.SayItErmano`. Each release, the
      flathub repo's manifest needs the new `tag` + `commit` (and any
      wheel/runtime change made in the repo's Flathub-shaped variant
      the same cycle — the per-release recipe is in
      [release-gates](../dev/release-gates.md)).
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
