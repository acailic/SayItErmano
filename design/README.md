# Design assets

## App icon — single source

`scripts/gen-app-icon.py` is the ONLY producer of every shipped app-icon
copy (original artwork, three palettes: `sol` — the shipped default —
`fiesta`, `noche`):

```bash
.venv/bin/python scripts/gen-app-icon.py sol --install
```

writes, from one render:

- `fluidvoice/assets/icon.png` — tray + overlay pill (loaded directly)
- `fluidvoice/assets/icons/sayit-ermano.png` — in-package icon-theme
  lookup (`Gtk.Window.set_default_icon_name("sayit-ermano")`)
- `packaging/icons/hicolor/<16..512>/apps/sayit-ermano.png` — the deb's
  hicolor set (`.desktop` `Icon=sayit-ermano`)

Without `--install` it renders palette previews into `design/icons/`
(`*-512.png`). The byte-identical copies across those destinations are
GENERATED install targets of the one source, not hand-maintained
duplicates — regeneration is expected to produce a clean `git diff`
(verified 2026-09-14). Never edit a shipped icon by hand; change the
script and regenerate.

## Provider logos

`fluidvoice/assets/providers/*.png` (and `-light` variants) are fetched
brand assets for the settings UI — not produced by this repo.
