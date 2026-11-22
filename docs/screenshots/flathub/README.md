# Flathub listing assets — shot list & demo-video storyboard

Owner-recorded screenshots and the one-minute demo video for the
Flathub listing (#12). The appstream metainfo
(`packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml`)
references these files by their **raw.githubusercontent.com URLs on the
`linux` branch**, so:

- file names here and URLs in the metainfo must stay in sync —
  `tests/test_flatpak_manifest.py` fails on drift;
- an asset only "exists" for the listing once it is **committed to the
  `linux` branch** — the submission checklist
  (`docs/packaging/flathub-submission.md`) gates on every metainfo URL
  returning HTTP 200 before anything is submitted.

Recording these is GUI work the owner does by hand; nothing here is
automatable in CI.

## Capture environment

Capture **from the Flatpak build**, not a host install — the listing
must show the sandboxed app exactly as Flathub users get it:

1. Build the bundle from the current manifest
   (`packaging/io.github.acailic.SayItErmano.yml`) as described in
   [ci-flatpak-bundle.md](../../../packaging/flatpak/ci-flatpak-bundle.md).
2. `flatpak install --user --bundled ...` / `flatpak run io.github.acailic.SayItErmano`
   on a clean-ish user session with the default Adwaita light style.
3. Seed believable state: 6–10 history entries of real-looking text
   (mixed lengths, one in Serbian), a configured mic, one model
   downloaded. No fake lorem ipsum — reviewers read screenshots.

## Quality bar

- PNG, at least 1200 px wide (the composed overlay shot at 1600×900 /
  1920×1080); sharp text, no cursor trails, no visible host
  terminals/paths that imply host dependencies.
- No personally identifying content: no real names, e-mails, windows
  with private data in the background.
- Captions are already written in the metainfo — the image must
  actually show what its caption claims.

## Assets

| File | Kind | Screen + state | Must show |
|---|---|---|---|
| `flathub/dictation-overlay.png` | screenshot (default) | Full desktop mid-dictation: a chat app or editor focused and receiving text, pill overlay visible while speaking | The pill with live waveform AND streaming partial transcript, elapsed-time indicator, the target app behind it — the "press, speak, done" promise in one frame |
| `flathub/settings.png` | screenshot | Settings → Dictation page, window focused | Global hotkey row, language list/cycling, microphone picker, model row — proves the app is fully usable from its window (tray-only apps are rejected) |
| `flathub/history.png` | screenshot | History window with search active | Search field with a query matching a subset, entry list with timestamps + tags, replay/export controls visible |
| `flathub/demo.webm` | video (60 s) | The one-minute demo, storyboarded below | Hotkey → portal consent → dictation with polish → language cycling → history — the submission template requires a demo video |

## Demo video storyboard (60 s, `flathub/demo.webm`)

The submission template requires a demo video. Flathub serves it from
the metainfo `<video>` URL; WebM (VP9 + Opus) is the safe container
for every app center. 1920×1080, no intro bumper, no YouTube branding,
host terminal only ever shows the one `flatpak run` if at all.

| Time | Beat | Must show |
|---|---|---|
| 0:00–0:05 | Cold start from the app launcher (NOT the tray) | Launcher icon → Settings window opens: window-first usability |
| 0:05–0:13 | Bind the hotkey in Settings → Dictation | The GlobalShortcuts **portal consent dialog** accepting the shortcut — the portal-based input story reviewers look for |
| 0:13–0:33 | Core dictation into a chat app | Hotkey press → pill + waveform; speak a filler-heavy sentence ("um, let's meet on tuesday around three… no wait, four pm"); stop → the **polished** sentence lands in the chat box |
| 0:33–0:43 | Language cycling | Language hotkey cycles to Srpski, short Serbian dictation inserts correctly |
| 0:43–0:53 | History | Open History (window, not tray), type a search term, replay one entry's audio, mention export via the visible button |
| 0:53–1:00 | Close | End on the pill collapsing + the polished text in place; brief end card with the app name if desired |

Encode after editing:

```bash
ffmpeg -i demo-raw.mp4 -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus \
    -vf scale=1920:1080 docs/screenshots/flathub/demo.webm
```

Aim for ≤ 20 MB; larger files make software centers (and the Flathub
build) unnecessarily slow.

## Before submitting — verify every URL resolves

After committing the assets to `linux`:

```bash
grep -o 'https://raw.githubusercontent.com/acailic/SayItErmano/linux/docs/screenshots/flathub/[^<]*' \
    packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml \
  | sort -u | while read -r u; do
      curl -fsSI "$u" >/dev/null || echo "MISSING: $u"
    done
```

Empty output = all metainfo assets are live. This check is step 2 of
the [submission checklist](../../packaging/flathub-submission.md).
