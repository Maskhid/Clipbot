"""Faceless story videos made from scratch: AI script -> AI pictures -> AI voice -> motion,
captions and music -> posted like any other clip.

Each niche in config/stories.yaml is its own channel (brand). Series niches remember the story
so far, so Part 2 continues Part 1 and every episode is new.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

from . import captions
from .config import CONFIG_DIR, ROOT
from .state import iso, short_hash, utcnow
from .util import ffprobe, log, need, run

W, H = 1080, 1920
IMG = 1080                # the square scene picture
IMG_Y = 400               # where the picture sits
FPS = 30
XF = 0.35                 # crossfade between scenes (seconds)
FONT = ROOT / "assets" / "fonts" / "Poppins-Bold.ttf"


class StoryError(RuntimeError):
    pass


# --------------------------------------------------------------------------- config
def load_niches() -> dict:
    p = CONFIG_DIR / "stories.yaml"
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("stories") or {}


# --------------------------------------------------------------------------- script
PROMPT = """You write short vertical videos (TikTok / Reels / Shorts) for a faceless channel.

CHANNEL: {channel}
WHAT THE CHANNEL IS: {brief}
NARRATION STYLE: {voice_style}
ART STYLE OF THE PICTURES: {art_style}
{character_block}
{series_block}
Stories already used (do NOT repeat or closely copy any of these): {history}

Write ONE episode. Rules:
- Total narration {wmin}-{wmax} words, spoken in about {secs} seconds. Short punchy sentences. Simple English.
- The first sentence is the hook: it must make people stop scrolling.
- Original story. Never copy existing movies, books, creepypastas or other channels.
- No gore, no sexual content, nothing hateful, no real private people.
{facts_rule}
- Split it into {smin}-{smax} scenes. Each scene: 1-3 sentences of narration + a picture.
- "image" = a precise visual description of ONE still picture for that scene (who, where, action, camera angle,
  lighting, mood). No text or letters in the picture. If the main character appears, describe them exactly the same
  way every time. Never mention real brands or famous characters.
{ending_rule}

Reply ONLY with JSON:
{{"title": "short catchy title (max 60 chars)",
  "series_title": "{series_title_hint}",
  "scenes": [{{"narration": "...", "image": "..."}}],
  "caption": "1-2 line social caption that makes people watch (no hashtags)",
  "hashtags": ["#...", "#...", "#...", "#..."],
  "summary": "2-3 sentences: what happened in this episode (for writing the next part)"}}
