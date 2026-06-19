"""Genera iconos PWA (180, 192, 512) para iPhone y Android."""
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Instala Pillow: pip install pillow")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

BRAND = (37, 99, 235)
BG = (15, 23, 42)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)
    margin = size // 8
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size // 6,
        fill=BRAND,
    )
    text = "MT"
    font_size = size // 3
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - size * 0.02), text, fill="white", font=font)
    return img


for px in (180, 192, 512):
    path = OUT / f"icon-{px}.png"
    draw_icon(px).save(path, "PNG")
    print(f"OK {path}")
