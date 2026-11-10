# gen-glm-logo.py — one-off GLM provider mark (rounded square + wordmark),
# normal black-on-transparent plus a white "-light" recolor for dark panels,
# matching the assets/providers convention. Regenerate with:
#   .venv/bin/python tools/gen-glm-logo.py
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "fluidvoice" / "assets" / "providers"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

for dark, suffix in ((False, ""), (True, "-light")):
    img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (255, 255, 255, 255) if dark else (15, 15, 15, 255)
    d.rounded_rectangle([6, 6, 122, 122], radius=30, outline=color, width=7)
    d.text((64, 64), "GLM", font=ImageFont.truetype(FONT, 46),
           fill=color, anchor="mm")
    img.save(OUT / f"glm{suffix}.png")
    print(f"wrote {OUT / f'glm{suffix}.png'}")