"""


def _series_block(n: dict, mem: dict) -> tuple[str, str, int]:
    """Returns (instructions, series title hint, part number)."""
    if n.get("format") != "series":
        return "FORMAT: one complete standalone story with a satisfying ending or twist.", "", 0
    arc = mem.get("arc") or {}
    parts = int(n.get("parts", 3))
    if arc and arc.get("part", 0) < arc.get("parts", parts):
        part = arc["part"] + 1
        last = part == arc.get("parts", parts)
        txt = (f"FORMAT: this is PART {part} of {arc.get('parts', parts)} of the series \"{arc['title']}\".\n"
               f"Story so far: {' '.join(arc.get('summaries', []))}\n"
               f"Continue directly from where the last part ended. Keep the same characters and setting. "
               + ("This is the FINAL part: give the story a strong ending." if last else
                  "End on a cliffhanger so viewers follow for the next part."))
        return txt, arc["title"], part
    txt = (f"FORMAT: start a NEW {parts}-part series. This is PART 1. Invent a short dramatic series title "
           f"(2-4 words, e.g. 'Zombie Outbreak', 'Frozen City'). End PART 1 on a cliffhanger.")
    return txt, "2-4 word series title", 1


def write_script(n: dict, mem: dict, settings: dict) -> dict:
    from .writer import _call_gemini, _call_openai_compat, _parse_json, _providers  # noqa: PLC0415

    series_block, hint, part = _series_block(n, mem)
    wmin, wmax = (n.get("words") or [130, 170])[:2]
    smin, smax = (n.get("scenes") or [6, 9])[:2]
    char = n.get("character")
    prompt = PROMPT.format(
        channel=n.get("channel_name") or n["brand"], brief=" ".join(str(n.get("brief", "")).split()),
        voice_style=n.get("voice_style", "storyteller"), art_style=n["art_style"],
        character_block=f"MAIN CHARACTER (appears in most pictures, ALWAYS described exactly like this): {char}" if char else "",
        series_block=series_block, history="; ".join((mem.get("history") or [])[-40:]) or "none yet",
        wmin=wmin, wmax=wmax, secs=round((wmin + wmax) / 2 / 2.6), smin=smin, smax=smax,
        facts_rule=("- FACTS MUST BE TRUE. Only use well-known, verifiable facts. If unsure of a number, don't give one. "
                    "Use 'scientists think' for theories." if n.get("factual") else ""),
        ending_rule=(f"- Last sentence: {str(n['outro']).replace('{next}', str(part + 1))}" if n.get("outro") else ""),
        series_title_hint=hint)
    errors = []
    for name, model, base, key in _providers(settings["llm"]):
        try:
            raw = _call_gemini(model, key, prompt) if name == "gemini" else _call_openai_compat(base, model, key, prompt)
            data = _parse_json(raw)
            scenes = [s for s in data.get("scenes", []) if s.get("narration") and s.get("image")]
            if len(scenes) < 3:
                raise ValueError("too few scenes")
            data["scenes"] = scenes
            data["part"] = part
            if part and not data.get("series_title"):
                data["series_title"] = (mem.get("arc") or {}).get("title") or data.get("title", "")
            if not part:  # standalone: the header shows this episode's title
                data["series_title"] = data.get("title", "")
            log.info("Story script (%s): %s, %d scenes, %d words", name, data.get("title"), len(scenes),
                     sum(len(s["narration"].split()) for s in scenes))
            return data
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:200]}")
    raise StoryError("AI writer failed: " + " | ".join(errors) if errors else "No AI writer configured (LLM_API_KEY)")


def update_memory(n: dict, mem: dict, script: dict) -> dict:
    mem = dict(mem)
    hist = list(mem.get("history") or [])
    hist.append(script.get("series_title") or script.get("title") or "")
    mem["history"] = [h for h in hist if h][-60:]
    if n.get("format") == "series":
        part = script.get("part") or 1
        parts = int(n.get("parts", 3))
        if part == 1:
            mem["arc"] = {"title": script.get("series_title") or script.get("title"), "part": 1, "parts": parts,
                          "summaries": [script.get("summary", "")]}
        else:
            arc = dict(mem.get("arc") or {})
            arc["part"] = part
            arc["summaries"] = (arc.get("summaries") or []) + [script.get("summary", "")]
            mem["arc"] = arc
        if part >= mem["arc"].get("parts", parts):
            mem["arc"] = {}   # finished: next video starts a new series
    return mem


# --------------------------------------------------------------------------- voice
def _edge_tts(text: str, v: dict, out: Path) -> list[dict]:
    import edge_tts  # noqa: PLC0415

    async def go():
        com = edge_tts.Communicate(text, v.get("name", "en-US-AndrewNeural"), rate=v.get("rate", "+0%"),
                                   pitch=v.get("pitch", "+0Hz"), boundary="WordBoundary")
        words = []
        with open(out, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    words.append({"word": ch["text"], "start": s, "end": s + ch["duration"] / 1e7})
        return words
    return asyncio.run(go())


def _elevenlabs(text: str, v: dict, out: Path) -> list[dict]:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise StoryError("voice provider is elevenlabs but ELEVENLABS_API_KEY is not set")
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{v.get('voice_id', 'pNInz6obpgDQGcFmaJgB')}/with-timestamps",
                      headers={"xi-api-key": key}, timeout=180,
                      json={"text": text, "model_id": v.get("model", "eleven_multilingual_v2")})
    r.raise_for_status()
    d = r.json()
    out.write_bytes(base64.b64decode(d["audio_base64"]))
    al = d.get("alignment") or {}
    chars, st, en = al.get("characters", []), al.get("character_start_times_seconds", []), al.get("character_end_times_seconds", [])
    words, cur, cs = [], "", None
    for c, a, b in zip(chars, st, en):
        if c.isspace():
            if cur:
                words.append({"word": cur, "start": cs, "end": b})
            cur, cs = "", None
            continue
        if cs is None:
            cs = a
        cur += c
        last_end = b
    if cur:
        words.append({"word": cur, "start": cs, "end": last_end})
    return words


def make_voice(text: str, v: dict, work: Path) -> tuple[Path, list[dict]]:
    out = work / "voice.mp3"
    prov = (v.get("provider") or "edge").lower()
    words = []
    for attempt in range(3):
        try:
            words = _elevenlabs(text, v, out) if prov == "elevenlabs" else _edge_tts(text, v, out)
            break
        except Exception as e:  # noqa: BLE001
            log.warning("Voice attempt %d failed: %s", attempt + 1, str(e)[:200])
            time.sleep(5 * (attempt + 1))
    if not out.exists() or out.stat().st_size < 1000:
        raise StoryError("Could not make the voiceover")
    if not words:  # no timings from the voice service -> listen to it with whisper
        from .transcribe import transcribe  # noqa: PLC0415

        wav = work / "voice.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "16000", str(wav)])
        words = [w for s in transcribe(wav) for w in s.get("words", [])]
    return out, words


# --------------------------------------------------------------------------- pictures
def _cloudflare(prompt: str, seed: int) -> bytes | None:
    acct, tok = os.environ.get("CF_ACCOUNT_ID", ""), os.environ.get("CF_API_TOKEN", "")
    if not (acct and tok):
        return None
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                      headers={"Authorization": f"Bearer {tok}"}, json={"prompt": prompt[:2000], "steps": 6, "seed": seed},
                      timeout=120)
    if r.status_code != 200:
        raise StoryError(f"Cloudflare image HTTP {r.status_code}: {r.text[:200]}")
    img = (r.json().get("result") or {}).get("image")
    return base64.b64decode(img) if img else None


def _pollinations(prompt: str, seed: int) -> bytes | None:
    hdr = {}
    if os.environ.get("POLLINATIONS_TOKEN"):
        hdr["Authorization"] = f"Bearer {os.environ['POLLINATIONS_TOKEN']}"
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt[:1500])}"
           f"?width=1024&height=1024&seed={seed}&model=flux&nologo=true&private=true")
    r = requests.get(url, headers=hdr, timeout=180)
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image"):
        raise StoryError(f"Pollinations HTTP {r.status_code}")
    return r.content


def make_images(scenes: list[dict], n: dict, work: Path, seed0: int) -> list[Path]:
    style = n["art_style"]
    neg = "no text, no letters, no words, no watermark, no logo, no signature"
    paths, last_poll = [], 0.0
    for i, sc in enumerate(scenes):
        prompt = f"{style}. {sc['image']}. {neg}"
        img = None
        for prov in ("cloudflare", "pollinations"):
            for attempt in range(2):
                try:
                    if prov == "pollinations":
                        wait = 16 - (time.time() - last_poll)   # free tier: one picture per 15 s
                        if wait > 0:
                            time.sleep(wait)
                        last_poll = time.time()
                        img = _pollinations(prompt, seed0 + i)
                    else:
                        img = _cloudflare(prompt, seed0 + i)
                    if img:
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("Picture %d via %s failed: %s", i + 1, prov, str(e)[:160])
                    time.sleep(4)
                if prov == "cloudflare" and img is None and not os.environ.get("CF_API_TOKEN"):
                    break
            if img:
                break
        if not img:
            if not paths:
                raise StoryError("Could not make the first picture (image services unavailable)")
            log.warning("Picture %d failed; reusing the previous one", i + 1)
            paths.append(paths[-1])
            continue
        p = work / f"scene{i:02d}.png"
        from io import BytesIO  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        im = Image.open(BytesIO(img)).convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
        im.resize((2048, 2048), Image.LANCZOS).save(p)
        paths.append(p)
    return paths


# --------------------------------------------------------------------------- render
MOTIONS = [  # (zoom expr, x expr, y expr) for zoompan; "on" = frame number, "d" = total frames
    ("1+0.14*on/{d}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),                 # slow push in
    ("1.14-0.14*on/{d}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),              # pull out
    ("1.12", "(iw-iw/zoom)*on/{d}", "ih/2-(ih/zoom/2)"),                         # pan left -> right
    ("1.12", "(iw-iw/zoom)*(1-on/{d})", "ih/2-(ih/zoom/2)"),                     # pan right -> left
    ("1+0.1*on/{d}", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*0.3"),                    # push in, upper part
]


def _header_png(n: dict, script: dict, out: Path) -> Path:
    """The series title (and Part N) at the top, like story channels do."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    img = Image.new("RGBA", (W, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    title = (script.get("series_title") or script.get("title") or "").strip()
    sub = f"Part {script['part']}" if script.get("part") else (n.get("header_sub") or "")
    f1 = ImageFont.truetype(str(FONT), 76 if len(title) <= 18 else 60)
    f2 = ImageFont.truetype(str(FONT), 64)
    acc = n.get("accent", "#FFB020")

    def center(t, y, f, fill):
        w = d.textlength(t, font=f)
        for dx, dy in ((3, 3), (0, 3), (3, 0)):
            d.text(((W - w) / 2 + dx, y + dy), t, font=f, fill=(0, 0, 0, 170))
        d.text(((W - w) / 2, y), t, font=f, fill=fill)
    if title:
        center(title, 60, f1, "white")
    if sub:
        center(sub, 160, f2, acc)
    img.save(out)
    return out


