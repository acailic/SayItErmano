# Flathub screenshot shot list (#12)

Owner-captured screenshots for the Flathub listing. The metainfo
(`packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml`)
references these exact filenames as
`https://raw.githubusercontent.com/acailic/SayItErmano/linux/packaging/flatpak/screenshots/<file>`
— the URLs go live when the PNGs land on `linux`, and
`tests/test_flatpak_manifest.py` pins the filename set (metainfo ↔
this list ↔ PNGs must not drift).

## Capture rules (Flathub quality guidelines)

Verified against
[docs.flathub.org quality guidelines](https://docs.flathub.org/docs/for-app-authors/metainfo-guidelines/quality-guidelines)
(2026-11-21):

- Linux, current UI, **default** settings (default font, theme,
  accent; not maximized — the window must keep its native shadow and
  rounded corners).
- Window-only captures via the screenshot tool's "window" mode: no
  wallpaper, no panels, no cropping, no added text or graphics.
- Window size **≤ 1000×700 logical px** (≤ 2000×1400 HiDPI physical)
  so text stays readable when Flathub scales the shot down.
- PNG, English UI (default locale; the Serbian/Bosnian translations
  make good extras tagged `xml:lang`, never the only shots).
- Re-take whenever the UI visibly changes — stale screenshots are a
  quality-check failure, and the first shot is the search-result
  face of the app.

## The shots

| # | File | Screen | State | Must show |
|---|------|--------|-------|-----------|
| 1 | `01-dictation-pill.png` | Dictation pill overlay (the app's face — shot 1 is what Flathub shows first, incl. banners) | Mid-dictation, active hold-to-talk | Live waveform + streaming partial transcript in the pill, hovering over a neutral app (e.g. a text editor receiving the text); compositor's pill shadow intact |
| 2 | `02-settings-models.png` | Settings → Models | A Whisper model present | Local model management: the model list, one-click switch, download state — proves "local, no cloud" at a glance |
| 3 | `03-history.png` | History window | A few entries + an active search | Search field with a query, entry cards with timestamps/tags, replay button — the "nothing is lost" story |

3 shots is the right count for an app this size (Flathub: 3–6 for
medium complexity). A 4th showing the pill's language cycling is a
good stretch shot if it composes cleanly.

## Capture state

- `02-settings-models.png` and `03-history.png`: captured 2026-11-23
  headlessly via `scripts/capture_flathub_shots.sh` (sandbox HOME with
  seeded fixture history, symlinked local model, Xvfb; English UI,
  960x680 / 900x680). Re-run that script whenever the Models or History
  UI visibly changes.
- `01-dictation-pill.png`: still a live-desktop owner capture — the pill
  needs a real recording (waveform + streaming partials). Capture with the
  system screenshot tool in window mode mid-dictation.

## Process

1. Prepare real content (dictate a few real sentences — no lorem
   ipsum, no empty states).
2. Capture per the rules above into this directory, exact filenames.
3. `python3 -c "import xml.etree.ElementTree as ET; ET.parse('packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml')"`
   and `pytest tests/test_flatpak_manifest.py -q` (pins filenames +
   PNG bounds once the files exist).
4. Commit on `linux` — the metainfo URLs resolve from that branch.
5. Re-verify each URL with curl once merged.

Existing `docs/screenshots/*.png` are README assets, not Flathub
shots: several show desktop context or non-default states, and the
Flathub URLs must point into this directory to stay under our
control.
