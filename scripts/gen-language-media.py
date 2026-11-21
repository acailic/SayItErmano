#!/usr/bin/env python3
"""Localized UI card for the "speaks your language" docs section.

Renders a small History-window mock whose every label comes from the
REAL sr-Latn catalog (set_language before drawing — no hardcoded
strings), the same offline-PIL approach as gen-doc-media.py: no X11,
no GTK, deterministic output.

Output: docs/screenshots/language-sr.png

Run:  python scripts/gen-language-media.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fluidvoice import i18n  # noqa: E402

OUT = ROOT / "docs" / "screenshots" / "language-sr.png"

W, H = 980, 560
BG_TOP = (19, 19, 25)
BG_BOT = (13, 13, 18)
WIN = (48, 44, 850, 470)
WIN_BG = (26, 26, 33)
WIN_BORDER = (45, 45, 56)
ROW = (33, 33, 41)
INK = (224, 224, 232)
INK_DIM = (150, 150, 162)
GOLD = (224, 176, 76)
GREEN = (46, 194, 126)

FONT_DIR = Path("/usr/share/fonts/truetype/liberation")


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    name = {"r": "LiberationSans-Regular.ttf",
            "b": "LiberationSans-Bold.ttf"}[kind]
    path = FONT_DIR / name
    if not path.exists():  # fall back to any FreeSans
        path = Path("/usr/share/fonts/truetype/freefont/FreeSans.ttf")
    return ImageFont.truetype(str(path), size)


def rounded(d, box, radius, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                        width=width)


def text(d, xy, s, f, fill=INK, anchor=None):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def main() -> int:
    i18n.set_language("sr-Latn")
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    # backdrop gradient
    for y in range(H):
        t = y / (H - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT))
        d.line([(0, y), (W, y)], fill=c)

    # window
    rounded(d, WIN, 14, fill=WIN_BG, outline=WIN_BORDER, width=1)
    # header bar
    d.line([(WIN[0], WIN[1] + 46), (WIN[2], WIN[1] + 46)], fill=WIN_BORDER)
    text(d, (WIN[0] + 20, WIN[1] + 13), "SayItErmano",
         font("b", 17), fill=INK)
    text(d, (WIN[0] + 20, WIN[1] + 30), i18n._("idle"), font("r", 11),
         fill=INK_DIM)
    # mic + menu glyph placeholders
    text(d, (WIN[2] - 26, WIN[1] + 13), "≡", font("r", 18), fill=INK_DIM)

    y = WIN[1] + 62
    # status line (real translations)
    status = " · ".join([
        i18n._("backend {name}").format(name="faster-whisper"),
        i18n._("GPU yes"),
        i18n._("model {name}").format(name="small"),
    ])
    text(d, (WIN[0] + 20, y), status, font("r", 11), fill=INK_DIM)
    d.ellipse([WIN[0] + 16, y + 2, WIN[0] + 22, y + 8], fill=GREEN)
    y += 22

    # search box
    rounded(d, (WIN[0] + 16, y, WIN[2] - 16, y + 30), 8,
            fill=(22, 22, 28), outline=WIN_BORDER, width=1)
    text(d, (WIN[0] + 28, y + 8),
         i18n._("Search transcripts, commands, apps…"),
         font("r", 11), fill=(105, 105, 118))
    y += 44

    # date header + two entry cards
    text(d, (WIN[0] + 20, y), i18n._("Today"), font("b", 11), fill=INK_DIM)
    y += 18
    entries = [
        ("13:07", "Možemo li sastanak pomeriti za utorak oko 4?",
         i18n._("AI polished")),
        ("12:41", "Podseti me u šest da kupim sladoled literal tačka",
         i18n._("Dictation mode")),
    ]
    for ts, body, tag in entries:
        rounded(d, (WIN[0] + 16, y, WIN[2] - 16, y + 64), 10, fill=ROW)
        text(d, (WIN[0] + 30, y + 10), ts, font("r", 10), fill=INK_DIM)
        # tag chip
        tw = d.textlength(tag, font=font("r", 9))
        rounded(d, (WIN[2] - 36 - tw - 14, y + 8, WIN[2] - 36, y + 24), 6,
                fill=(38, 44, 38))
        text(d, (WIN[2] - 36 - tw - 7, y + 12), tag, font("r", 9),
             fill=(126, 200, 156))
        text(d, (WIN[0] + 30, y + 28), body, font("r", 12), fill=INK)
        y += 64 + 10

    # language row (the Settings selector)
    rounded(d, (WIN[0] + 16, y, WIN[2] - 16, y + 40), 10, fill=ROW)
    text(d, (WIN[0] + 30, y + 6), i18n._("Language"), font("b", 11))
    text(d, (WIN[0] + 30, y + 21),
         i18n._("Interface language — follows the system by default"),
         font("r", 10), fill=INK_DIM)
    pill = i18n.LANGUAGES["sr-Latn"] + "  ▾"
    pw = d.textlength(pill, font=font("r", 11)) + 22
    rounded(d, (WIN[2] - 36 - pw, y + 10, WIN[2] - 36, y + 32), 7,
            fill=(35, 42, 56), outline=(60, 76, 108), width=1)
    text(d, (WIN[2] - 36 - pw + 11, y + 13), pill, font("r", 11), fill=GOLD)

    img.save(OUT, optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
