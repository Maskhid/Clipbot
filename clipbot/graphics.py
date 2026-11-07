"""Brand graphics drawn with Pillow and laid over the video by ffmpeg:
hook banner (rounded gradient pill), progress bar, divider and logo watermark."""
from __future__ import annotations

import re
from pathlib import Path

from .config import ASSETS_DIR

FONT = ASSETS_DIR / "fonts" / "Poppins-Bold.ttf"
DEFAULT_COLORS = ["#00F0FF", "#8A5CFF", "#FF2E93"]


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _gradient(w: int, h: int, colors: list[str]):
    from PIL import Image  # noqa: PLC0415

    cols = [_rgb(c) for c in colors] or [(255, 255, 255)]
    img = Image.new("RGBA", (w, h))
    px = img.load()
    n = len(cols) - 1
    for x in range(w):
        if n == 0:
            c = cols[0]
        else:
            t = x / max(1, w - 1) * n
            i = min(int(t), n - 1)
            f = t - i
            c = tuple(int(cols[i][k] + (cols[i + 1][k] - cols[i][k]) * f) for k in range(3))
        for y in range(h):
            px[x, y] = (*c, 255)
    return img


def _font(size: int):
    from PIL import ImageFont  # noqa: PLC0415

    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,!? ") + "…"
    return lines


def clean_hook(text: str) -> str:
    # keep letters, numbers and normal punctuation (the font has no emoji)
    t = re.sub(r"[^\w\s'’!?.,:&%$#@+\-]", "", text or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip().upper()


def hook_banner(text: str, out: Path, colors: list[str] | None = None, max_w: int = 940, size: int = 66) -> tuple[int, int] | None:
    """Rounded pill with brand gradient, white bold text with a soft shadow. Returns (w, h)."""
    from PIL import Image, ImageDraw, ImageFilter  # noqa: PLC0415

    text = clean_hook(text)
    if not text:
        return None
    font = _font(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    pad_x, pad_y, gap = 44, 26, 10
    lines = _wrap(probe, text, font, max_w - 2 * pad_x)
    asc, desc = font.getmetrics()
    line_h = asc + desc
    text_w = max(int(probe.textlength(line, font=font)) for line in lines)
    w = min(max_w, text_w + 2 * pad_x)
    h = len(lines) * line_h + (len(lines) - 1) * gap + 2 * pad_y
    margin = 24  # room for the drop shadow
    W, H = w + 2 * margin, h + 2 * margin
    radius = min(38, h // 2)

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([margin, margin + 8, margin + w, margin + h + 8], radius, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(12)))

    grad = _gradient(w, h, colors or DEFAULT_COLORS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pill.paste(grad, (0, 0), mask)
    # thin white rim for a crisp edge
    ImageDraw.Draw(pill).rounded_rectangle([1, 1, w - 2, h - 2], radius, outline=(255, 255, 255, 170), width=3)
    canvas.alpha_composite(pill, (margin, margin))

    d = ImageDraw.Draw(canvas)
    y = margin + pad_y
    for line in lines:
        lw = d.textlength(line, font=font)
        x = margin + (w - lw) / 2
        d.text((x + 3, y + 4), line, font=font, fill=(0, 0, 0, 120))
        d.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h + gap
    canvas.save(out)
    return W, H


def bar(out: Path, w: int, h: int, colors: list[str] | None = None):
    _gradient(w, h, colors or DEFAULT_COLORS).save(out)


def track(out: Path, w: int, h: int):
    from PIL import Image  # noqa: PLC0415

    Image.new("RGBA", (w, h), (0, 0, 0, 150)).save(out)


def watermark_badge(text: str, out: Path, logo: Path | None = None, size: int = 34) -> tuple[int, int] | None:
    """Small round logo + handle on a translucent dark pill."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    if not text and not (logo and logo.exists()):
        return None
    font = _font(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = int(probe.textlength(text, font=font)) if text else 0
    icon = 54
    has_logo = bool(logo and Path(logo).exists())
    pad = 12
    w = pad + (icon + 12 if has_logo else 8) + tw + pad + 6
    h = icon + 2 * 8
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], h // 2, fill=(0, 0, 0, 115))
    x = pad
    if has_logo:
        lg = Image.open(logo).convert("RGBA").resize((icon, icon), Image.LANCZOS)
        m = Image.new("L", (icon, icon), 0)
        ImageDraw.Draw(m).ellipse([0, 0, icon - 1, icon - 1], fill=255)
        img.paste(lg, (x - 4, 8), m)
        x += icon + 8
    if text:
        asc, desc = font.getmetrics()
        d.text((x, (h - asc - desc) / 2 - 1), text, font=font, fill=(255, 255, 255, 235))
    img.save(out)
    return w, h
