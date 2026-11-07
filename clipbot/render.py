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


SCALE = "flags=lanczos"


def layout_filter(layout: str, src_w: int, src_h: int, cfg: dict) -> tuple[str, dict]:
    """Returns (filtergraph producing [v0], geometry used to place the graphics)."""
    aspect = src_w / max(1, src_h)
    vertical = aspect <= (W / H) * 1.05
    if layout == "crop" or vertical:
        cx = float(cfg.get("crop_x", 0.5))
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:{SCALE},"
                f"crop={W}:{H}:(iw-{W})*{cx}:(ih-{H})/2,unsharp=5:5:0.4,setsar=1[v0]",
                {"seam": int(H * 0.80), "hook_center": int(H * 0.16)})
    if layout == "split":
        sp = cfg.get("split") or {}
        fx, fy, fw, fh = (sp.get("facecam") or [0, 0, 0.25, 0.25])
        top = _even(sp.get("top_height", 640))
        bot = H - top
        return (f"[0:v]split=2[a][b];"
                f"[a]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},scale={W}:{top}:force_original_aspect_ratio=increase:{SCALE},"
                f"crop={W}:{top},unsharp=5:5:0.9:3:3:0[top];"
                f"[b]scale=-2:{bot}:{SCALE},crop={W}:{bot},unsharp=5:5:0.5[bot];"
                f"[top][bot]vstack=inputs=2,setsar=1[v0]",
                {"seam": top, "hook_center": top})
    # blur (default): blurred full-screen background + zoomed gameplay strip in the middle
    zoom = float(cfg.get("fg_zoom", 1.25))
    fg_h = min(H, _even(W / aspect * zoom))
    fg_top = (H - fg_h) // 2 - 60
    return (f"[0:v]split=2[bgs][fgs];"
            f"[bgs]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
            f"scale={W}:{H},eq=brightness=-0.07:saturation=1.1[bg];"
            f"[fgs]scale=-2:{fg_h}:{SCALE},crop='min(iw,{W})':{fg_h},unsharp=5:5:0.5[fg];"
            f"[bg][fg]overlay=(W-w)/2:{fg_top},setsar=1[v0]",
            {"seam": fg_top + fg_h, "hook_center": max(200, fg_top - 120)})


def render_clip(src: Path, info: dict, start: float, end: float, ass: Path | None, out: Path,
                cfg: dict, watermark: str | None = None, hook: str | None = None,
                colors: list[str] | None = None, logo: Path | None = None) -> Path:
    """Vertical clip with: layout, burned captions, gradient hook pill (first seconds),
    progress bar along the seam, logo + handle badge, loudness normalisation."""
    from . import graphics  # noqa: PLC0415

    need("ffmpeg")
    dur = max(0.5, end - start)
    gcfg = cfg.get("graphics") or {}
    colors = colors or gcfg.get("colors") or graphics.DEFAULT_COLORS
    vf, geo = layout_filter(cfg.get("layout", "blur"), info["width"], info["height"], cfg)
    fps = cfg.get("fps", "auto")
    fps = min(60, round(info.get("fps") or 30)) if fps in ("auto", None) else int(fps)
    work = out.parent

    steps: list[str] = []
    cur = "v0"

    images: list[Path] = []

    chain = f"[{cur}]fps={fps}"
    if ass is not None:
        chain += f",ass='{_fesc(ass)}':fontsdir='{_fesc(FONTS_DIR)}'"
    chain += "[vc]"
    steps.append(chain)
    cur = "vc"

    if gcfg.get("progress_bar", True):
        bh = 10
        tp, bp = work / f"{out.stem}_track.png", work / f"{out.stem}_bar.png"
        graphics.track(tp, W, bh)
        graphics.bar(bp, W, bh, colors)
        images += [tp, bp]
        y = max(0, geo["seam"] - bh // 2)
        steps.append(f"[{cur}][IMG{len(images) - 2}]overlay=0:{y}[vt]")
        steps.append(f"[vt][IMG{len(images) - 1}]overlay=x='-w+w*t/{dur:.3f}':y={y}[vp]")
        cur = "vp"

    hook_s = float(cfg.get("hook_seconds", 3.0))
    if hook and hook_s > 0 and gcfg.get("hook_style", "pill") == "pill":
        hp = work / f"{out.stem}_hook.png"
        size = graphics.hook_banner(hook, hp, colors)
        if size:
            images.append(hp)
            hw, hh = size
            hy = max(0, min(H - hh, geo["hook_center"] - hh // 2))
            idx = len(images) - 1
            fade_out = max(0.2, min(hook_s, dur) - 0.3)
            steps.append(f"[IMG{idx}]format=rgba,fade=t=in:st=0:d=0.25:alpha=1,"
                         f"fade=t=out:st={fade_out:.2f}:d=0.3:alpha=1[hk]")
            steps.append(f"[{cur}][hk]overlay=(W-w)/2:{hy}:enable='lt(t,{min(hook_s, dur):.2f})'[vh]")
            cur = "vh"

    if watermark or (logo and Path(logo).exists()):
        wp = work / f"{out.stem}_wm.png"
        size = graphics.watermark_badge(watermark or "", wp, logo)
        if size:
            images.append(wp)
            ww, wh = size
            wy = 150  # below the platforms' top bar, clear of the caption area
            steps.append(f"[{cur}][IMG{len(images) - 1}]overlay=W-w-28:{wy}[vw]")
            cur = "vw"

    steps.append(f"[{cur}]null[vout]")
    fc = vf + ";" + ";".join(steps)

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    first_img = 1
    if not info.get("has_audio"):
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        first_img = 2
    for p in images:
        cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(p)]
    for i in range(len(images)):
        fc = fc.replace(f"[IMG{i}]", f"[{first_img + i}:v]")
    cmd += ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a:0" if info.get("has_audio") else "1:a:0"]
    if info.get("has_audio") and cfg.get("loudnorm", True):
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    cmd += ["-c:v", "libx264", "-preset", str(cfg.get("render_preset", "medium")), "-crf", str(cfg.get("crf", 18)),
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-map_metadata", "-1", "-t", f"{dur:.3f}", str(out)]
    log.info("Rendering %s (%.1fs, %dfps)", out.name, dur, fps)
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
