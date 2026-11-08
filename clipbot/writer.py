"""Titles, hooks and captions.

With an LLM key (free tiers: Google Gemini, or Groq/OpenRouter via the OpenAI-compatible
option) the agent picks the best moments and writes the text. Without one, it falls back
to a built-in writer, so nothing breaks.
"""
from __future__ import annotations

import json
import os
import random
import re

import requests

from .highlights import transcript_slice
from .util import log

STYLE_TEMPLATES = {
    "hype": [
        "{hook} 😳🔥", "bro really said \"{quote}\" 💀", "{hook}… wait for it 👀", "this is why we play {game} 🔥",
        "{hook} 🤯", "nobody was ready for this 😭",
    ],
    "funny": [
        "\"{quote}\" 💀💀", "{hook} 😭", "I can't stop laughing at this 🤣", "the timing 💀", "{hook} LMAO",
    ],
    "chill": [
        "{hook}.", "one of the best moments from today's stream", "\"{quote}\"", "{game} moments 🎮",
    ],
    "news": [
        "{hook} — {game} clip", "{game}: {hook}", "Clip: \"{quote}\"",
    ],
}

FILLER = re.compile(r"^(um+|uh+|like|so|and|but|yeah|okay|ok|oh)\b[\s,]*", re.I)


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    for _ in range(3):
        t = FILLER.sub("", t).strip()
    return t


def _best_sentence(segments: list[dict], start: float, end: float, peak: float) -> str:
    best, bd = "", 1e9
    for s in segments:
        if s["end"] > start and s["start"] < end:
            d = abs((s["start"] + s["end"]) / 2 - peak)
            if d < bd and len(s["text"].split()) >= 2:
                best, bd = s["text"], d
    return _clean(best)


def heuristic_text(clip: dict, segments: list[dict], src: dict, styles: list[str], game: str) -> dict:
    rnd = random.Random(f"{clip['start']}-{clip['end']}")
    quote = _best_sentence(segments, clip["start"], clip["end"], clip.get("peak", clip["start"]))
    words = quote.split()
    quote_short = " ".join(words[:10]) + ("…" if len(words) > 10 else "")
    hook = " ".join(words[:5]).rstrip(",.!?") if words else ""
    if not hook:
        hook = rnd.choice(["WAIT FOR IT", "NO WAY", "THIS IS INSANE", "WATCH THIS"])
    title_base = src.get("title_hint") or game or "Gaming"
    title = f"{quote_short or hook} | {title_base}" if quote_short else f"{hook} | {title_base}"
    captions = []
    for st in styles:
        tpl = rnd.choice(STYLE_TEMPLATES.get(st, STYLE_TEMPLATES["hype"]))
        captions.append(tpl.format(hook=hook.lower() if st != "news" else hook, quote=quote_short or hook, game=game or "this game"))
    # without the AI writer we don't guess a hook: a vague banner hurts more than none
    return {"title": title[:95], "hook": "", "captions": captions, "hashtags": []}


# ---------------------------------------------------------------- LLM -----

PROMPT = """You are an expert short-form video editor for gaming clips (TikTok, YouTube Shorts, Reels).
Below are candidate moments from one long video, detected automatically with a loudness+hype score.
Game / topic: {game}
Video title: {vtitle}
Write in: {language}

Pick the {n} candidates most likely to go viral (funny, shocking, skillful, emotional, or a clear payoff).
Skip boring or confusing moments, and moments that need context to understand.
For each pick you may tighten start/end by a few seconds so it opens on action and ends right after the payoff,
but keep start/end inside the candidate's range and the length between {mn} and {mx} seconds.

Return ONLY JSON like:
{{"clips":[{{"idx":0,"start":12.3,"end":41.0,
  "title":"YouTube Shorts title, max 70 chars, no hashtags, curiosity-driven",
  "hook":"on-screen hook, max 6 words, that says PRECISELY what happens in THIS clip (use the frames and transcript), e.g. COPS BOX HIM IN AT THE DOCKS. Empty string if you can't be precise",
  "captions":[{caption_spec}],
  "hashtags":["#gta6","#..."]}}]}}

Rules for the hook: it must be specific to what is seen or said in that clip (who/what/where), never generic
filler like "WATCH THIS", "YO BRO", "NO WAY" or a quote of the first words. If nothing precise can be said, use "".
Titles must be specific too. No clickbait lies, no slurs, don't claim to be the streamer, max 6 hashtags.
Streamer: {streamer}
Each candidate may come with 3 frames (start, peak, end) so you can see what happens.

Candidates:
{cands}
"""


