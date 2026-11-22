# Flathub submission — the human checklist (#12)

Everything the owner does to get SayItErmano accepted on Flathub
**stable**, in order. This file is repo-side preparation only; the
submission itself is human-only work (below).

> **Generative-AI policy — read first.** Flathub's policy forbids
> AI-generated or AI-assisted content on the submission: the manifest
> as committed to flathub/flathub, the PR text, commit messages, the
> listing description as finally submitted, and every review comment
> and reply. **No AI tool or agent may open, comment on, or reply to
> the submission PR.** Concretely:
>
> - The owner authors everything that lands in flathub/flathub **in
>   their own words** — including rewriting/adapting any repo-side
>   draft (metainfo description, the drafts at the bottom of this
>   file, the permission justifications) until it is their own text.
> - Agents never touch the flathub fork, never push branches there,
>   never comment. If a bot must be mentioned, mention it factually in
>   your own words; do not let one write.
> - Repo-side files (this checklist, the shot list, tests) are not
>   submission content — but anything copied into the PR gets
>   rewritten by a human first.

## Already in place (repo-side)

| Piece | Where |
|---|---|
| Shipping manifest (ADR-0006 baseline permissions) | `packaging/io.github.acailic.SayItErmano.yml` |
| Per-permission justifications (source for the PR text) | `docs/packaging/flatpak-permissions.md` |
| Appstream metainfo: keywords for the four target queries, screenshots + demo-video block, GPL-3.0, OARS, community-port note | `packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml` |
| Shot list + 60 s demo storyboard | `docs/screenshots/flathub/README.md` |
| Structural gates (manifest/metainfo/shot-list sync) | `tests/test_flatpak_manifest.py` |

Why these keywords: verified 2026-11-21, the four Flathub queries
(`dictation`, `voice typing`, `speech to text`, `whisper`) return
≤ 21 hits and nothing in them is a polished push-to-talk + AI-polish
product; `whisper` alone is a trap (its #1 is an unrelated
mic-monitor utility), so the listing must rank on its own terms.

## Step 0 — Preconditions (repo-side, before anything public)

1. **Engine slice of #11 landed**: the speech engine (whisper.cpp
   Vulkan / faster-whisper) builds inside the Flatpak sandbox. Policy:
   core function must not depend on host-installed components — a
   listing whose dictation only works with a host engine or a
   user-configured remote endpoint is a rejection risk. Verify with a
   bundle build + offline dictation test (`ci-flatpak-bundle.md`).
2. **Release tag exists and the manifest points at it**: tag
   `v<version>` (matching `pyproject.toml`'s version and the metainfo
   `<release>`), then change the manifest's git source from
   `branch: linux` to the tag (the `TODO(submission, #12)` in the
   yml). Flathub builds must come from stable refs.
3. **Gates green**: `pytest tests/test_flatpak_manifest.py -q`,
   `ruff check .`, and the metainfo tag check:
   `python3 -c "import xml.etree.ElementTree as ET; r=ET.parse('packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml').getroot(); [print(t, r.find(t)) for t in ('id','name','summary','keywords','screenshots','releases','content_rating')]"`
   — no `None` in the output.

## Step 1 — Record the listing assets (owner, GUI)

Work through `docs/screenshots/flathub/README.md`: three screenshots
against the shot list, the 60 s demo video against the storyboard,
captured **from the Flatpak bundle build**. Commit them to `linux`
(the metainfo URLs pin that branch), then run the URL check from that
README — every metainfo asset URL must answer HTTP 200 (raw URLs lag
the push by a minute or two).

## Step 2 — Domain verification file

The app id `io.github.acailic.SayItErmano` reverses to the domain
`acailic.github.io`. Flathub verifies domain-based ids by serving a
verification file from that domain:

1. In the Flathub web UI (sign in with GitHub), open the developer /
   app settings and start website verification for the app id; Flathub
   shows the exact file name and the expected content (the file lists
   the app ids the domain vouches for — the documented name is
   `org.flathub.VerifiedApps.txt` under `/.well-known/`).
2. The file must resolve at the **domain root**:
   `https://acailic.github.io/.well-known/org.flathub.VerifiedApps.txt`.
   The project site deploys at `acailic.github.io/SayItErmano/`
   (repo subpath — see `site/` and `.github/workflows/pages.yml`), so
   the root needs the user-site repository `acailic.github.io` (create
   it if absent, containing just `.well-known/`), or another host the
   owner controls for that hostname.
3. Trigger Flathub's re-check; the badge appears once it reads the
   file. (Verification is not required for submission — do it in
   parallel — but it is on the acceptance list.)

## Step 3 — The submission PR (human-only, stable-first)

