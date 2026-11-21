#!/usr/bin/env python3
"""Apply the sr-Latn hand-written catalog and derive sr / bs.

One-shot helper, kept for provenance and re-runs after catalog changes:

- sr-Latn: po/sr_Latn_translations.py (hand-written) applied verbatim.
- sr (Cyrillic): Serbian Latin -> Cyrillic transliteration of sr-Latn
  (lossless by orthography; placeholders, `backtick` spans and
  technical tokens stay Latin). Standard practice — the Serbian l10n
  team maintains sr@Latn as a script conversion of sr.
- bs: sr-Latn adapted with ijekavica + Bosnian lexical replacements
  and per-string overrides for idioms that don't map word-for-word.

Run from the repo root:
  python po/apply_translations.py
then rebuild the catalogs:
  python scripts/build_translations.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "po"))

from extract_translations import (  # noqa: E402
    PO_DIR,
    catalog_files,
    extract,
    lang_header,
    render,
)
from sr_Latn_translations import LATIN  # noqa: E402

# -- sr-Latn: apply the hand-written catalog -----------------------------------

def apply_latin() -> dict[str, dict]:
    catalog = extract(catalog_files())
    plurals = LATIN.pop("_PLURALS", {})
    missing = [m for m in catalog
               if m not in LATIN and m not in plurals]
    extra = [m for m in LATIN if m not in catalog]
    plural_extra = [m for m in plurals if m not in catalog]
    if plural_extra:
        extra += plural_extra
    if missing or extra:
        raise SystemExit(f"catalog mismatch — missing: {missing[:5]!r} "
                         f"extra: {extra[:5]!r}")
    entries = {}
    for msgid, e in catalog.items():
        if msgid in plurals:
            forms = plurals[msgid]
            entries[msgid] = {**e, "msgstrs": {0: forms[0], 1: forms[1],
                                               2: forms[2]}}
        else:
            entries[msgid] = {**e, "msgstrs": {0: LATIN[msgid]}}
    body = render(entries, lang_header("sr-Latn"))
    (PO_DIR / "sr-Latn.po").write_text(body, encoding="utf-8")
    return entries


# -- sr: Latin -> Cyrillic transliteration --------------------------------------

# Latin-only spans: masked (kept verbatim) before transliteration.
# Token = a maximal run of ASCII/technical chars or Serbian diacritic
# letters. A token is KEPT Latin when it looks technical (contains
# . : _ digit, starts with / or ~) or is a curated brand/command word;
# everything else (Serbian words, ASCII or with diacritics) is
# transliterated.
_MASK_TOKEN = r"[A-Za-zčćšđžČĆŠĐŽ0-9_.:/+=]*"
MASK_RE = re.compile(
    r"\{[^{}]*\}"                # {placeholders}
    r"|`[^`]*`"                  # `command spans`
    r"|</?[a-z]+>"               # inline markup (<b>, </b>, …)
    fr"|{_MASK_TOKEN}"
)

#: curated Latin-kept words that appear in the sr-Latn catalog (brands,
#: tool names, command fragments). Anything not listed and not
#: technically-shaped gets transliterated.
KEEP_LATIN = {
    "AI", "GPU", "API", "URL", "GGUF", "ONNX", "RAM", "VRAM", "CUDA",
    "MPRIS", "JSON", "WAV", "GLM", "vLLM", "NIM", "DGX", "Spark", "LM",
    "Studio", "Mac", "macOS", "X11", "Wayland", "GNOME", "KDE", "wlroots",
    "Ollama", "OpenAI", "Groq", "Parakeet", "faster", "whisper", "GitHub",
    "SayItErmano", "sayit", "ermano", "systemctl", "restart", "Translated",
    "bluez", "wtype", "ydotool", "ydotoold", "xclip", "xdotool", "paplay",
    "pipewire", "utils", "python", "evdev", "update", "toggle", "doctor",
    "qwen", "ggml", "bin", "http", "localhost", "lan", "box", "sudo",
    "apt", "install", "bearer", "token", "daemon", "Daemon", "STT", "TDT",
    "Esc", "Enter", "Tab", "auto", "en", "sl", "ctrl", "v", "pw", "record",
    "px", "wl", "copy", "paste", "clipboard", "Whisper", "hotwords",
    "hotword", "initial", "prompt", "server",
}

_LAT2CYR = {
    "Dž": "Џ", "DŽ": "Џ", "dž": "џ",
    "Lj": "Љ", "LJ": "Љ", "lj": "љ",
    "Nj": "Њ", "NJ": "Њ", "nj": "њ",
    "a": "а", "b": "б", "c": "ц", "č": "ч", "ć": "ћ", "d": "д", "đ": "ђ",
    "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "ј", "k": "к",
    "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с",
    "š": "ш", "t": "т", "u": "у", "v": "в", "z": "з", "ž": "ж",
    "A": "А", "B": "Б", "C": "Ц", "Č": "Ч", "Ć": "Ћ", "D": "Д", "Đ": "Ђ",
    "E": "Е", "F": "Ф", "G": "Г", "H": "Х", "I": "И", "J": "Ј", "K": "К",
    "L": "Л", "M": "М", "N": "Н", "O": "О", "P": "П", "R": "Р", "S": "С",
    "Š": "Ш", "T": "Т", "U": "У", "V": "В", "Z": "З", "Ž": "Ж",
}

_DIACRITICS = set("čćšđžČĆŠĐŽ")
# Serbian abbreviations that carry a trailing period: transliterate
# despite the "." keep-trigger
_ABBREV = {"npr.": "нпр.", "itd.": "итд.", "tj.": "тј."}


def _translit_token(text: str) -> str:
    for src in ("Dž", "DŽ", "dž", "Lj", "LJ", "lj", "Nj", "NJ", "nj"):
        text = text.replace(src, _LAT2CYR[src])
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        out.append(_LAT2CYR.get(ch, ch))
        i += 1
    return "".join(out)


def _keep_token(token: str) -> bool:
    if not token:
        return False
    if token in _ABBREV:
        return False
    if _diacritic_free := not (_DIACRITICS & set(token)):
        # Serbian words never carry . : _ / = digits and stay lowercase
        # compounds; technical tokens do (paths, urls, code fragments)
        if any(c in token for c in "._:/=+" + "0123456789"):
            return True
        if token.startswith(("/", "~")):
            return True
        if len(token) > 1 and token.isupper():
            return True  # AI, GGUF, LM, STT …
    return token in KEEP_LATIN


def to_cyrillic(text: str) -> str:
    for src, dst in _ABBREV.items():
        text = text.replace(src, dst)
    pieces: list[str] = []
    masks: list[str] = []
    idx = 0
    for m in MASK_RE.finditer(text):
        token = m.group(0)
        pieces.append(_translit_token(text[idx:m.start()]))
        if m.group(0).startswith(("{", "`", "<")) or _keep_token(token):
            masks.append(token)
            pieces.append(f"\x00{len(masks) - 1}\x00")
        else:
            pieces.append(_translit_token(token))
        idx = m.end()
    pieces.append(_translit_token(text[idx:]))
    out = "".join(pieces)
    return re.sub("\x00(\\d+)\x00", lambda m: masks[int(m.group(1))], out)


def make_cyrillic(latin_entries: dict[str, dict]) -> None:
    entries = {}
    for msgid, e in latin_entries.items():
        strs = {k: to_cyrillic(v) for k, v in e["msgstrs"].items()}
        entries[msgid] = {**e, "msgstrs": strs}
    body = render(entries, lang_header("sr"))
    (PO_DIR / "sr.po").write_text(body, encoding="utf-8")


# -- bs: ijekavica + lexical adaptation of sr-Latn ------------------------------

# word-boundary, case-sensitive Serbian -> Bosnian replacements
BS_WORDS = [
    ("gde", "gdje"), ("Gde", "Gdje"),
    ("ovde", "ovdje"), ("Ovde", "Ovdje"),
    ("uvek", "uvijek"), ("Uvek", "Uvijek"),
    ("vreme", "vrijeme"), ("Vreme", "Vrijeme"),
    ("vrednost", "vrijednost"), ("vrednosti", "vrijednosti"),
    ("opšte", "opće"), ("Opšte", "Opće"),
    ("obaveštenja", "obavještenja"), ("Obaveštenja", "Obavještenja"),
    ("obaveštenje", "obavještenje"), ("Obaveštava", "Obavještava"),
    ("lepo", "lijepo"), ("Lepo", "Lijepo"),
    ("mesec", "mjesec"), ("meseca", "mjeseca"),
    ("rečnik", "rječnik"), ("Rečnik", "Rječnik"), ("rečnika", "rječnika"),
    ("reči", "riječi"), ("reč", "riječ"), ("Reči", "Riječi"),
    ("hteo", "htio"), ("htela", "htjela"),
    ("uopšte", "uopće"),
    ("proverite", "provjerite"), ("Proverite", "Provjerite"),
    ("provera", "provjera"), ("Provera", "Provjera"), ("Proveri", "Provjeri"),
    ("provere", "provjere"), ("proverava", "provjerava"),
    ("proveravam", "provjeravam"), ("Proveravam", "Provjeravam"),
    ("podrazumevano", "zadano"), ("Podrazumevano", "Zadano"),
    ("podrazumevani", "zadani"), ("podrazumevanog", "zadanog"),
    ("sačuvaj", "spremi"), ("Sačuvaj", "Spremi"),
    ("sačuvane", "spremljene"), ("Sačuvane", "Spremljene"),
    ("sačuvana", "spremljena"), ("Sačuvana", "Spremljena"),
    ("sačuvano", "spremljeno"), ("Sačuvano", "Spremljeno"),
    ("sačuvan", "spremljen"), ("Sačuvan", "Spremljen"),
    ("sačuva", "spremi"), ("sačuvaju", "spremaju"), ("sačuvati", "spremiti"),
    ("nesačuvane", "nespremljene"), ("Nesačuvane", "Nespremljene"),
    ("čuvanje", "spremanje"), ("Čuvanje", "Spremanje"),
    ("čuvaj", "spremaj"), ("Čuvaj", "Spremaj"), ("čuva se", "sprema se"),
    ("čuvaju", "spremaju"), ("čuva", "sprema"),
    ("nalepi", "zalijepi"), ("Nalepi", "Zalijepi"),
    ("nalepite", "zalijepite"), ("nalepljivanje", "lijepljenje"),
    ("nalep", "zalijep"),
    ("zapetama", "zarezima"),
    ("pregledača", "preglednika"), ("pregledač", "preglednik"),
    ("juče", "jučer"), ("Juče", "Jučer"),
    ("prosek", "prosjek"), ("Prosek", "Prosjek"),
    ("ceo", "cio"), ("cela", "cijela"), ("celu", "cijelu"),
    ("celi", "cijeli"), ("celom", "cijelom"), ("celog", "cijelog"),
    ("otud", "odatle"),
]

# stem replacements (no trailing word boundary): inflected forms
BS_STEMS = [
    ("istorij", "historij"), ("Istorij", "Historij"),
    ("obavešt", "obavješt"),
    ("izmen", "izmjen"), ("Izmen", "Izmjen"),
    ("promen", "promjen"), ("Promen", "Promjen"),
    ("primenj", "primjenj"), ("Primenj", "Primjenj"),
    ("prover", "provjer"), ("Prover", "Provjer"),
    ("poslednj", "posljednj"), ("Poslednj", "Posljednj"),
    ("sledeć", "sljedeć"), ("Sledeć", "Sljedeć"),
]

# uspe- forms shift vowel by vowel, not by stem: word-level only
BS_WORDS += [
    ("uspela", "uspjela"), ("uspelo", "uspio"), ("uspeo", "uspio"),
    ("uspeva", "uspjeva"), ("uspeh", "uspjeh"),
    ("neuspeha", "neuspjeha"), ("neuspela", "neuspjela"),
    ("neuspelo", "neuspjelo"), ("Uspelo", "Uspio"),
    ("Reč", "Riječ"), ("Reči", "Riječi"), ("Rečice", "Riječice"),
    ("rečice", "riječice"),
]

# per-string overrides where word mapping reads wrong
BS_OVERRIDES = {
    "Postavke": "Postavke",
    "Otvori postavke": "Otvori postavke",
    "postavkama": "postavkama",
    "Kontrolni soket": "Kontrolni priključak",
    "Copy transcriptions to clipboard": "Kopiraj transkripcije u međuspremnik",
    "Clipboard": "Međuspremnik",
    "No clipboard tool found (install xclip)":
        "Nije nađen alat za međuspremnik (instalirajte xclip)",
    "install the clipboard tool: sudo apt install xclip":
        "instalirajte alat za međuspremnik: sudo apt install xclip",
    "hover the pill while recording for copy/paste/cancel buttons (X11)":
        "pređite mišem preko balončića tokom snimanja za dugmad "
        "kopiraj/zalijepi/otkaži (X11)",
    "um, uh, hmm… before punctuation": "hm, e, eee… prije interpunkcije",
    "That's the whole loop — press the key whenever you want to dictate. "
    "Every take is saved in History if you ever need it again.":
        "To je cijeli krug — pritisnite taster kad god želite da "
        "diktirate. Svaki snimak se sprema u historiju ako vam ikad "
        "zatreba ponovo.",
    "install the typing tool: sudo apt install xdotool (text goes to "
    "History meanwhile - copy it from there)":
        "instalirajte alat za kucanje: sudo apt install xdotool (tekst "
        "u međuvremenu ide u historiju - kopirajte odatle)",
    "every dictation is saved in History - copy or re-insert text from "
    "there any time":
        "svako diktiranje se sprema u historiju - kopirajte ili ponovo "
        "umetnite tekst odatle u svako doba",
    "Wayland: text reaches the clipboard (wl-copy) but nothing can press "
    "ctrl+v - install ydotool to type into apps, or paste manually after "
    "each dictation":
        "Wayland: tekst dolazi u međuspremnik (wl-copy) ali ništa ne može "
        "pritisnuti ctrl+v - instalirajte ydotool da kucate u aplikacije, "
        "ili ručno zalijepite poslije svakog diktiranja",
    "Wayland: no typing tool and no wl-clipboard - install ydotool (needs "
    "ydotoold running and /dev/uinput access) or wl-clipboard for manual "
    "paste":
        "Wayland: nema alata za kucanje ni wl-clipboard - instalirajte "
        "ydotool (zahtijeva pokrenutog ydotoold i pristup /dev/uinput) ili "
        "wl-clipboard za ručno lijepljenje",
}


def to_bosnian(text: str) -> str:
    for src, dst in BS_STEMS:
        text = re.sub(rf"\b{re.escape(src)}", dst, text)
    for src, dst in BS_WORDS:
        text = re.sub(rf"\b{re.escape(src)}\b", dst, text)
    return text


def make_bosnian(latin_entries: dict[str, dict]) -> None:
    entries = {}
    for msgid, e in latin_entries.items():
        if msgid in BS_OVERRIDES:
            strs = {k: BS_OVERRIDES[msgid] for k in e["msgstrs"]}
        else:
            strs = {k: to_bosnian(v) for k, v in e["msgstrs"].items()}
        entries[msgid] = {**e, "msgstrs": strs}
    body = render(entries, lang_header("bs"))
    (PO_DIR / "bs.po").write_text(body, encoding="utf-8")


def main() -> None:
    latin_entries = apply_latin()
    make_cyrillic(latin_entries)
    make_bosnian(latin_entries)
    print("po/sr-Latn.po, po/sr.po, po/bs.po written")


if __name__ == "__main__":
    main()
