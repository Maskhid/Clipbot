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


def _cookie_file() -> str | None:
    """YTDLP_COOKIES secret: the text of a Netscape cookies.txt (helps when YouTube says
    'Sign in to confirm you're not a bot' on GitHub's servers)."""
    txt = os.environ.get("YTDLP_COOKIES")
    if not txt:
        return None
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(txt)
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
        opts["force_keyframes_at_cuts"] = True

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
