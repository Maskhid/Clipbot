"""Fetches a source video: YouTube / Twitch / Kick VODs and most sites (via yt-dlp),
direct .mp4 links (Dropbox ?dl=1, etc.), or a local file path."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from .util import log, need, run


def _hms(t) -> float | None:
    if t is None or t == "":
        return None
    if isinstance(t, (int, float)):
        return float(t)
    parts = [float(p) for p in str(t).split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def clean_cookies(txt: str) -> tuple[str, int]:
    """Repairs a pasted cookies.txt: copy/paste through a browser or text box often turns
    tabs into spaces, adds Windows line endings or drops the header line, and yt-dlp then
    says 'failed to load cookies'. Returns (clean Netscape text, number of cookies)."""
    out, n = ["# Netscape HTTP Cookie File"], 0
    for raw in txt.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue  # comments / old header
        parts = line.split("\t") if "\t" in line else line.split()
        parts = [p for p in parts if p != ""] if "\t" not in line else parts
        if len(parts) < 7:
            continue
        if len(parts) > 7:  # a value that contained spaces
            parts = parts[:6] + [" ".join(parts[6:])]
        domain, sub, path, secure, expiry = parts[:5]
        if sub.upper() not in ("TRUE", "FALSE") or secure.upper() not in ("TRUE", "FALSE"):
            continue
        if not expiry.split(".")[0].lstrip("-").isdigit():
            continue
        out.append("\t".join([domain, sub.upper(), path, secure.upper(), expiry.split(".")[0], parts[5], parts[6]]))
        n += 1
    return "\n".join(out) + "\n", n


def _cookie_file() -> str | None:
    """YTDLP_COOKIES secret: the text of a Netscape cookies.txt (helps when YouTube says
    'Sign in to confirm you're not a bot' on GitHub's servers)."""
    txt = os.environ.get("YTDLP_COOKIES")
    if not txt:
        return None
    clean, n = clean_cookies(txt)
    if n == 0:
        log.warning("YTDLP_COOKIES is set but no valid cookie lines were found in it; ignoring it. "
                    "Re-export with 'Get cookies.txt LOCALLY' (Export, Netscape format) and paste the whole file.")
        return None
    log.info("Using %d YouTube cookies from YTDLP_COOKIES", n)
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(clean)
    return path


def fetch(src: dict, work: Path) -> Path:
    url = src["url"]
    work.mkdir(parents=True, exist_ok=True)
    start, end = _hms(src.get("start")), _hms(src.get("end"))

    local = Path(os.path.expanduser(url))
    if local.exists():
        out = work / f"source{local.suffix or '.mp4'}"
        if start is not None or end is not None:
            need("ffmpeg")
            cmd = ["ffmpeg", "-y", "-v", "error"]
            if start is not None:
                cmd += ["-ss", str(start)]
            if end is not None:
                cmd += ["-to", str(end)]
            cmd += ["-i", str(local), "-c", "copy", str(work / "source.mp4")]
            run(cmd)
            return work / "source.mp4"
        shutil.copy2(local, out)
        return out

    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("yt-dlp is not installed: pip install yt-dlp") from e

    opts = {
        "outtmpl": str(work / "source.%(ext)s"),
        "format": "bv*[height<=1080][vcodec!*=av01]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
    }
    cookies = _cookie_file()
    if cookies:
        opts["cookiefile"] = cookies
    if start is not None or end is not None:
        from yt_dlp.utils import download_range_func  # noqa: PLC0415
        opts["download_ranges"] = download_range_func(None, [(start or 0, end or float("inf"))])
        # no force_keyframes_at_cuts: it re-encodes the whole section and made downloads take ~50 min.
        # Cuts land on the nearest keyframe (a few seconds early), which is fine because clips are cut precisely later.

    log.info("Downloading %s", url)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(work.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if not re.search(r"\.(part|ytdl|json)$", f.name)]
    if not files:
        raise RuntimeError(f"Download produced no file for {url}")
    src["_title"] = (info or {}).get("title")
    src["_uploader"] = (info or {}).get("uploader")
    return files[0]
