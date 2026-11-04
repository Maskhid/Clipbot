"""`clip` command: new sources -> highlights -> rendered vertical clips -> posting queue."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta

from . import captions, highlights, render, sources, transcribe, writer
from .config import WORK_DIR, Config
from .scheduler import Scheduler
from .state import State, iso, parse_iso, short_hash, utcnow
from .storage import get_storage
from .util import ffprobe, log

MAX_SOURCE_ATTEMPTS = 3


def busy_times(state: State, now: datetime) -> tuple[dict, dict, dict]:
    """Taken posting times per account, planned times per clip, pending jobs per brand."""
    results = state.results()
    busy: dict[str, list[datetime]] = {}
    clip_times: dict[str, list] = {}
    brand_load: dict[str, int] = {}
    for job in state.jobs():
        res = results.get(job["id"]) or {}
        t = parse_iso(res["posted_at"]) if res.get("posted_at") else parse_iso(job["due_at"])
        if res.get("status") == "failed":
            continue
        if t < now - timedelta(days=2):
            continue
        busy.setdefault(job["account"], []).append(t)
        clip_times.setdefault(job["clip_id"], []).append((t, job["brand"]))
        if res.get("status") != "posted":
            brand_load[job["brand"]] = brand_load.get(job["brand"], 0) + 1
    return busy, clip_times, brand_load


def schedule_clip(cfg: Config, state: State, sched: Scheduler, clip: dict) -> list[dict]:
    jobs = []
    for bname in clip["brands"]:
        brand = cfg.brands[bname]
        for acc in brand.accounts:
            if not acc.get("enabled", True):
                continue
            jid = State.job_id(clip["id"], acc.id)
            if state._read("queue", jid):
                continue
            slot = sched.next_slot(acc, clip["id"])
            if slot is None:
                log.warning("No free slot for %s in the next 60 days; skipping", acc.id)
                continue
            job = {"id": jid, "clip_id": clip["id"], "account": acc.id, "brand": bname,
                   "platform": acc.platform, "due_at": iso(slot), "created_at": iso(utcnow())}
            state.save_job(job)
            jobs.append(job)
    return jobs


def process_source(cfg: Config, state: State, storage, src: dict, sched: Scheduler, brand_load: dict) -> list[dict]:
    skey = State.source_key(src)
    work = WORK_DIR / skey
    work.mkdir(parents=True, exist_ok=True)
    cc = dict(cfg.settings["clipping"])
    for k in ("layout", "fg_zoom", "crop_x", "split", "min_clip_seconds", "max_clip_seconds", "clips_per_hour"):
        if k in src:
            cc[k] = src[k]
    if src.get("max_clips"):
        cc["max_clips_per_source"] = int(src["max_clips"])

    video = sources.fetch(src, work / "dl")
    info = ffprobe(video)
    log.info("Source: %.0fs, %dx%d", info["duration"], info["width"], info["height"])

    wav = transcribe.extract_audio(video, work / "audio.wav") if info["has_audio"] else None
    loud = transcribe.loudness_curve(wav) if wav else []
    segs = transcribe.transcribe(wav, cc["whisper_model"], cc.get("language"), cache=work / "transcript.json") if wav else []

    cands = highlights.find_highlights(info["duration"], loud, segs, cc)
    target = highlights.clip_target(info["duration"], cc)
    if not cands:
        log.warning("No usable moments found in %s", src["url"])
        return []

    brands = [cfg.brands[b] for b in src["brands"]]
    styles = sorted({b.get("caption_style", "hype") for b in brands}) or ["hype"]
    game = src.get("game") or next((b.get("niches")[0] for b in brands if b.get("niches")), "") or ""

    picks = writer.llm_pick(cands[: target * 3], segs, src, target, styles, cfg.settings, game)
    if not picks:
        picks = []
        for c in cands[:target]:
            t = writer.heuristic_text(c, segs, src, styles, game)
            picks.append({**c, **t})

    tag = cfg.settings["storage"]["release_prefix"] + utcnow().strftime("%Y-%m-%d")
    made = []
    for i, p in enumerate(picks):
        cid = f"{skey}-{i:02d}-" + short_hash(f"{p['start']}-{p['end']}", 4)
        if state.clip(cid):
            continue
        if src["distribute"] == "all":
            clip_brands = [b.name for b in brands]
        else:  # split: least-loaded brand gets the next clip
            b = min(brands, key=lambda x: (brand_load.get(x.name, 0), src["brands"].index(x.name)))
            clip_brands = [b.name]
            brand_load[b.name] = brand_load.get(b.name, 0) + len(b.accounts)

        dur = p["end"] - p["start"]
        ass = captions.build_ass(highlights.words_in(segs, p["start"], p["end"]), dur, p.get("hook"),
                                 cc["caption"], float(cc.get("hook_seconds", 3)), work / f"{cid}.ass")
        wm = cfg.brands[clip_brands[0]].get("watermark") if len(clip_brands) == 1 else None
        out = render.render_clip(video, info, p["start"], p["end"], ass, work / f"{cid}.mp4", cc, watermark=wm)
        asset = storage.put(out, tag)

        caps = p.get("captions") or []
        cap_map = {st: (caps[j] if j < len(caps) else (caps[0] if caps else p.get("title", ""))) for j, st in enumerate(styles)}
        clip = {"id": cid, "source": src["url"], "source_key": skey, "start": p["start"], "end": p["end"],
                "duration": round(dur, 2), "score": p.get("score"), "title": p.get("title"), "hook": p.get("hook"),
                "captions": cap_map, "hashtags": p.get("hashtags", []), "credit": src.get("credit"),
                "brands": clip_brands, "shared": len(clip_brands) > 1, "watermarked": bool(wm),
                "asset": asset, "tag": tag, "created_at": iso(utcnow())}
        state.save_clip(clip)
        jobs = schedule_clip(cfg, state, sched, clip)
        log.info("Clip %s (%.0fs, score %.2f) -> %s, %d posts scheduled", cid, dur, p.get("score") or 0,
                 ",".join(clip_brands), len(jobs))
        made.append(clip)
    return made


def run_clip(cfg: Config, state: State | None = None, only_url: str | None = None, keep_work: bool = False) -> list[dict]:
    state = state or State()
    storage = get_storage(cfg.settings)
    now = utcnow()
    busy, clip_times, brand_load = busy_times(state, now)
    sched = Scheduler(cfg, busy, clip_times, now=now)
    all_made = []
    for src in cfg.sources:
        if only_url and src["url"] != only_url:
            continue
        if src.get("enabled") is False:
            continue
        done = state.source_done(src) or {}
        if done.get("status") == "done" or done.get("attempts", 0) >= MAX_SOURCE_ATTEMPTS:
            continue
        log.info("=== Source %s -> brands %s (%s)", src["url"], ",".join(src["brands"]), src["distribute"])
        try:
            made = process_source(cfg, state, storage, src, sched, brand_load)
            state.mark_source(src, {"status": "done", "clips": [c["id"] for c in made], "at": iso(utcnow())})
            all_made += made
        except Exception as e:  # noqa: BLE001
            log.exception("Source failed: %s", src["url"])
            state.mark_source(src, {"status": "error", "error": str(e)[:1000],
                                    "attempts": done.get("attempts", 0) + 1, "at": iso(utcnow())})
        finally:
            if not keep_work:
                shutil.rmtree(WORK_DIR / State.source_key(src), ignore_errors=True)
    return all_made


def reschedule_all(cfg: Config, state: State) -> int:
    """Re-plan every unposted job (use after changing windows / posts_per_day)."""
    now = utcnow()
    results = state.results()
    pending = [j for j in state.jobs() if (results.get(j["id"]) or {}).get("status") not in ("posted", "failed")]
    for j in pending:
        state.delete_job(j["id"])
    busy, clip_times, _ = busy_times(state, now)
    sched = Scheduler(cfg, busy, clip_times, now=now)
    n = 0
    for clip_id in sorted({j["clip_id"] for j in pending}):
        clip = state.clip(clip_id)
        if clip:
            n += len(schedule_clip(cfg, state, sched, clip))
    return n
