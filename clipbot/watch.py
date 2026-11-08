"""Streamer watch list: checks channels for new past broadcasts and queues them for clipping.

In config/sources.yaml:

channels:
  - url: "https://www.twitch.tv/kaicenat"
    brands: [peakgaminggt]
    credit: "@KaiCenat on Twitch"
    layout: split
    split: {facecam: [0.0, 0.0, 0.235, 0.23], top_height: 760}
    start_minute: 30      # skip the intro
    window_minutes: 120   # how much of each stream to scan for moments
    max_clips: 5          # best N moments per stream
    max_age_hours: 48     # ignore older streams
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .config import CONFIG_DIR, Config
from .state import State, utcnow
from .util import log

COPY_KEYS = ("only_gaming", "games", "brands", "distribute", "credit", "game", "title_hint", "layout", "split", "fg_zoom", "crop_x",
             "max_clips", "min_clip_seconds", "max_clip_seconds", "clips_per_hour")


def _listing_url(url: str) -> str:
    u = url.rstrip("/")
    m = re.match(r"https?://(www\.)?twitch\.tv/([^/?#]+)$", u)
    if m:
        return f"https://www.twitch.tv/{m.group(2)}/videos?filter=archives&sort=time"
    m = re.match(r"https?://(www\.)?kick\.com/([^/?#]+)$", u)
    if m:
        return f"https://kick.com/{m.group(2)}/videos"
    if re.match(r"https?://(www\.)?youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+)$", u):
        return u + "/streams"
    return url


def _hms(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def backlog_days(cfg: Config, state: State, brands: list[str]) -> float:
    """How many days of posts are already waiting for these brands."""
    results = state.results()
    pending: dict[str, int] = {}
    for j in state.jobs():
        if j["brand"] in brands and (results.get(j["id"]) or {}).get("status") not in ("posted", "failed"):
            pending[j["account"]] = pending.get(j["account"], 0) + 1
    days = 0.0
    for b in brands:
        for acc in cfg.brands[b].accounts:
            ppd = max(1, int(acc.get("posts_per_day", 3)))
            days = max(days, pending.get(acc.id, 0) / ppd)
    return days


def new_broadcasts(channel: dict, limit: int = 6, ydl_factory=None) -> list[dict]:
    """Recent finished broadcasts for a channel: [{url, title, timestamp, duration}] newest first."""
    if ydl_factory is None:
        import yt_dlp  # noqa: PLC0415
        ydl_factory = yt_dlp.YoutubeDL
    listing = _listing_url(channel["url"])
    out = []
    with ydl_factory({"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                      "playlistend": limit, "skip_download": True}) as ydl:
        info = ydl.extract_info(listing, download=False) or {}
    entries = [e for e in (info.get("entries") or []) if e][:limit]
    with ydl_factory({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        for e in entries:
            url = e.get("url") or e.get("webpage_url")
            if not url:
                continue
            if url.isdigit():  # twitch flat entries sometimes give just the id
                url = f"https://www.twitch.tv/videos/{url}"
            try:
                full = ydl.extract_info(url, download=False) or {}
            except Exception as ex:  # noqa: BLE001
                log.warning("watch: could not read %s (%s)", url, str(ex)[:150])
                continue
            if full.get("is_live") or full.get("live_status") in ("is_live", "is_upcoming"):
                continue  # still streaming; pick it up on a later run
            ts = full.get("timestamp") or full.get("release_timestamp")
            out.append({"url": full.get("webpage_url") or url, "title": full.get("title") or "",
                        "timestamp": ts, "duration": float(full.get("duration") or 0)})
    return out


def run_watch(cfg: Config, state: State | None = None, now: datetime | None = None, ydl_factory=None) -> list[str]:
    """Adds new broadcasts from watched channels to config/sources.yaml. Returns added URLs."""
    state = state or State()
    now = now or utcnow()
    channels = cfg.channels
    known = {s["url"] for s in cfg.sources}
    added: list[str] = []
    blocks: list[str] = []
    for ch in channels:
        if ch.get("enabled") is False:
            continue
        brands = ch.get("brands") or list(cfg.brands)
        days = backlog_days(cfg, state, brands)
        max_backlog = float(ch.get("max_backlog_days", 3))
        if days >= max_backlog:
            log.info("watch: %s skipped, %.1f days of posts already queued", ch["url"], days)
            continue
        try:
            vods = new_broadcasts(ch, ydl_factory=ydl_factory)
        except Exception as ex:  # noqa: BLE001
            log.warning("watch: %s failed (%s)", ch["url"], str(ex)[:200])
            continue
        max_age = timedelta(hours=float(ch.get("max_age_hours", 48)))
        for v in vods:
            if v["url"] in known:
                continue
            if v["timestamp"] and now - datetime.fromtimestamp(v["timestamp"], tz=timezone.utc) > max_age:
                continue
            dur = v["duration"]
            if dur and dur < 20 * 60:
                continue  # too short to be a real stream
            start = float(ch.get("start_minute", 30)) * 60
            if dur and start > dur * 0.5:
                start = dur * 0.1
            end = start + float(ch.get("window_minutes", 120)) * 60
            if dur:
                end = min(end, dur - 60)
            title = re.sub(r"[^\w ]", "", v["title"])[:60]
            lines = [f'  - url: "{v["url"]}"', f'    start: "{_hms(start)}"', f'    end: "{_hms(end)}"',
                     f"    added_by: watch  # {title}"]
            for k in COPY_KEYS:
                if k in ch:
                    val = ch[k]
                    if isinstance(val, bool):
                        val = "true" if val else "false"
                    elif isinstance(val, list):
                        val = "[" + ", ".join(str(x) for x in val) + "]"
                    elif isinstance(val, dict):
                        val = "{" + ", ".join(f"{a}: {('[' + ', '.join(str(x) for x in b) + ']') if isinstance(b, list) else b}"
                                              for a, b in val.items()) + "}"
                    elif isinstance(val, str):
                        val = f'"{val}"'
                    lines.append(f"    {k}: {val}")
            blocks.append("\n".join(lines))
            known.add(v["url"])
            added.append(v["url"])
            log.info("watch: new broadcast %s (%s)", v["url"], v["title"][:60])
            break  # one new stream per channel per run keeps the queue balanced
    if blocks:
        path = CONFIG_DIR / "sources.yaml"
        text = path.read_text(encoding="utf-8")
        marker = "\nchannels:"
        if marker in text:
            head, tail = text.split(marker, 1)
            text = head.rstrip("\n") + "\n" + "\n".join(blocks) + "\n" + marker + tail
        else:
            text = text.rstrip("\n") + "\n" + "\n".join(blocks) + "\n"
        path.write_text(text, encoding="utf-8")
    return added

