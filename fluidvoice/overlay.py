"""Mac-style recording overlay (Linux port of the upstream macOS
BottomOverlayView): a bottom-center, always-on-top, never-focusable pill.

Solid black stadium with a subtle gloss border, the app icon, a live
waveform driven by the microphone level, and - once partial results exist -
a line of streaming transcription above the waveform row. While the final
pass runs the bars flatten and a shimmer sweeps across them, the same
choreography as on the Mac.

Rendering is pure Pillow so it is testable headless. The X11 side pushes
RGBA frames through a 32-bit visual when a compositor is available, falls
back to a shaped opaque window, and finally to replaceable desktop
notifications (NotifyPreview) - preserving the old graceful degradation.
"""
from __future__ import annotations

import array
import math
import os
import subprocess
import threading
import time
from pathlib import Path

# Mac pill spec (upstream LayoutConstants for .pill / .small)
PILL_H = 46
PILL_RADIUS = 23          # stadium: height/2
PAD_H = 12
PAD_V = 8
ICON_SIZE = 18
ICON_GAP = 8
BAR_COUNT = 8
BAR_W = 3.0
BAR_GAP = 2.5
BAR_MIN_H = 4
BAR_MAX_H = 28
BARS_WIDTH = BAR_COUNT * BAR_W + (BAR_COUNT - 1) * BAR_GAP  # 41.5
WAVE_W = 46
WAVE_H = 30
LABEL = "Dictate"
LABEL_SIZE = 10
TEXT_SIZE = 13
TEXT_LINE_H = 20
TEXT_GAP = 6
TEXT_RADIUS = 16          # rounded rect once preview text is shown
MAX_TEXT_W = 560
BOTTOM_OFFSET = 64        # px above the screen bottom edge
MAX_FRAME_BYTES = 800_000  # conservative XPutImage request cap

BAR_ALPHA = 224           # accent @ 0.88 while recording
BAR_FLAT_ALPHA = 82       # accent @ 0.32 while processing
LABEL_ALPHA = 217         # accent @ 0.85
TAIL_ALPHA = 132  # provisional live-tail ink (see _paint)
TEXT_ALPHA = 230          # white @ 0.9
BORDER_ALPHA_TOP = 76     # gloss: bright top fading to dim bottom
BORDER_ALPHA_BOTTOM = 26
SHIMMER_PERIOD = 1.05     # seconds per sweep (upstream CompositorShimmerSweep)

# -- hover action chips (A2, upstream overlay controls parity) ------------------
CHIP_STRIP = 44    # reserved above the pill while chips are shown
CHIP_H = 32
CHIP_GAP = 8       # chips row -> pill top
CHIP_PAD_H = 14    # text padding inside a chip
CHIP_FONT = 12

# fixed action order; labels stay ASCII-safe for the bitmap fallback fonts
CHIP_LABELS = {"copy_last": "Copy last", "paste_last": "Paste last",
               "cancel": "Cancel"}


def hit_chip(rects: dict, x: float, y: float) -> str | None:
    """Name of the chip whose rect contains the pointer, else None."""
    for name, (cx, cy, cw, ch) in rects.items():
        if cx <= x < cx + cw and cy <= y < cy + ch:
            return name
    return None


def edge_click(prev_mask: int, mask: int) -> bool:
    """True when Button1 transitions released->pressed (a real click,
    not a hold: auto-repeat never re-fires the press bit)."""
    b1 = 1 << 8
    return not (prev_mask & b1) and bool(mask & b1)
PROCESSING_CAP = 15.0     # hard auto-close so a hung pipeline never strands it
FADE_IN_FRAMES = 4        # ~130 ms at 30 fps: inside the 0.1 s "instant" band
DONE_HOLD = 0.45          # peak-end beat: success frame lingers, then fades
ELAPSED_AFTER = 2.0       # processing seconds before the label shows "· N s"
STILL_WORKING_AFTER = 4.0  # processing seconds before the label pulses

# Upstream NotchContentViews.OverlayMode.notchColor: dictation white,
# edit blue, command red - the pill accent (bars + label) follows the mode.
MODE_ACCENTS: dict[str, tuple[int, int, int]] = {
    "dictate": (255, 255, 255),
    "rewrite": (96, 165, 250),
    "command": (255, 99, 88),
}

# Done beat (peak-end, research §7): success reads green, low-confidence
# amber - honest ordinal cues (research §5), never red.
DONE_OK = (94, 214, 132)
DONE_LOW = (255, 186, 66)

# Per-state label (upstream processingLabel / "Listening..." header)
STATE_LABELS: dict[tuple[str, str], str] = {
    ("dictate", "recording"): "Dictate",
    ("dictate", "processing"): "Transcribing",
    ("dictate", "done"): "Done",
    ("rewrite", "recording"): "Rewrite",
    ("rewrite", "processing"): "Thinking",
    ("rewrite", "done"): "Done",
    ("command", "recording"): "Listening...",
    ("command", "processing"): "Working...",
    ("command", "confirm"): "Command",
    ("command", "done"): "Done",
}


def state_label(mode: str, state: str) -> str:
    return STATE_LABELS.get((mode, state),
                            STATE_LABELS[("dictate", "recording")])


def processing_label(base: str, elapsed: float | None) -> str:
    """Processing label with the elapsed cue: "Transcribing · 3 s" past
    2 s (research §1: unexplained waits feel longer; ≥4 s also pulses)."""
    if elapsed is None or elapsed < ELAPSED_AFTER:
        return base
    return f"{base} · {int(elapsed)} s"


_ANIMS_CACHE: bool | None = None


def _gsetting_animations() -> bool:
    """GNOME's enable-animations; unreadable => animations stay on."""
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface",
             "enable-animations"],
            capture_output=True, text=True, timeout=1.0).stdout.strip()
        return out != "false"
    except Exception:
        return True


def animations_enabled() -> bool:
    """Reduced-motion honor (research §8): SAYITERMANO_NO_ANIMATIONS wins,
    then GNOME's org.gnome.desktop.interface enable-animations, default on."""
    global _ANIMS_CACHE
    env = os.environ.get("SAYITERMANO_NO_ANIMATIONS")
    if env is not None:
        return env.strip().lower() not in ("1", "true", "yes", "on")
    if _ANIMS_CACHE is None:
        _ANIMS_CACHE = _gsetting_animations()
    return _ANIMS_CACHE

_FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/noto",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts",
)
_REGULAR = ("DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf")
_BOLD = ("DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "LiberationSans-Bold.ttf")


def _find_font(bold: bool) -> str | None:
    for d in _FONT_DIRS:
        for name in (_BOLD if bold else _REGULAR):
            p = Path(d) / name
            if p.exists():
                return str(p)
    return None


def _load_font(size: int, bold: bool):
    from PIL import ImageFont
    path = _find_font(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def head_truncate(draw, text: str, font, max_w: int) -> str:
    """Clip the HEAD of long streaming text: keep the newest words."""
    if draw.textlength(text, font=font) <= max_w or len(text) < 2:
        return text
    out = "…" + text[1:]
    while len(out) > 2 and draw.textlength(out, font=font) > max_w:
        out = "…" + out[2:]
    return out


def _load_default_icon():
    try:
        from importlib import resources
        ref = resources.files("fluidvoice.assets").joinpath("icon.png")
        with resources.as_file(ref) as p:
            return str(p)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Overlay size presets - ported 1:1 from the macOS LayoutConstants
# (pill / small / medium / large; upstream default is medium)
# ---------------------------------------------------------------------------


class SizeSpec:
    def __init__(self, name, pad_h, pad_v, wave_w, wave_h, icon,
                 bars, bar_w, bar_gap, bar_min, bar_max,
                 label_font, text_font, radius, max_w, text_lines):
        self.name = name
        self.pad_h = pad_h
        self.pad_v = pad_v
        self.wave_w = wave_w
        self.wave_h = wave_h
        self.icon = icon
        self.bars = bars
        self.bar_w = bar_w
        self.bar_gap = bar_gap
        self.bar_min = bar_min
        self.bar_max = bar_max
        self.label_font = label_font
        self.text_font = text_font
        self.radius = radius
        self.max_w = max_w
        self.text_lines = text_lines  # 0 = no streaming preview (pill)

    @property
    def line_h(self) -> int:
        return int(self.text_font * 1.5) if self.text_lines else 0


SIZE_SPECS: dict[str, SizeSpec] = {
    "pill": SizeSpec("pill", 12, 8, 46, 30, 18,
                     BAR_COUNT, BAR_W, BAR_GAP, BAR_MIN_H, BAR_MAX_H,
                     10, 10, PILL_RADIUS, 220, 0),
    "small": SizeSpec("small", 10, 6, 90, 20, 16,
                      7, 3.0, 3.5, 5, 16,
                      10, 11, 14, 280, 1),
    "medium": SizeSpec("medium", 18, 12, 130, 32, 20,
                       8, 3.5, 4.5, 6, 28,
                       12, 13, 18, 400, 2),
    "large": SizeSpec("large", 18, 12, 180, 48, 26,
                      11, 5.0, 6.0, 8, 44,
                      14, 15, 24, 620, 4),
}
DEFAULT_SIZE = "medium"


def wrap_lines(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    """Word-wrap streaming text; on overflow keep the NEWEST lines and mark
    the first kept line with a leading ellipsis (head truncation)."""
    if max_lines <= 0 or not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if not cur or draw.textlength(cand, font=font) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        first = lines[0]
        while len(first) > 2 and draw.textlength("…" + first, font=font) > max_w:
            first = first[2:]
        lines[0] = "…" + first
    return lines


class PillRenderer:
    """Renders pill frames as RGBA images (pure Pillow, no X11)."""

    SS = 2   # supersample factor for anti-aliasing
    MARGIN = 22  # transparent margin: room for the drop shadow (blur tail ~3*sigma)

    def __init__(self, icon_path: str | Path | None = None,
                 size: str = DEFAULT_SIZE, animations: bool = True):
        from PIL import Image
        self._Image = Image
        self.animations = animations
        self.spec = SIZE_SPECS.get(size, SIZE_SPECS[DEFAULT_SIZE])
        # fonts are painted on the supersampled canvas, so scale by SS and
        # divide textlength by SS wherever 1x geometry is needed
        self._label_font = _load_font(self.spec.label_font * self.SS, bold=True)
        self._text_font = _load_font(self.spec.text_font * self.SS, bold=False)
        self._icon = self._load_icon(icon_path or _load_default_icon())
        self._cache: dict = {}

    def _load_icon(self, icon_path):
        try:
            img = self._Image.open(icon_path).convert("RGBA")
        except Exception:
            return None
        return img.resize((self.spec.icon * self.SS, self.spec.icon * self.SS),
                          self._Image.LANCZOS)

    # -- geometry -----------------------------------------------------------

    def text_lines(self, text: str | None) -> list[str]:
        """Wrapped, head-truncated preview lines under the current spec."""
        if self.spec.text_lines == 0 or not text:
            return []
        from PIL import ImageDraw
        probe = self._Image.new("RGBA", (4, 4))
        d = ImageDraw.Draw(probe)
        max_w = (self.spec.max_w - 2 * self.spec.pad_h) * self.SS
        return wrap_lines(d, text, self._text_font, max_w,
                          self.spec.text_lines)

    def _row_width(self, label: str = LABEL,
                   badge: str | None = None) -> int:
        from PIL import ImageDraw
        probe = self._Image.new("RGBA", (4, 4))
        dd = ImageDraw.Draw(probe)
        label_w = dd.textlength(label, font=self._label_font) / self.SS
        w = 0
        if self._icon is not None:
            w += self.spec.icon + ICON_GAP
        w += self.spec.wave_w + 10 + int(label_w)
        if badge:
            w += 10 + int(dd.textlength(badge, font=self._label_font)
                          / self.SS)
        return w

    def inner_size(self, text: str | None, label: str = LABEL,
                   badge: str | None = None) -> tuple[int, int, int]:
        """(pill width, pill height, radius) without the shadow margin."""
        row_w = self._row_width(label, badge)
        lines = self.text_lines(text)
        spec = self.spec
        if not lines:
            h = 2 * spec.pad_v + spec.wave_h
            radius = h // 2 if spec.name == "pill" else spec.radius
            return max(row_w, spec.wave_w) + 2 * spec.pad_h, h, radius
        widest = 0
        from PIL import ImageDraw
        probe = self._Image.new("RGBA", (4, 4))
        d = ImageDraw.Draw(probe)
        for ln in lines:
            widest = max(widest, int(d.textlength(ln, font=self._text_font) / self.SS))
        inner_w = max(row_w, widest)
        h = spec.pad_v + len(lines) * spec.line_h + TEXT_GAP + spec.wave_h \
            + spec.pad_v
        return inner_w + 2 * spec.pad_h, h, spec.radius

    def measure(self, text: str | None) -> tuple[int, int]:
        """Outer size (pill + shadow margin) - also the X11 window size."""
        w, h, _ = self.inner_size(text)
        return w + 2 * self.MARGIN, h + 2 * self.MARGIN

    # -- painting -----------------------------------------------------------

    def render(self, levels, text: str | None = None, *, processing: bool = False,
               phase: float = 0.0, alpha: float = 1.0,
               mode: str = "dictate", state: str | None = None,
               badge: str | None = None, elapsed: float | None = None,
               confidence: int | None = None,
               stable_chars: int | None = None):
        """One frame -> (RGBA image, (w, h)).

        `mode` picks the accent color (dictate/rewrite/command); `state`
        ("recording"/"processing"/"confirm"/"done") picks the label -
        `processing` is the legacy shorthand for state="processing".
        `badge` is a short status chip (e.g. the spoken-send indicator)
        right of the label. `elapsed` (processing only) adds the "· N s"
        cue and the still-working pulse; `confidence` (done only) flips
        the success color from green to amber at band 0.
        """
        from PIL import Image
        if state is None:
            state = "processing" if processing else "recording"
        processing = state == "processing"
        done = state == "done"
        accent = MODE_ACCENTS.get(mode, MODE_ACCENTS["dictate"])
        if done:
            accent = DONE_LOW if confidence == 0 else DONE_OK
        label = state_label(mode, state)
        if processing:
            label = processing_label(label, elapsed)
        label_alpha = LABEL_ALPHA
        if processing:
            label_alpha = int(LABEL_ALPHA * 0.5)
            if (elapsed or 0.0) >= STILL_WORKING_AFTER and self.animations:
                cycle = (elapsed % 1.2) / 1.2  # slow "still working" pulse
                label_alpha = int(label_alpha * (0.55 + 0.45 *
                                                 (0.5 + 0.5 * math.sin(
                                                     2 * math.pi * cycle))))
        w, h, radius = self.inner_size(text, label, badge)
        ow = w + 2 * self.MARGIN
        oh = h + 2 * self.MARGIN

        frame = self._shadow_layer(ow, oh, radius)
        inner = Image.new("RGBA", (w * self.SS, h * self.SS), (0, 0, 0, 0))
        self._paint(inner, levels, text, state, phase, radius, accent, label,
                    badge, label_alpha, stable_chars)
        inner = inner.resize((w, h), Image.LANCZOS)
        frame.alpha_composite(inner, (self.MARGIN, self.MARGIN))
        if alpha < 1.0:
            frame.putalpha(frame.getchannel("A").point(lambda a: int(a * alpha)))
        return frame, (ow, oh)

    def _paint(self, im, levels, text, state, phase, radius, accent, label,
               badge=None, label_alpha=LABEL_ALPHA, stable_chars=None):
        from PIL import ImageDraw
        S = self.SS
        spec = self.spec
        processing = state == "processing"
        W, H = im.size
        d = ImageDraw.Draw(im)

        d.rounded_rectangle((0, 0, W - 1, H - 1), radius * S, fill=(0, 0, 0, 255))
        im.alpha_composite(self._gloss_border(W, H, radius))

        lines = self.text_lines(text)
        row_w = self._row_width(label, badge)
        text_block_h = len(lines) * spec.line_h + TEXT_GAP if lines else 0
        row_y = (H - spec.wave_h * S) // 2 if not lines \
            else (spec.pad_v * S) + text_block_h * S
        x = (W - row_w * S) // 2

        if self._icon is not None:
            icon_y = row_y + (spec.wave_h * S - spec.icon * S) // 2
            im.alpha_composite(self._rounded_icon(), (int(x), int(icon_y)))
            x += (spec.icon + ICON_GAP) * S

        bars_width = spec.bars * spec.bar_w + (spec.bars - 1) * spec.bar_gap
        self._paint_bars(d, x + (spec.wave_w - bars_width) / 2 * S, row_y,
                         levels, processing, phase, accent)
        x += spec.wave_w * S + 10 * S

        d.text((x, row_y + (spec.wave_h * S - spec.label_font * S) // 2 + S),
               label, font=self._label_font,
               fill=(*accent, label_alpha))
        if badge:
            bx = x + d.textlength(label, font=self._label_font) + 10 * S
            d.text((bx, row_y + (spec.wave_h * S - spec.label_font * S) // 2 + S),
                   badge, font=self._label_font,
                   fill=(*accent, 255 if not processing else 200))

        ty = spec.pad_v * S
        stable_left = None if stable_chars is None else int(stable_chars)
        for ln in lines:
            if stable_left is None or stable_left >= len(ln):
                d.text((spec.pad_h * S, ty), ln, font=self._text_font,
                       fill=(255, 255, 255, TEXT_ALPHA))
                if stable_left is not None:
                    stable_left -= len(ln) + 1  # +1 joined space
            elif stable_left <= 0:
                # uncommitted live tail: provisional ink until it commits
                # (CHI'23 text-stability finding - readers read the
                # volatile part differently when it LOOKS volatile)
                d.text((spec.pad_h * S, ty), ln, font=self._text_font,
                       fill=(255, 255, 255, TAIL_ALPHA))
            else:
                cut = ln.rfind(" ", 0, max(0, min(stable_left, len(ln))))
                cut = cut + 1 if cut > 0 else stable_left
                d.text((spec.pad_h * S, ty), ln[:cut], font=self._text_font,
                       fill=(255, 255, 255, TEXT_ALPHA))
                d.text((spec.pad_h * S
                        + d.textlength(ln[:cut], font=self._text_font), ty),
                       ln[cut:], font=self._text_font,
                       fill=(255, 255, 255, TAIL_ALPHA))
                stable_left = 0
            ty += spec.line_h * S

    def _gloss_border(self, W, H, radius):
        """Subtle gloss: brighter top edge fading toward the bottom."""
        from PIL import Image, ImageChops, ImageDraw
        key = ("gloss", W, H, radius)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        outline = Image.new("L", (W, H), 0)
        ImageDraw.Draw(outline).rounded_rectangle(
            (0, 0, W - 1, H - 1), radius * self.SS, outline=255,
            width=max(2, int(1.2 * self.SS)))
        grad = Image.linear_gradient("L").resize((W, H))
        scale = Image.new("L", (W, H), BORDER_ALPHA_TOP)
        scale.paste(Image.new("L", (W, H), BORDER_ALPHA_BOTTOM), (0, 0, W, H),
                    grad.point(lambda v: v))
        border = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        border.putalpha(ImageChops.multiply(outline, scale))
        self._cache[key] = border
        return border

    def _rounded_icon(self):
        from PIL import Image, ImageChops, ImageDraw
        key = "icon"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        size = self.spec.icon * self.SS
        r = int(size * 0.3)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), r,
                                               fill=255)
        out = self._icon.copy()
        out.putalpha(ImageChops.multiply(out.getchannel("A"), mask))
        self._cache[key] = out
        return out

    def _paint_bars(self, d, x, row_y, levels, processing, phase,
                    accent=(255, 255, 255)):
        S = self.SS
        spec = self.spec
        heights = list(levels) if levels else []
        heights += [spec.bar_min] * (spec.bars - len(heights))
        heights = [min(max(h, spec.bar_min), spec.bar_max)
                   for h in heights[:spec.bars]]
        sweep = (phase % SHIMMER_PERIOD) / SHIMMER_PERIOD  # 0..1 left..right
        for i, bh in enumerate(heights):
            bx = x + i * (spec.bar_w + spec.bar_gap) * S
            bh_px = bh * S
            y0 = row_y + (spec.wave_h * S - bh_px) / 2
            if processing:
                # flat bars; the shimmer sweep only runs when motion is allowed
                if self.animations:
                    center = (i + 0.5) / spec.bars
                    dist = abs(center - sweep)
                    boost = math.exp(-((dist / 0.16) ** 2))
                    a = int(BAR_FLAT_ALPHA + (255 - BAR_FLAT_ALPHA) * 0.9 * boost)
                else:
                    a = BAR_FLAT_ALPHA
            else:
                a = BAR_ALPHA
            d.rounded_rectangle(
                (bx, y0, bx + spec.bar_w * S - S * 0.4, y0 + bh_px),
                spec.bar_w * S / 2, fill=(*accent, a))

    # -- cached layers --------------------------------------------------------

    def pill_mask(self, text: str | None):
        """L-mode mask of the pill inside its margin (for X11 shape)."""
        from PIL import Image, ImageDraw
        w, h, radius = self.inner_size(text)
        key = ("mask", w, h, radius)
        cached = self._cache.get(key)
        if cached is None:
            S = 4
            im = Image.new("L", (w * S, h * S), 0)
            ImageDraw.Draw(im).rounded_rectangle(
                (0, 0, w * S - 1, h * S - 1), radius * S, fill=255)
            im = im.resize((w, h), Image.LANCZOS)
            full = Image.new("L", (w + 2 * self.MARGIN, h + 2 * self.MARGIN), 0)
            full.paste(im, (self.MARGIN, self.MARGIN))
            cached = self._cache[key] = full
        return cached

    def _shadow_layer(self, w: int, h: int, radius: int):
        """Soft drop shadow under the pill (cached per size)."""
        from PIL import Image, ImageDraw, ImageFilter
        key = ("shadow", w, h, radius)
        cached = self._cache.get(key)
        if cached is None:
            m = self.MARGIN
            pill = Image.new("L", (w, h), 0)
            ImageDraw.Draw(pill).rounded_rectangle(
                (m, m + 6, w - m - 1, h - m - 1), radius, fill=90)
            pill = pill.filter(ImageFilter.GaussianBlur(7))
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            layer.putalpha(pill)
            cached = self._cache[key] = layer
        return cached.copy()


class AudioLevels:
    """Maps recent PCM to per-bar heights: fast attack, slow release."""

    GAIN = 2.4

    def __init__(self, bars: int = BAR_COUNT, lo: float = BAR_MIN_H,
                 hi: float = BAR_MAX_H, rate: int = 16000):
        self.bars = bars
        self.lo = lo
        self.hi = hi
        self.rate = rate
        self._h = [lo] * bars

    def levels(self) -> list[float]:
        return list(self._h)

    def update(self, pcm_tail: bytes | None, chunk_ms: float = 55.0) -> None:
        chunk = int(self.rate * chunk_ms / 1000)  # samples per bar window
        targets = [self.lo] * self.bars
        if pcm_tail:
            need = chunk * self.bars * 2  # s16le mono
            data = pcm_tail[-need:]
            n_chunks = len(data) // (chunk * 2)
            for i in range(n_chunks):
                part = data[i * chunk * 2:(i + 1) * chunk * 2]
                samples = array.array("h")
                samples.frombytes(part)
                rms = math.sqrt(
                    sum(s * s for s in samples) / len(samples)) / 32768.0
                slot = self.bars - n_chunks + i  # newest window -> rightmost bar
                targets[slot] = self.lo + (self.hi - self.lo) * min(
                    1.0, math.sqrt(rms) * self.GAIN)
        for i in range(self.bars):
            t = targets[i]
            self._h[i] = t if t > self._h[i] else \
                self._h[i] * 0.70 + t * 0.30  # ~150 ms release at 30 fps


def confirmation_pill_text(command: str, purpose: str | None = None) -> str:
    """Text shown in the pill while a command proposal awaits confirmation."""
    lines = [f"$ {command}"]
    if purpose:
        lines.append(purpose)
    lines.append("hotkey = run · Esc = cancel")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command panel - Linux port of the upstream NotchCommandOutputExpandedView:
# a ~380 px black panel that shows the voice->shell conversation (instruction,
# proposed commands, results, summary) while the agent loop runs. Voice
# follow-ups work as before (command key starts a new take); the panel makes
# the loop visible instead of blind.
# ---------------------------------------------------------------------------

PANEL_W = 380
PANEL_RADIUS = 16
PANEL_PAD = 14
PANEL_HEADER_H = 30
PANEL_TEXT_SIZE = 12
PANEL_LINE_H = 17
PANEL_ENTRY_GAP = 7
PANEL_MAX_ENTRIES = 8
PANEL_HINT = "follow-up: command key · Esc cancels"

PANEL_FG = (235, 235, 235)
PANEL_DIM = (150, 150, 150)
PANEL_OK = (94, 214, 132)


class CommandPanelRenderer:
    """Renders command-conversation frames as RGBA images (pure Pillow)."""

    SS = 2    # supersample, same as the pill
    MARGIN = 22

    def __init__(self, width: int = PANEL_W):
        from PIL import Image
        self._Image = Image
        self.width = width
        self._font = _load_font(PANEL_TEXT_SIZE * self.SS, bold=False)
        self._font_bold = _load_font(PANEL_TEXT_SIZE * self.SS, bold=True)
        self._small = _load_font(10 * self.SS, bold=True)
        self.entries: list[dict] = []
        self.status: str | None = None
        self.awaiting: str | None = None   # pending "$ command" hint footer
        self._cache: dict = {}

    # -- content ---------------------------------------------------------

    def set_entries(self, entries: list[dict]) -> None:
        self.entries = list(entries)[-PANEL_MAX_ENTRIES:]

    def set_status(self, status: str | None) -> None:
        self.status = status

    def set_awaiting(self, command: str | None) -> None:
        self.awaiting = command

    # -- geometry ----------------------------------------------------------

    def _wrap(self, d, text: str, max_w: int, max_lines: int) -> list[str]:
        return wrap_lines(d, text, self._font, max_w, max_lines)

    def _body_lines(self, d) -> int:
        """Total painted line count + inter-entry gaps for current entries."""
        max_w = (self.width - 2 * PANEL_PAD - 14) * self.SS
        lines = 0
        for e in self.entries:
            lines += len(self._wrap(d, e.get("text", ""), max_w, 4))
            if e.get("sub"):
                lines += len(self._wrap(d, e["sub"], max_w, 2))
            lines += 1  # the inter-entry gap (PANEL_ENTRY_GAP) in line units
        return lines

    def inner_size(self, text: str | None = None) -> tuple[int, int, int]:
        from PIL import ImageDraw
        probe = ImageDraw.Draw(self._Image.new("RGBA", (4, 4)))
        lines = self._body_lines(probe)
        status_h = (PANEL_LINE_H + 2) if self.status else 0
        hint_h = 16 if self.awaiting else 0
        h = PANEL_PAD + PANEL_HEADER_H + PANEL_ENTRY_GAP \
            + lines * PANEL_LINE_H + status_h + hint_h + PANEL_PAD
        h = max(h, 90)
        return self.width, h, PANEL_RADIUS

    def measure(self, text: str | None = None) -> tuple[int, int]:
        w, h, _ = self.inner_size(text)
        return w + 2 * self.MARGIN, h + 2 * self.MARGIN

    # -- painting ----------------------------------------------------------

    def render(self, levels, text: str | None = None, *,
               processing: bool = False, phase: float = 0.0,
               alpha: float = 1.0, mode: str = "command",
               state: str | None = None, badge: str | None = None,
               elapsed: float | None = None, confidence: int | None = None):
        # badge/elapsed/confidence are pill-only: accepted so the shared
        # FluidOverlay fade path can render either renderer.
        from PIL import Image, ImageDraw
        accent = MODE_ACCENTS.get("command", (255, 99, 88))
        w, h, radius = self.inner_size()
        ow, oh = w + 2 * self.MARGIN, h + 2 * self.MARGIN
        frame = self._shadow_layer(ow, oh, radius)
        inner = Image.new("RGBA", (w * self.SS, h * self.SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(inner)
        S = self.SS

        d.rounded_rectangle((0, 0, w * S - 1, h * S - 1), radius * S,
                            fill=(0, 0, 0, 255))
        inner.alpha_composite(self._gloss_border(w * S, h * S, radius * S))

        # header: red dot + "Command" + right-aligned state/hint
        y = PANEL_PAD * S
        dot_r = 4 * S
        cy = y + PANEL_HEADER_H * S // 2
        live = state in ("recording", "processing")
        d.ellipse((PANEL_PAD * S, cy - dot_r, PANEL_PAD * S + 2 * dot_r,
                   cy + dot_r), fill=(*accent, 255 if live else 140))
        tx = (PANEL_PAD + 14) * S
        d.text((tx, cy - self._small.size // 2 - S), "Command",
               font=self._small, fill=(*accent, 230))
        right = "confirm?" if self.awaiting \
            else ("working" if self.status else "")
        rw = d.textlength(right, font=self._small)
        d.text((w * S - PANEL_PAD * S - rw, cy - self._small.size // 2 - S),
               right, font=self._small, fill=(*PANEL_DIM, 200))

        # body: newest at bottom, head-truncated wrap
        y = (PANEL_PAD + PANEL_HEADER_H + PANEL_ENTRY_GAP) * S
        max_w = (self.width - 2 * PANEL_PAD) * S
        line_h = PANEL_LINE_H * S
        for e in self.entries:
            kind = e.get("kind")
            danger = bool(e.get("destructive"))  # amber strong-confirm row
            dcol = (*DONE_LOW, 255) if danger else (*accent, 255)
            if kind == "user":
                d.text((PANEL_PAD * S, y), "»", font=self._font_bold,
                       fill=(*PANEL_FG, 235))
                x = (PANEL_PAD + 14) * S
                col = (*PANEL_FG, 235)
            elif kind == "proposal":
                d.text((PANEL_PAD * S, y), "⚠ $" if danger else "$",
                       font=self._font_bold, fill=dcol)
                x = (PANEL_PAD + 14) * S
                col = dcol
            elif kind == "ok":
                d.text((PANEL_PAD * S, y), "✓", font=self._font_bold,
                       fill=(*PANEL_OK, 235))
                x = (PANEL_PAD + 14) * S
                col = (*PANEL_DIM, 230)
            elif kind == "fail":
                d.text((PANEL_PAD * S, y), "✗", font=self._font_bold,
                       fill=(*accent, 255))
                x = (PANEL_PAD + 14) * S
                col = (*PANEL_DIM, 230)
            else:  # summary / note
                x = PANEL_PAD * S
                col = (*PANEL_FG, 220)
            for ln in self._wrap(d, e.get("text", ""), max_w - 14 * S, 4):
                d.text((x, y), ln, font=self._font, fill=col)
                y += line_h
            if e.get("sub"):
                for ln in self._wrap(d, e["sub"], max_w - 14 * S, 2):
                    d.text(((PANEL_PAD + 14) * S, y), ln, font=self._font,
                           fill=(*DONE_LOW, 230) if danger
                           else (*PANEL_DIM, 200))
                    y += line_h
            y += PANEL_ENTRY_GAP * S

        if self.status:
            d.text((PANEL_PAD * S, y), self.status, font=self._font,
                   fill=(*PANEL_DIM, 210))
            y += (PANEL_LINE_H + 2) * S
        if self.awaiting:
            d.text((PANEL_PAD * S, y + 2 * S), self.awaiting,
                   font=self._small, fill=(*accent, 220))

        inner = inner.resize((w, h), Image.LANCZOS)
        frame.alpha_composite(inner, (self.MARGIN, self.MARGIN))
        if alpha < 1.0:
            frame.putalpha(frame.getchannel("A").point(
                lambda a: int(a * alpha)))
        return frame, (ow, oh)

    # -- cached layers (same recipe as the pill) -----------------------------

    def pill_mask(self, text: str | None = None):
        from PIL import Image, ImageDraw
        w, h, radius = self.inner_size()
        S = 4
        im = Image.new("L", (w * S, h * S), 0)
        ImageDraw.Draw(im).rounded_rectangle(
            (0, 0, w * S - 1, h * S - 1), radius * S, fill=255)
        im = im.resize((w, h), Image.LANCZOS)
        full = Image.new("L", (w + 2 * self.MARGIN, h + 2 * self.MARGIN), 0)
        full.paste(im, (self.MARGIN, self.MARGIN))
        return full

    def _gloss_border(self, W, H, radius):
        from PIL import Image, ImageChops, ImageDraw
        outline = Image.new("L", (W, H), 0)
        ImageDraw.Draw(outline).rounded_rectangle(
            (0, 0, W - 1, H - 1), radius, outline=255,
            width=max(2, int(1.2 * self.SS)))
        grad = Image.linear_gradient("L").resize((W, H))
        scale = Image.new("L", (W, H), BORDER_ALPHA_TOP)
        scale.paste(Image.new("L", (W, H), BORDER_ALPHA_BOTTOM),
                    (0, 0, W, H), grad)
        border = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        border.putalpha(ImageChops.multiply(outline, scale))
        return border

    def _shadow_layer(self, w: int, h: int, radius: int):
        from PIL import Image, ImageDraw, ImageFilter
        m = self.MARGIN
        pill = Image.new("L", (w, h), 0)
        ImageDraw.Draw(pill).rounded_rectangle(
            (m, m + 6, w - m - 1, h - m - 1), radius, fill=90)
        pill = pill.filter(ImageFilter.GaussianBlur(7))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.putalpha(pill)
        return layer


class FluidOverlay:
    """The pill window. Falls back to NotifyPreview when X11/Pillow fail."""

    FPS = 30

    def __init__(self, display_name: str | None = None,
                 raw_path: Path | None = None,
                 bottom_offset: int = BOTTOM_OFFSET,
                 icon_path: str | Path | None = None,
                 size: str = DEFAULT_SIZE,
                 mode: str = "dictate",
                 actions: dict | None = None):
        from .preview import NotifyPreview
        self.fallback = NotifyPreview()
        self._actions = {k: v for k, v in (actions or {}).items()
                         if k in CHIP_LABELS and callable(v)}
        self._hovered = False          # pointer over the pill
        self._hover_chip: str | None = None
        self._hovered_prev = False
        self._prev_mask = 0
        self._chip_rects: dict = {}
        self._chips_ok = False         # set when the shape ext is available
        self._d = None
        self._win = None
        self._renderer = None
        self._raw_path = raw_path
        self._bottom_offset = bottom_offset
        self._size = size
        self._mode = mode if mode in MODE_ACCENTS else "dictate"
        self._badge: str | None = None
        self._text: str | None = None
        self._stable_chars: int | None = None
        self._state = "recording"
        self._state_since = time.monotonic()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._anims = animations_enabled()
        self._fade_left = 0  # fade-in frames remaining (0 = settled)
        self._done_confidence: int | None = None
        spec = SIZE_SPECS.get(size, SIZE_SPECS[DEFAULT_SIZE])
        self._levels = AudioLevels(bars=spec.bars, lo=spec.bar_min,
                                   hi=spec.bar_max)
        self._phase = 0.0
        self._last_sig: tuple | None = None
        try:
            self._setup(display_name, icon_path)
        except Exception:
            self._teardown_display()

    # -- X11 plumbing ---------------------------------------------------------

    def _setup(self, display_name: str | None, icon_path) -> None:
        from Xlib import X
        from Xlib.display import Display
        self._X = X
        self._d = Display(display_name)
        self._screen = self._d.screen()
        self._depth, self._visual_id, self._colormap = self._pick_visual()
        self._renderer = PillRenderer(icon_path, size=self._size,
                                      animations=self._anims)
        self._gc = self._scratch_gc()
        self._win_size = (0, 0)
        self._probe_shape_ext()

    def _pick_visual(self):
        X = self._X
        screen = self._screen
        # prefer a 32-bit TrueColor visual (compositor -> real alpha)
        for depth in screen.allowed_depths:
            if depth.depth == 32:
                for v in depth.visuals:
                    if v.visual_class == X.TrueColor:
                        cmap = screen.root.create_colormap(v.visual_id,
                                                           X.AllocNone)
                        return 32, v.visual_id, cmap
        return screen.root_depth, None, None

    def _scratch_gc(self):
        pm = self._screen.root.create_pixmap(1, 1, self._depth)
        gc = pm.create_gc(foreground=0, background=0)
        pm.free()
        return gc

    @property
    def using_overlay(self) -> bool:
        return self._d is not None

    # -- lifecycle --------------------------------------------------------------

    def start(self) -> None:
        if self._d is None or self._thread:
            return
        self._arm_fade_in()
        self._thread = threading.Thread(target=self._run, name="fluidvoice-overlay",
                                        daemon=True)
        self._thread.start()

    def _arm_fade_in(self) -> None:
        """Appear through a ~130 ms alpha ramp (skipped under reduced motion)."""
        if self._anims:
            self._fade_left = FADE_IN_FRAMES

    def show(self, text: str, stable_chars: int | None = None) -> None:
        if self._d is None:
            try:
                self.fallback.show(text)
            except TypeError:
                self.fallback.show(text, stable_chars)
            return
        with self._lock:
            self._text = text
            self._stable_chars = stable_chars
            self._last_sig = None  # force redraw + possible resize

    def set_mode(self, mode: str) -> None:
        """Switch the accent color + label family (dictate/rewrite/command)."""
        if mode not in MODE_ACCENTS:
            raise ValueError(f"unknown overlay mode {mode!r}")
        with self._lock:
            if self._mode != mode:
                self._mode = mode
                self._last_sig = None  # accent change -> repaint

    def set_badge(self, text: str | None) -> None:
        """Short right-of-label status chip (spoken-send indicator: 
        '⏎ sending…' / '⏎ sent')."""
        with self._lock:
            if self._badge != text:
                self._badge = text
                self._last_sig = None

    def set_state(self, state: str) -> None:
        """'recording' (bars follow audio), 'processing' (flat bars +
        shimmer), 'confirm' (static bars while the user decides whether
        to run the proposed command) or 'done' (success beat; the render
        loop holds it for DONE_HOLD, then fades itself out)."""
        if state not in ("recording", "processing", "confirm", "done"):
            raise ValueError(f"unknown overlay state {state!r}")
        with self._lock:
            # _state_since first: the render loop reads both unlocked, and
            # seeing the new state must never pair with a stale timestamp
            self._state_since = time.monotonic()
            self._state = state
            if state != "done":
                self._done_confidence = None
            self._last_sig = None
        if self._d is None:
            return  # notifications have no processing visual

    def finish(self, badge: str | None = None,
               confidence: int | None = None) -> None:
        """Peak-end done beat: show the success frame (badge "✓", green
        bars - amber when confidence band 0) for DONE_HOLD, then the loop
        fades and unmaps itself. Notification fallbacks just close - there
        is no frame to polish."""
        if self._d is None:
            self.close()
            return
        with self._lock:
            if badge is not None:
                self._badge = badge
            self._done_confidence = confidence
            self._state_since = time.monotonic()  # before _state: see below
            self._state = "done"
            self._last_sig = None

    def close(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._teardown_display()
        if self.fallback is not None:
            self.fallback.close()

    def _run(self) -> None:
        try:
            interval = 1.0 / self.FPS
            while not self._stop.is_set():
                t0 = time.monotonic()
                state = self._state
                now = time.monotonic()
                if state == "processing" and \
                        now - self._state_since > PROCESSING_CAP:
                    break
                if state == "done" and now - self._state_since > DONE_HOLD:
                    break  # done beat over -> fade below
                try:
                    self._tick(state)
                except Exception:
                    break  # display died; future show() goes to notifications
                delay = interval - (time.monotonic() - t0)
                self._stop.wait(max(0.002, delay))
            self._fade_out()  # Mac dismissal: quick fade, then unmap
        finally:
            self._teardown_display()

    def _fade_out(self) -> None:
        """Animate opacity down before unmapping (upstream scales+fades
        the overlay away over ~0.2 s); skipped under reduced motion."""
        if self._win is None or self._renderer is None or not self._anims:
            return
        for alpha in (0.7, 0.45, 0.2, 0.0):
            img, (w, h) = self._renderer.render(
                self._levels.levels(), self._text,
                phase=self._phase, alpha=alpha, mode=self._mode,
                state=self._state, badge=self._badge,
                confidence=(self._done_confidence
                            if self._state == "done" else None))
            try:
                self._blit(img, w, h, self._text)
            except Exception:
                break
            time.sleep(1.0 / self.FPS)  # bounded ~130 ms; not stop-gated

    # -- per-frame ------------------------------------------------------------

    def _tick(self, state: str) -> None:
        X = self._X
        with self._lock:
            text = self._text
            mode = self._mode
            badge = self._badge
            stable = self._stable_chars
        if state == "recording":
            self._levels.update(self._read_pcm_tail())
        self._phase += 1.0 / self.FPS

        # -- hover chips (A2): poll the pointer; no grabs, no focus.
        show_chips = (state == "recording" and self._actions
                      and self._chips_ok and self._update_hover())

        elapsed = (time.monotonic() - self._state_since
                   if state == "processing" else None)
        conf = self._done_confidence if state == "done" else None
        fade_alpha = 1.0
        if self._fade_left > 0:
            fade_alpha = (FADE_IN_FRAMES - self._fade_left + 1) / FADE_IN_FRAMES
            self._fade_left -= 1

        img, (w, h) = self._renderer.render(
            self._levels.levels(), text, phase=self._phase,
            alpha=fade_alpha, mode=mode, state=state, badge=badge,
            elapsed=elapsed, confidence=conf, stable_chars=stable)
        if show_chips:
            img, w, h = self._compose_chips(img, w, h)
        sig = (w, h, state, mode, text, badge,
               tuple(round(b, 1) for b in self._levels.levels()),
               round(self._phase % SHIMMER_PERIOD, 2),
               None if elapsed is None else int(elapsed),
               self._hover_chip if show_chips else None)
        if sig == self._last_sig and self._fade_left <= 0:
            return
        self._last_sig = sig

        if self._win is None or self._win_size != (w, h):
            self._create_window(w, h)
            self._apply_input_shape(show_chips)
        elif self._hovered_prev != show_chips:
            self._apply_input_shape(show_chips)
        self._hovered_prev = show_chips
        self._blit(img, w, h, text)

    # -- hover chips plumbing -------------------------------------------

    def _probe_shape_ext(self) -> None:
        """Chips need the X shape extension (input region): without it the
        transparent strip above the pill would steal clicks from the app
        underneath, so they stay off entirely."""
        try:
            d = self._d
            if hasattr(d, "has_extension"):
                ok = bool(d.has_extension("SHAPE"))
            else:  # older python-xlib: QueryExtension reply's present flag
                r = d.query_extension("SHAPE")
                data = getattr(r, "_data", None) or {}
                ok = bool(data.get("present") or getattr(r, "present", False))
            self._chips_ok = ok
        except Exception:
            self._chips_ok = False

    def _update_hover(self) -> bool:
        """Poll the pointer once: hovered over the pill (or a chip), and
        dispatch an edge-detected Button1 press on a chip as its action.
        Returns True while chips should be shown."""
        if self._win is None:
            return False
        try:
            q = self._win.query_pointer()
            # python-xlib versions differ on field exposure: resolved
            # requests carry a _data dict; some expose attributes directly
            data = getattr(q, "_data", None)
            if not isinstance(data, dict):
                data = {k: getattr(q, k) for k in
                        ("same_screen", "win_x", "win_y", "mask")}
            if not data.get("same_screen"):
                self._hovered = False
                self._hover_chip = None
                return False
            clicked = self._drain_button_events()
            mask = int(data.get("mask") or 0)
            x, y = data.get("win_x", -1), data.get("win_y", -1)
            w, h = self._win_size
        except Exception:
            return self._hovered
        # the whole window counts: the input shape (pill + chips strip)
        # already confines where the pointer can meaningfully be
        if not (0 <= x < w and 0 <= y < h):
            self._hovered = False
            self._hover_chip = None
            self._prev_mask = mask
            return False
        self._hovered = True
        self._hover_chip = hit_chip(self._chip_rects, x, y)
        if self._hover_chip and (clicked or edge_click(self._prev_mask, mask)):
            name, self._hover_chip = self._hover_chip, None
            self._fire_action(name)
        self._prev_mask = mask
        return True

    def _drain_button_events(self) -> bool:
        """We select ButtonPress to CONSUME clicks over the pill; drain the
        queue so the socket buffer never fills. Returns True when a real
        ButtonPress was delivered - a 30 Hz poll can miss a ~10 ms click,
        the event cannot be missed."""
        clicked = False
        try:
            while self._d.pending_events():
                ev = self._d.next_event()
                if getattr(ev, "type", None) == self._X.ButtonPress:
                    clicked = True
        except Exception:
            pass
        return clicked

    def _fire_action(self, name: str) -> None:
        cb = self._actions.get(name)
        if cb is None:
            return

        def _run():
            try:
                cb()
            except Exception:
                pass  # daemon-side actions log their own failures

        threading.Thread(target=_run, name="fluidvoice-overlay-chip",
                         daemon=True).start()

    def _compose_chips(self, pill_img, pw: int, ph: int):
        """Paste the pill frame under a row of action chips (pill stays
        put: the window grows upward, bottom-anchored)."""
        from PIL import Image, ImageDraw
        font = _load_font(CHIP_FONT, bold=True)
        names = [n for n in CHIP_LABELS if n in self._actions]
        widths = {}
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        for n in names:
            widths[n] = (CHIP_PAD_H * 2
                         + int(probe.textlength(CHIP_LABELS[n], font=font)))
        total = sum(widths.values()) + CHIP_GAP * max(0, len(names) - 1)
        w = max(pw, total + 2 * CHIP_GAP)
        h = CHIP_STRIP + ph
        frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        frame.paste(pill_img, ((w - pw) // 2, CHIP_STRIP))
        d = ImageDraw.Draw(frame)
        accent = MODE_ACCENTS.get(self._mode, MODE_ACCENTS["dictate"])
        x = (w - total) // 2
        y = (CHIP_STRIP - CHIP_GAP - CHIP_H) // 2
        self._chip_rects = {}
        for n in names:
            cw = widths[n]
            hot = n == self._hover_chip
            box = (x, y, x + cw - 1, y + CHIP_H - 1)
            if hot:  # inverted: accent fill, dark text
                d.rounded_rectangle(box, (CHIP_H - 4) // 2,
                                    fill=(*accent, 245),
                                    outline=(255, 255, 255, 90), width=1)
                tcol = (17, 17, 17, 255)
            else:    # dark chip, light text (pill family)
                d.rounded_rectangle(box, (CHIP_H - 4) // 2,
                                    fill=(24, 24, 24, 240),
                                    outline=(*accent, 200), width=1)
                tcol = (245, 245, 245, 235)
            tw = probe.textlength(CHIP_LABELS[n], font=font)
            d.text((x + (cw - tw) // 2, y + (CHIP_H - font.size) // 2 - 1),
                   CHIP_LABELS[n], font=font, fill=tcol)
            self._chip_rects[n] = (x, y, cw, CHIP_H)
            x += cw + CHIP_GAP
        return frame, w, h

    def _apply_input_shape(self, chips: bool) -> None:
        """Restrict pointer input to the pill (+ chips when shown) so the
        transparent shadow margins and the hover strip never steal clicks."""
        try:
            from Xlib.ext import shape as xshape
            w, h = self._win_size
            from PIL import Image, ImageDraw
            mask = Image.new("L", (w, h), 0)
            d = ImageDraw.Draw(mask)
            if chips:
                d.rectangle((0, CHIP_STRIP, w - 1, h - 1), fill=255)
                d.rectangle((0, 0, w - 1, CHIP_STRIP - CHIP_GAP - 1), fill=255)
            else:
                d.rectangle((0, 0, w - 1, h - 1), fill=255)
            bm = mask.point(lambda v: 255 if v > 127 else 0).convert("1")
            src_stride = (w + 7) // 8
            dst_stride = (w + 31) // 32 * 4
            raw = bm.tobytes("raw", "1")
            packed = bytearray(dst_stride * h)
            for yy in range(h):
                row = raw[yy * src_stride:(yy + 1) * src_stride]
                packed[yy * dst_stride:yy * dst_stride + len(row)] = row
            pm = self._screen.root.create_pixmap(w, h, 1)
            # XYBitmap put_image maps 1-bits -> foreground pixel: a depth-1
            # shape must store 1 where input is allowed, so fg=1 bg=0
            scratch1 = self._screen.root.create_pixmap(1, 1, 1)
            gc1 = scratch1.create_gc(foreground=1, background=0)
            scratch1.free()
            pm.put_image(gc1, 0, 0, w, h, self._X.XYBitmap, 1, 0,
                         bytes(packed))
            self._win.shape_mask(xshape.SO.Set, xshape.SK.Input, 0, 0, pm)
            pm.free()
            self._d.flush()
        except Exception:
            if chips:
                # cannot confine input: never show the strip
                self._chips_ok = False

    def _blit(self, img, w: int, h: int, text: str | None) -> None:
        """Push one frame into the (existing, correctly sized) window."""
        X = self._X
        data = img.tobytes("raw", "BGRA")
        if len(data) > MAX_FRAME_BYTES:
            scale = math.sqrt(MAX_FRAME_BYTES / len(data))
            from PIL import Image
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
            data = img.tobytes("raw", "BGRA")
        self._win.put_image(self._gc, 0, 0, img.width, img.height,
                            X.ZPixmap, self._depth, 0, data)
        if self._depth != 32:
            self._apply_shape(text)
        self._d.flush()

    def _read_pcm_tail(self) -> bytes | None:
        p = self._raw_path
        if p is None:
            return None
        try:
            with open(p, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                need = 16000 * 2 * 2  # last ~2 s is plenty for 8 bars
                fh.seek(max(0, size - need))
                return fh.read()
        except OSError:
            return None

    def _create_window(self, w: int, h: int) -> None:
        X = self._X
        screen = self._screen
        if self._win is not None:
            try:
                self._win.unmap()
                self._win.destroy()
                self._d.sync()
            except Exception:
                pass
            self._win = None
        x = (screen.width_in_pixels - w) // 2
        y = screen.height_in_pixels - h - self._bottom_offset
        kwargs = dict(override_redirect=True,
                      event_mask=X.ExposureMask | X.ButtonPressMask,
                      background_pixel=0,
                      border_pixel=0)
        if self._depth == 32:
            kwargs.update(visual=self._visual_id,
                          colormap=self._colormap)
        self._win = screen.root.create_window(
            max(0, x), max(0, y), w, h, 0, self._depth, **kwargs)
        self._win.map()
        self._d.flush()
        self._win_size = (w, h)
        self._last_sig = None

    def _apply_shape(self, text: str | None) -> None:
        """Opaque fallback: cut the rounded pill out of a rectangular window."""
        try:
            from Xlib.ext import shape as xshape
            mask = self._renderer.pill_mask(text)
            w, h = mask.size
            bm = mask.point(lambda v: 255 if v > 127 else 0).convert("1")
            src_stride = (w + 7) // 8
            dst_stride = (w + 31) // 32 * 4
            raw = bm.tobytes("raw", "1;MSB")
            packed = bytearray(dst_stride * h)
            for yy in range(h):
                row = raw[yy * src_stride:(yy + 1) * src_stride]
                packed[yy * dst_stride:yy * dst_stride + len(row)] = row
            pm = self._screen.root.create_pixmap(w, h, 1)
            pm.put_image(self._gc, 0, 0, w, h, self._X.XYBitmap, 1, 0,
                         bytes(packed))
            self._win.mask(xshape.SO.Set, xshape.SK.Bounding, 0, 0, pm)
            pm.free()
        except Exception:
            pass  # plain rectangle is the last resort; content is still right

    def _teardown_display(self) -> None:
        win, self._win = self._win, None
        d, self._d = self._d, None
        if win is not None and d is not None:
            try:
                win.unmap()
                d.sync()
            except Exception:
                pass
        if d is not None:
            try:
                d.close()
            except Exception:
                pass


class CommandPanel(FluidOverlay):
    """The command-conversation window: FluidOverlay's X11 plumbing with a
    structured-content renderer. Voice follow-ups stay on the command key."""

    FPS = 12  # static content + a slow "Working..." pulse is plenty

    def __init__(self, display_name: str | None = None, **kw):
        kw.setdefault("mode", "command")
        super().__init__(display_name, **kw)
        self._entries: list[dict] = []
        self._status: str | None = None
        self._awaiting: str | None = None
        if self._d is not None:
            self._renderer = CommandPanelRenderer()

    # content API (thread-safe through the overlay lock)

    def update(self, entries: list[dict], status: str | None = None,
               awaiting: str | None = None) -> None:
        with self._lock:
            self._entries = list(entries)[-PANEL_MAX_ENTRIES:]
            self._status = status
            self._awaiting = awaiting
            self._last_sig = None

    def show(self, text: str) -> None:  # pill API not used by the panel
        pass

    def _tick(self, state: str) -> None:
        X = self._X
        with self._lock:
            entries, status, awaiting = (self._entries, self._status,
                                         self._awaiting)
        if self._renderer is None:
            return
        self._renderer.set_entries(entries)
        self._renderer.set_status(status)
        self._renderer.set_awaiting(awaiting)
        self._phase += 1.0 / self.FPS
        blink = self._anims and bool(int(self._phase * 2) % 2)
        pulse = "Working..." if (status and blink) else (status or "")
        self._renderer.set_status(pulse)
        img, (w, h) = self._renderer.render(
            None, state="confirm")
        sig = (w, h, state, tuple(map(str, entries)), status, awaiting,
               round(self._phase, 1))
        if sig == self._last_sig:
            return
        self._last_sig = sig
        if self._win is None or self._win_size != (w, h):
            self._create_window(w, h)
        self._blit(img, w, h, None)