def _scene_clip(img: Path, dur: float, motion: int, out: Path) -> Path:
    frames = max(2, int(round(dur * FPS)))
    z, x, y = (m.format(d=frames) for m in MOTIONS[motion % len(MOTIONS)])
    fc = (f"[0:v]split[a][b];"
          f"[a]zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={IMG}x{IMG}:fps={FPS}[fg];"
          f"[b]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:3,eq=brightness=-0.32,"
          f"loop=loop={frames}:size=1:start=0,setpts=N/{FPS}/TB,trim=end_frame={frames}[bg];"
          f"[bg][fg]overlay=0:{IMG_Y},setsar=1,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(img), "-filter_complex", fc, "-map", "[v]",
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS), str(out)])
    return out


def _scene_times(scenes: list[dict], words: list[dict], total: float) -> list[float]:
    """Start time of each scene, found by matching scene text length to the spoken word timings."""
    lens = [len(re.sub(r"\s+", " ", s["narration"]).strip()) + 1 for s in scenes]
    tot = sum(lens)
    marks, acc = [], 0
    for ln in lens:
        marks.append(acc / tot)
        acc += ln
    spoken = sum(len(w["word"]) + 1 for w in words) or 1
    starts, pos, wi = [], 0, 0
    for frac in marks:
        target = frac * spoken
        while wi < len(words) and pos + len(words[wi]["word"]) + 1 <= target:
            pos += len(words[wi]["word"]) + 1
            wi += 1
        starts.append(0.0 if not starts else (words[wi]["start"] if wi < len(words) else total * frac))
    return starts