New apps target the **stable** repo; a beta branch only exists after
acceptance and is not listed on the website (Step 5). Submission is a
GitHub PR **against flathub/flathub** from a fork:

1. Fork `github.com/flathub/flathub`, clone the fork, create a branch
   named `new-pr`.
2. Add exactly one file at the repo root: `io.github.acailic.SayItErmano.yml`
   — a copy of `packaging/io.github.acailic.SayItErmano.yml`. Review
   it line by line and make the text your own where you can (comment
   wording): you are the author of what is submitted.
3. Open the PR. In your own words, state what the app is, that it is
   the unofficial community port of FluidVoice (macOS), GPL-3.0-or-
   later, and **justify every finish-arg** — the facts live in the
   table in `docs/packaging/flatpak-permissions.md`:
   - `--socket=wayland` — GTK 4 UI; hotkey + text insertion go
     through the GlobalShortcuts / RemoteDesktop portals, no extra
     bus permissions;
   - `--socket=pulseaudio` — microphone capture is the core function;
   - `--device=dri` — GPU nodes for whisper.cpp Vulkan (CPU int8
     works without it; NVIDIA via the `org.freedesktop.Platform.GL.*
     extension);
   - `--share=network` — first-run model download + update check, no
     telemetry, check disableable;
   - `--env=SAYITERMANO_FLATPAK=1` — not a permission; sandbox
     lifecycle marker (the GTK process owns the daemon).
   Also volunteer what is *absent* on purpose (`--device=all`,
   fallback-x11, StatusNotifierWatcher talk-name — documented opt-in
   overrides, see the permissions doc) and that the app is fully
   usable from its Settings window.
4. The **submission-checker bot runs hourly** — expect its report on
   the PR, fix findings by pushing to `new-pr`, in your own words.
   Approval means Flathub builds the app from your manifest on their
   infra.

## Step 4 — During review

- Replies are yours, human-only (the AI policy covers comments and
  replies too).
- Likely pressure points and the repo-side answers:
  - *Tray-only?* No — Settings and History are real windows; the tray
    icon is optional and even needs an opt-in permission.
  - *Host components?* None for core function: input via portals,
    engine in-sandbox (Step 0), models under `~/.var/app/…/cache`.
  - *Extra permissions?* The baseline is the ADR-0006 minimum; the
    opt-in table in `flatpak-permissions.md` documents each refusal.

## Step 5 — After acceptance

1. The app moves to `flathub/io.github.acailic.SayItErmano`; you gain
   direct push rights there (update flow: push manifest changes,
   Flathub rebuilds stable).
2. Confirm the **stable** listing page and the verification badge.
3. **Beta channel (post-acceptance only):** create a `beta` branch in
   `flathub/io.github.acailic.SayItErmano` whose manifest publishes
   prerelease versions (e.g. `0.9~beta` / next-minor pre tags) with
   the same permission baseline; users opt in with
   `flatpak install --user flathub beta/io.github.acailic.SayItErmano`.
   It is not listed on the website — link it from the README instead.
4. Acceptance evidence (issue #12 checklist):
   - search the website for `dictation`, `voice typing`,
     `speech to text`, `whisper` — the listing must appear;
   - clean-VM install (`flatpak install flathub
     io.github.acailic.SayItErmano`) matches the README quick-start:
     launcher entry, Right-Ctrl toggle, text lands in any app;
   - demo video, screenshots, per-permission justifications and the
     domain verification file all present;
   - add Flathub as an install route in the README/install guide
     (repo-side follow-up).

## Listing copy — DRAFTS (rewrite in your own words before use)

Internal reference only; per the AI policy the submitted/edited text
must be the owner's own. The metainfo already carries both facts —
these are shorter variants for the PR text or app-page edits:

- **Community port:** "SayItErmano is an unofficial community port of
  FluidVoice, the macOS dictation app, to Linux — same behavior, not
  built by the FluidVoice authors. Upstream: github.com/altic-dev/
  FluidVoice."
- **License:** "GPL-3.0-or-later (the metainfo declares it; the vendored
  portal-input wheel is AGPL-3.0-only, combined one-way into GPLv3 —
  see docs/packaging/flatpak-permissions.md, 'Engine scope')."

## Verification commands (repo-side gates)

```bash
python3 -m pytest tests/test_flatpak_manifest.py -q   # manifest/metainfo sync
python3 -m ruff check .                               # lint
python3 - <<'EOF'                                     # metainfo required tags
import xml.etree.ElementTree as ET
r = ET.parse('packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml').getroot()
missing = [t for t in ('id', 'name', 'summary', 'keywords', 'screenshots',
                       'releases', 'content_rating') if r.find(t) is None]
assert not missing, missing
print('metainfo OK')
EOF
```
