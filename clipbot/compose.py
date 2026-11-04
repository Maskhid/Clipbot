"""Turns a clip's text into each platform's post format."""
from __future__ import annotations

from .config import Account, Brand


def _dedupe_tags(*lists) -> list[str]:
    seen, out = set(), []
    for lst in lists:
        for t in lst or []:
            t = t.strip()
            if not t:
                continue
            if not t.startswith("#"):
                t = "#" + t
            k = t.lower()
            if k not in seen:
                seen.add(k)
                out.append(t)
    return out


def pick_caption(clip: dict, brand: Brand) -> str:
    caps = clip.get("captions") or {}
    style = brand.get("caption_style", "hype")
    if isinstance(caps, dict):
        return caps.get(style) or next(iter(caps.values()), "") or clip.get("title", "")
    return (caps[0] if caps else "") or clip.get("title", "")


def compose(clip: dict, brand: Brand, acc: Account) -> dict:
    caption = pick_caption(clip, brand).strip()
    tags = _dedupe_tags(clip.get("hashtags"), brand.get("hashtags"), acc.get("hashtags"))
    credit = clip.get("credit")
    credit_line = f"{brand.get('credit_prefix') or ''} {credit}".strip() if credit else ""
    title = (clip.get("title") or caption or "Clip").strip()

    if acc.platform == "youtube":
        yt_title = title[:100]
        if "#shorts" not in yt_title.lower() and len(yt_title) + 8 <= 100:
            yt_title += " #Shorts"
        desc = "\n\n".join(x for x in [caption, credit_line, " ".join(tags[:12])] if x)
        return {"title": yt_title, "description": desc[:4900],
                "tags": [t.lstrip("#") for t in tags][:15]}
    if acc.platform == "tiktok":
        text = " ".join(x for x in [caption, credit_line, " ".join(tags[:8])] if x)
        return {"text": text[:2150]}
    # instagram / facebook
    text = "\n\n".join(x for x in [caption, credit_line, " ".join(tags[:10])] if x)
    return {"title": title[:250], "text": text[:2150]}