def render_story(n: dict, script: dict, images: list[Path], voice: Path, words: list[dict], out: Path, work: Path) -> Path:
    need("ffmpeg")
    vdur = float(ffprobe(voice)["duration"])
    total = vdur + 0.8
    starts = _scene_times(script["scenes"], words, vdur) + [total]
    clips, lens = [], []
    for i, img in enumerate(images):
        d = max(1.2, starts[i + 1] - starts[i]) + (XF if i < len(images) - 1 else 0)
        lens.append(d)
        clips.append(_scene_clip(img, d, i, work / f"seg{i:02d}.mp4"))
    # join the scenes with short crossfades (each xfade starts where the previous scene's own time ends)
    inputs, fc, prev, off = [], [], "0:v", 0.0
    for c in clips:
        inputs += ["-i", str(c)]
    for i in range(1, len(clips)):
        off += lens[i - 1] - XF
        fc.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={XF}:offset={off:.3f}[x{i}]")
        prev = f"x{i}"
    joined = work / "joined.mp4"
    if fc:
        run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc), "-map", f"[{prev}]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(joined)])
    else:
        joined = clips[0]
    # captions, header, voice and music
    cap = {"size": int(n.get("caption_size", 80)), "position": 0.865, "words_per_line": int(n.get("words_per_line", 4)),
           "uppercase": bool(n.get("caption_upper", False)), "highlight": n.get("accent", "#FFB020"), "outline": 6}
    ass = captions.build_ass(words, total, None, cap, 0, work / "story.ass")
    header = _header_png(n, script, work / "header.png")
    from .render import FONTS_DIR, _fesc  # noqa: PLC0415

    music = _pick_music(n)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(joined), "-i", str(header), "-i", str(voice)]
    afc = "[2:a]apad=pad_dur=0.8,volume=1.0[vo]"
    amap = "[vo]"
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        vol = float(n.get("music_volume", 0.12))
        afc += (f";[3:a]volume={vol},afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5[mu];"
                f"[vo][mu]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        amap = "[aout]"
    vfc = (f"[0:v][1:v]overlay=0:{IMG_Y - 300 - 20}[h];"
           f"[h]ass='{_fesc(ass)}':fontsdir='{_fesc(FONTS_DIR)}'[vout]")
    cmd += ["-filter_complex", vfc + ";" + afc, "-map", "[vout]", "-map", amap, "-t", f"{total:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    run(cmd)
    return out


def _pick_music(n: dict) -> Path | None:
    d = n.get("music_dir")
    if not d:
        return None
    p = Path(d) if Path(d).is_absolute() else ROOT / d
    files = [f for f in p.glob("*") if f.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg")] if p.exists() else []
    return random.choice(files) if files else None


# --------------------------------------------------------------------------- one episode
def make_episode(cfg, name: str, n: dict, state, storage, work: Path, sample: bool = False) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    mem = state._read("stories", name) or {}
    script = write_script(n, mem, cfg.settings)
    text = " ".join(s["narration"].strip() for s in script["scenes"])
    voice, words = make_voice(text, n.get("voice") or {}, work)
    seed = int(short_hash(name + script.get("title", "") + iso(utcnow()), 8), 16) % 900000
    images = make_images(script["scenes"], n, work, seed)
    vid = f"{name}-{utcnow().strftime('%Y%m%d-%H%M')}-{short_hash(script.get('title', ''), 4)}"
    out = render_story(n, script, images, voice, words, work / f"{vid}.mp4", work)
    dur = float(ffprobe(out)["duration"])
    tag = "samples" if sample else cfg.settings["storage"]["release_prefix"] + utcnow().strftime("%Y-%m-%d")
    asset = storage.put(out, tag)
    title = script.get("title") or script.get("series_title") or "Story"
    if script.get("part"):
        title = f"{script.get('series_title') or title} - Part {script['part']}"
    brand = cfg.brands[n["brand"]]
    clip = {"id": vid, "source": f"story:{name}", "start": 0, "end": dur, "duration": round(dur, 2),
            "title": title, "hook": None, "captions": {brand.get("caption_style", "story"): script.get("caption") or title},
            "hashtags": script.get("hashtags") or [], "credit": None, "brands": [n["brand"]], "shared": False,
            "watermarked": True, "campaign": None, "asset": asset, "tag": tag, "created_at": iso(utcnow()),
            "story": {"niche": name, "part": script.get("part"), "series": script.get("series_title"),
                      "summary": script.get("summary")}}
    if not sample:
        state._write("stories", name, update_memory(n, mem, script))
    return {"clip": clip, "script": script, "url": storage.public_url(asset, tag)}


def run_stories(cfg, only: str | None = None, sample: bool = False, count: int | None = None) -> int:
    from .notify import notify  # noqa: PLC0415
    from .pipeline import busy_times, schedule_clip  # noqa: PLC0415
    from .scheduler import Scheduler  # noqa: PLC0415
    from .state import State  # noqa: PLC0415
    from .storage import get_storage  # noqa: PLC0415

    niches = load_niches()
    if only:
        niches = {k: v for k, v in niches.items() if k == only}
    if not niches:
        raise StoryError("No niches found in config/stories.yaml" + (f" named '{only}'" if only else ""))
    state = State()
    (state.root / "stories").mkdir(parents=True, exist_ok=True)
    storage = get_storage(cfg.settings)
    made, failed = 0, []
    for name, n in niches.items():
        if not sample and not n.get("enabled", True):
            continue
        if n["brand"] not in cfg.brands:
            failed.append(f"{name}: brand '{n['brand']}' is not in accounts.yaml")
            continue
        for k in range(count or (1 if sample else int(n.get("per_run", 1)))):
            work = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "stories" / f"{name}{k}"
            try:
                ep = make_episode(cfg, name, n, state, storage, work, sample=sample)
                made += 1
                if sample:
                    notify(cfg.settings, f"🎬 **SAMPLE – {name}**: {ep['clip']['title']}\n{ep['url']}", brand=n["brand"])
                else:
                    state.save_clip(ep["clip"])
                    busy, ct, _ = busy_times(state, utcnow())
                    jobs = schedule_clip(cfg, state, Scheduler(cfg, busy, ct, now=utcnow()), ep["clip"])
                    log.info("Story %s -> %d posts scheduled", ep["clip"]["id"], len(jobs))
            except Exception as e:  # noqa: BLE001
                log.exception("Story for %s failed", name)
                failed.append(f"{name}: {str(e)[:250]}")
    if failed:
        notify(cfg.settings, "❌ **Story videos failed**\n" + "\n".join(failed))
    log.info("Made %d story video(s); %d failed", made, len(failed))
    return 1 if failed and not made else 0
