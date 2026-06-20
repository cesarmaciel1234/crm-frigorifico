"""Genera iconos PWA (180, 192, 512) para iPhone y Android."""
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    raise SystemExit("Instala Pillow: pip install pillow")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

# Elegant Gold and Black Theme
BRAND_GOLD = (212, 175, 55) # Metallic Gold
BG_DARK = (15, 23, 30)      # Very dark blue/black

def draw_icon(size: int) -> Image.Image:
    # Fondo oscuro
    img = Image.new("RGB", (size, size), BG_DARK)
    draw = ImageDraw.Draw(img)
    
    # Borde dorado sutil o gradiente simulado
    margin = size // 10
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size // 5,
        outline=BRAND_GOLD,
        width=max(2, size // 50)
    )
    
    text = "MT"
    font_size = int(size / 2.2)
    try:
        # Intentar cargar una fuente elegante o serif
        font = ImageFont.truetype("times.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("georgia.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
            
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    # Dibujar texto en dorado
    draw.text(((size - tw) / 2, (size - th) / 2 - size * 0.05), text, fill=BRAND_GOLD, font=font)
    return img


for px in (180, 192, 512):
    path = OUT / f"icon-{px}.png"
    draw_icon(px).save(path, "PNG")
    print(f"OK {path}")