def _call_gemini(model: str, key: str, prompt: str, images: list[tuple[str, bytes]] | None = None) -> str:
    import base64  # noqa: PLC0415

    parts: list[dict] = [{"text": prompt}]
    for label, jpg in images or []:
        parts.append({"text": label})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpg).decode()}})
    import time  # noqa: PLC0415

    last = None
    for m in dict.fromkeys([model, "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        for attempt in range(3):
            try:
                r = requests.post(url, params={"key": key}, timeout=180, json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
                })
            except requests.RequestException as e:
                last = f"{m}: {e}"
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code == 200:
                log.info("AI writer: Gemini model %s answered", m)
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            last = f"{m}: HTTP {r.status_code} {r.text[:200]}"
            if r.status_code == 404:  # model name retired -> next model
                break
            if r.status_code in (429, 500, 502, 503, 504):  # busy -> wait, retry, then next model
                log.info("AI writer: %s busy (HTTP %s), retrying", m, r.status_code)
                time.sleep(15 * (attempt + 1))
                continue
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError(last or "no Gemini model available")


GENERIC = {"WATCH THIS", "WAIT FOR IT", "NO WAY", "YO", "BRO", "OMG", "WHAT", "THIS IS INSANE", "INSANE"}


def _precise_hook(h) -> str:
    h = re.sub(r"\s+", " ", str(h or "")).strip().upper()[:48]
    if not h or h.strip(".!? ") in GENERIC or len(h.split()) < 2:
        return ""
    return h


def _call_openai_compat(base_url: str, model: str, key: str, prompt: str) -> str:
    r = requests.post(base_url.rstrip("/") + "/chat/completions", timeout=120,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "temperature": 0.8,
                            "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def _providers(lc: dict) -> list[tuple[str, str, str, str]]:
    """(name, model, base_url, key) in the order to try: the configured one, then Groq as backup."""
    out = []
    prov = (lc.get("provider") or "none").lower()
    key = os.environ.get(lc.get("api_key_env") or "LLM_API_KEY", "").strip()
    if prov != "none" and key:
        out.append((prov, os.environ.get("LLM_MODEL") or lc.get("model") or "gemini-2.5-flash",
                    lc.get("base_url") or "", key))
    groq = os.environ.get("GROQ_API_KEY", "").strip()
    if groq:
        out.append(("openai_compatible", lc.get("backup_model") or "llama-3.3-70b-versatile",
                    "https://api.groq.com/openai/v1", groq))
    return out


def llm_pick(cands: list[dict], segments: list[dict], src: dict, n: int, styles: list[str],
             settings: dict, game: str, frames: dict[int, list[bytes]] | None = None) -> list[dict] | None:
    lc = settings["llm"]
    providers = _providers(lc)
    if not providers:
        if (lc.get("provider") or "none").lower() != "none":
            log.warning("AI writer: provider=%s but the %s secret is empty -> no AI hooks",
                        lc.get("provider"), lc.get("api_key_env") or "LLM_API_KEY")
        else:
            log.info("AI writer: off (llm.provider: none)")
        return None
    cc = settings["clipping"]
    lines = []
    for i, c in enumerate(cands):
        txt = transcript_slice(segments, c["start"], c["end"])[:700] or "(no speech: gameplay/sound only)"
        lines.append(f"[{i}] {c['start']:.1f}-{c['end']:.1f}s (score {c['score']:.2f}): {txt}")
    caption_spec = ", ".join(f'"caption in a {s} style, max 150 chars, 1-3 emojis"' for s in styles)
    streamer = (src.get("credit") or src.get("_uploader") or "the streamer").replace("on Twitch", "").strip()
    prompt = PROMPT.format(game=game or "gaming", vtitle=src.get("_title") or src.get("title_hint") or "",
                           language=lc.get("language") or "English", n=n, mn=cc["min_clip_seconds"],
                           mx=cc["max_clip_seconds"], caption_spec=caption_spec, cands="\n".join(lines),
                           streamer=streamer)
    images = []
    for i, shots in (frames or {}).items():
        for j, jpg in enumerate(shots):
            images.append((f"[{i}] frame {('start', 'peak', 'end')[j] if j < 3 else j}", jpg))

    data = None
    for name, model, base, key in providers:
        try:
            if name == "gemini":
                raw = _call_gemini(model, key, prompt, images)
            else:
                raw = _call_openai_compat(base, model, key, prompt)
                log.info("AI writer: %s answered via %s", model, base)
            data = _parse_json(raw)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("AI writer: %s failed (%s)", name, str(e)[:300])
    if data is None:
        log.warning("AI writer: all providers failed -> clips get no hook banner")
        return None

    picks = []
    used = set()
    for item in data.get("clips", []):
        try:
            idx = int(item["idx"])
            c = cands[idx]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if idx in used:
            continue
        used.add(idx)
        s = float(item.get("start", c["start"]))
        e = float(item.get("end", c["end"]))
        s = min(max(s, c["start"] - 3), c["end"])
        e = max(min(e, c["end"] + 3), s)
        if not (cc["min_clip_seconds"] <= e - s <= cc["max_clip_seconds"]):
            s, e = c["start"], c["end"]
        caps = [str(x)[:300] for x in (item.get("captions") or []) if str(x).strip()]
        tags = [t if t.startswith("#") else f"#{t}" for t in (item.get("hashtags") or [])][:6]
        tags = [re.sub(r"[^\w#]", "", t) for t in tags if len(t) > 1]
        picks.append({**c, "start": round(s, 2), "end": round(e, 2),
                      "title": str(item.get("title") or "")[:95], "hook": _precise_hook(item.get("hook")),
                      "captions": caps, "hashtags": tags})
        if len(picks) >= n:
            break
    return picks or None
