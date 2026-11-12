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


VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".mxf", ".webm", ".avi")


def _is_direct_file(url: str) -> bool:
    """A plain link to a video file (GitHub release asset, Frame.io/Dropbox/S3 download link...)."""
    from urllib.parse import urlparse  # noqa: PLC0415

    return url.startswith("http") and urlparse(url).path.lower().endswith(VIDEO_EXT)


def _direct_title(url: str) -> str | None:
    from urllib.parse import parse_qs, unquote, urlparse  # noqa: PLC0415

    u = urlparse(url)
    disp = parse_qs(u.query).get("response-content-disposition", [""])[0]
    m = re.search(r'filename="([^"]+)"', disp)
    name = m.group(1) if m else unquote(u.path.rsplit("/", 1)[-1])
    name = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name).replace("_", " ").strip()
    return name if name and name.lower() not in ("original", "video", "file") else None


def _fetch_direct(src: dict, url: str, work: Path, start, end) -> Path:
    """Stream a direct video link through ffmpeg into a 1080p H.264 working copy.
    Big studio files (4K, ProRes .mov) are shrunk on the fly, so the original never has to fit on disk."""
    import json as _json  # noqa: PLC0415

    need("ffmpeg")
    net = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1", "-reconnect_delay_max", "30",
           "-rw_timeout", "60000000"]
    probe = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                 "-show_entries", "format=duration", "-of", "json", url], timeout=300)
    meta = _json.loads(probe.stdout or "{}")
    v = next((x for x in meta.get("streams", []) if x.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError(f"No video stream found at {url[:80]}...")
    small = v.get("codec_name") in ("h264",) and int(v.get("height") or 9999) <= 1080 and int(v.get("width") or 9999) <= 1920
    out = work / "source.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", *net]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", url, "-map", "0:v:0", "-map", "0:a:0?"]
    if small:
        cmd += ["-c", "copy"]
    else:
        cmd += ["-vf", "scale='if(gt(a,16/9),min(1920,iw),-2)':'if(gt(a,16/9),-2,min(1080,ih))':flags=lanczos,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-movflags", "+faststart", str(out)]
    dur = float((meta.get("format") or {}).get("duration") or 0)
    log.info("Downloading direct file (%s %sx%s, %.0f min) -> %s", v.get("codec_name"), v.get("width"), v.get("height"),
             dur / 60, "copy" if small else "1080p H.264")
    try:
        run(cmd)
    except RuntimeError as e:  # server without range support etc. -> download the whole file first, then convert
        log.warning("Streaming read failed (%s); downloading the whole file first", str(e).splitlines()[-1][:120])
        import requests  # noqa: PLC0415

        raw = work / ("raw" + (Path(url.split("?")[0]).suffix or ".mp4"))
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(raw, "wb") as f:
                for chunk in r.iter_content(8 << 20):
                    f.write(chunk)
        cmd2 = cmd[:4] + cmd[4 + len(net):]  # same command minus the network options
        cmd2[cmd2.index(url)] = str(raw)
        run(cmd2)
        raw.unlink(missing_ok=True)
    src["_title"] = src.get("_title") or _direct_title(url)
    return out


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

    if _is_direct_file(url) or src.get("direct"):
        return _fetch_direct(src, url, work, start, end)

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
