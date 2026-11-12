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


def _mention_for(camp: dict, platform: str) -> list[str]:
    m = camp.get("mention") or []
    if isinstance(m, dict):  # different handle per platform
        m = m.get(platform) or m.get("default") or []
    m = [m] if isinstance(m, str) else list(m)
    return [x if x.startswith("@") else "@" + x for x in m if x]


def campaign_text(clip: dict, brand: Brand, platform: str = "") -> str:
    """Default:            [your caption] @mention
                           #Ad #tag #tag #tag   <- disclosure first, own line, max N more hashtags
    With first_line:       Full episode on @mention   <- tag in the FIRST line
                           [your caption]
                           #tag #tag #tag"""
    camp = clip["campaign"]
    caption = _strip_tags(pick_caption(clip, brand)).replace("\n", " ").strip()
    mentions = " ".join(_mention_for(camp, platform))
    disc = camp.get("disclosure")
    tags = [t for t in _dedupe_tags(camp.get("hashtags")) if not disc or t.lower() != disc.lower()]
    tags = tags[: int(camp.get("max_hashtags", 3))]
    tag_line = " ".join(([disc] if disc else []) + tags)
    if camp.get("first_line"):
        first = str(camp["first_line"]).replace("{mention}", mentions).strip()
        lines = [first, caption]
    else:
        lines = [" ".join(x for x in (caption, mentions) if x)]
    if tag_line:
        lines.append(tag_line)
    return "\n".join(lines)


def compose(clip: dict, brand: Brand, acc: Account) -> dict:
    if clip.get("campaign"):
        text = campaign_text(clip, brand, acc.platform)
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
