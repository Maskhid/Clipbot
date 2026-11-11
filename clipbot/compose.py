"""Turns a clip's text into each platform's post format."""
from __future__ import annotations

import re

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


def _strip_tags(text: str) -> str:
    """Campaign captions: hashtags/mentions are added in the required place, so drop any the AI wrote."""
    text = re.sub(r"(?<!\w)[#@][\w.]+", "", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip(" \n")


def campaign_text(clip: dict, brand: Brand) -> str:
    """[your caption] @mention
    #Ad #tag #tag #tag      <- disclosure first, on its own line, max N more hashtags"""
    camp = clip["campaign"]
    caption = _strip_tags(pick_caption(clip, brand))
    mentions = camp.get("mention") or []
    mentions = [mentions] if isinstance(mentions, str) else mentions
    first = " ".join([caption] + [m if m.startswith("@") else "@" + m for m in mentions]).strip()
    disc = camp.get("disclosure") or "#Ad"
    extra = [t for t in _dedupe_tags(camp.get("hashtags")) if t.lower() != disc.lower()][: int(camp.get("max_hashtags", 3))]
    return first + "\n" + " ".join([disc] + extra)


def compose(clip: dict, brand: Brand, acc: Account) -> dict:
    if clip.get("campaign"):
        text = campaign_text(clip, brand)
        title = _strip_tags(clip.get("title") or "") or text.split("\n")[0]
        if acc.platform == "youtube":
            return {"title": title[:100], "description": text, "tags": []}
        return {"title": title[:250], "text": text[:2150]}
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
