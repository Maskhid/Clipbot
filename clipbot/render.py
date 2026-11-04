"""Renders a vertical 1080x1920 clip with ffmpeg: layout + burned captions + loudness fix."""
from __future__ import annotations

import random
from pathlib import Path

from .config import ASSETS_DIR
from .util import log, need, run

W, H = 1080, 1920
FONTS_DIR = ASSETS_DIR / "fonts"


def _even(x: float) -> int:
    v = int(round(x))
    return v - (v % 2)


def _fesc(p: Path | str) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    s = str(p).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


def _dtext(t: str) -> str:
    return t.replace("\\", "").replace(":", r"\:").replace("'", "").replace("%", r"\%").replace(",", r"\,")


def font_file() -> Path | None:
    for name in ("Poppins-Bold.ttf", "Poppins-ExtraBold.ttf"):
        p = FONTS_DIR / name
        if p.exists():
            return p
    return None


def layout_filter(layout: str, src_w: int, src_h: int, cfg: dict) -> str:
    aspect = src_w / max(1, src_h)
    vertical = aspect <= (W / H) * 1.05
    if layout == "crop" or vertical:
        cx = float(cfg.get("crop_x", 0.5))
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:(iw-{W})*{cx}:(ih-{H})/2,setsar=1[v0]")
    if layout == "split":
        sp = cfg.get("split") or {}
        fx, fy, fw, fh = (sp.get("facecam") or [0, 0, 0.25, 0.25])
        top = _even(sp.get("top_height", 640))
        bot = H - top
        return (f"[0:v]split=2[a][b];"
                f"[a]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={W}:{top}:force_original_aspect_ratio=increase,crop={W}:{top}[top];"
                f"[b]scale=-2:{bot},crop={W}:{bot}[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[v0]")
    # blur (default): blurred full-screen background + zoomed gameplay strip in the middle
    zoom = float(cfg.get("fg_zoom", 1.25))
    fg_h = min(H, _even(W / aspect * zoom))
    return (f"[0:v]split=2[bgs][fgs];"
            f"[bgs]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
            f"scale={W}:{H},eq=brightness=-0.07:saturation=1.1[bg];"
            f"[fgs]scale=-2:{fg_h},crop='min(iw,{W})':{fg_h}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2-60,setsar=1[v0]")


def render_clip(src: Path, info: dict, start: float, end: float, ass: Path | None, out: Path,
                cfg: dict, watermark: str | None = None) -> Path:
    need("ffmpeg")
    dur = max(0.5, end - start)
    vf = layout_filter(cfg.get("layout", "blur"), info["width"], info["height"], cfg)
    chain = f"[v0]fps={int(cfg.get('fps', 30))}"
    if ass is not None:
        chain += f",ass='{_fesc(ass)}':fontsdir='{_fesc(FONTS_DIR)}'"
    if watermark:
        ff = font_file()
        fpart = f"fontfile='{_fesc(ff)}':" if ff else ""
        chain += (f",drawtext={fpart}text='{_dtext(watermark)}':fontsize=38:fontcolor=white@0.55:"
                  f"borderw=2:bordercolor=black@0.4:x=(w-tw)/2:y=h*0.905")
    chain += "[vout]"
    fc = vf + ";" + chain

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    if not info.get("has_audio"):
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a:0" if info.get("has_audio") else "1:a:0"]
    if info.get("has_audio") and cfg.get("loudnorm", True):
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.get("crf", 21)), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-map_metadata", "-1", "-shortest", str(out)]
    log.info("Rendering %s (%.1fs)", out.name, dur)
    run(cmd)
    return out


def make_variant(master: Path, out: Path, seed: str, vcfg: dict, watermark: str | None = None,
                 vary: bool = True) -> Path:
    """Per-account copy of a shared clip: tiny zoom/speed/colour changes + fresh encode and
    stripped metadata, so the same clip on several brands isn't a byte-identical file."""
    need("ffmpeg")
    rnd = random.Random(seed)
    z = rnd.uniform(*vcfg.get("zoom", [1.0, 1.06])) if vary else 1.0
    sp = rnd.uniform(*vcfg.get("speed", [1.0, 1.03])) if vary else 1.0
    sat = rnd.uniform(*vcfg.get("saturation", [0.94, 1.08])) if vary else 1.0
    br = rnd.uniform(*vcfg.get("brightness", [-0.03, 0.03])) if vary else 0.0
    zw, zh = _even(W * z), _even(H * z)
    vf = (f"scale={zw}:{zh},crop={W}:{H},eq=saturation={sat:.3f}:brightness={br:.3f},"
          f"setpts=PTS/{sp:.4f}")
    if watermark:
        ff = font_file()
        fpart = f"fontfile='{_fesc(ff)}':" if ff else ""
        vf += (f",drawtext={fpart}text='{_dtext(watermark)}':fontsize=38:fontcolor=white@0.55:"
               f"borderw=2:bordercolor=black@0.4:x=(w-tw)/2:y=h*0.905")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(master), "-vf", vf, "-af", f"atempo={sp:.4f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-map_metadata", "-1", str(out)]
    run(cmd)
    return out
