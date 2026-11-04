"""`post` command: posts every job whose time has come."""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from . import render
from .compose import compose
from .config import WORK_DIR, Config
from .notify import notify
from .platforms import DAILY_CAP, PostError, get_uploader
from .state import State, iso, parse_iso, utcnow
from .storage import get_storage
from .util import log
from .vault import Vault, VaultError


def due_jobs(cfg: Config, state: State, now=None) -> list[dict]:
    now = now or utcnow()
    results = state.results()
    out = []
    for j in state.jobs():
        res = results.get(j["id"]) or {}
        if res.get("status") in ("posted", "failed"):
            continue
        if parse_iso(j["due_at"]) > now:
            continue
        if res.get("next_attempt_at") and parse_iso(res["next_attempt_at"]) > now:
            continue
        out.append(j)
    out.sort(key=lambda j: j["due_at"])
    return out


def _posted_last_24h(state: State, now) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in state.results().values():
        if r.get("status") == "posted" and r.get("posted_at") and parse_iso(r["posted_at"]) > now - timedelta(hours=24):
            counts[r["account"]] = counts.get(r["account"], 0) + 1
    return counts


def run_post(cfg: Config, state: State | None = None, dry_run: bool | None = None, only_account: str | None = None) -> dict:
    state = state or State()
    now = utcnow()
    p = cfg.settings["posting"]
    dry = p.get("dry_run", False) if dry_run is None else dry_run
    jobs = due_jobs(cfg, state, now)
    if only_account:
        jobs = [j for j in jobs if j["account"] == only_account]
    summary = {"posted": [], "failed": [], "retry": [], "blocked": [], "notes": []}
    if not jobs:
        log.info("Nothing due.")
        return summary

    vault = Vault()
    storage = get_storage(cfg.settings)
    work = WORK_DIR / "post"
    work.mkdir(parents=True, exist_ok=True)
    counts = _posted_last_24h(state, now)
    variants: dict[tuple, object] = {}
    done_this_run = 0

    try:
        for job in jobs:
            if done_this_run >= int(p["max_posts_per_run"]):
                log.info("Reached max_posts_per_run; the rest go next run.")
                break
            acc = cfg.accounts.get(job["account"])
            prev = state.result(job["id"]) or {}
            base = {"job_id": job["id"], "account": job["account"], "clip_id": job["clip_id"], "platform": job["platform"]}
            if acc is None or not acc.get("enabled", True):
                state.save_result(job["id"], {**base, "status": "failed", "error": "account removed or disabled", "at": iso(now)})
                continue
            cap = int(acc.get("daily_cap", DAILY_CAP.get(acc.platform, 10)))
            if counts.get(acc.id, 0) >= cap:
                log.info("%s hit its 24h cap (%d); waiting", acc.id, cap)
                continue
            if not vault.has_account(acc.id):
                if prev.get("status") != "blocked":
                    summary["blocked"].append(f"{acc.id}: not connected yet (run auth for it)")
                state.save_result(job["id"], {**base, "status": "blocked", "error": "account not connected", "at": iso(now)})
                continue
            clip = state.clip(job["clip_id"])
            if not clip:
                state.save_result(job["id"], {**base, "status": "failed", "error": "clip record missing", "at": iso(now)})
                continue
            brand = cfg.brands[job["brand"]] if job["brand"] in cfg.brands else cfg.brands[acc.brand]
            meta = compose(clip, brand, acc)
            try:
                master = storage.get(clip["asset"], clip["tag"], work / "masters")
                wm = brand.get("watermark") if not clip.get("watermarked") else None
                video = master
                if clip.get("shared") or wm:
                    key = (clip["id"], brand.name)
                    if key not in variants:
                        variants[key] = render.make_variant(master, work / f"{clip['id']}__{brand.name}.mp4",
                                                            seed=f"{clip['id']}:{brand.name}", vcfg=cfg.settings["variants"],
                                                            watermark=wm, vary=bool(clip.get("shared")))
                    video = variants[key]
                if dry:
                    log.info("[dry-run] would post %s -> %s: %s", clip["id"], acc.id, (meta.get("title") or meta.get("text"))[:80])
                    res = {"id": "dry-run", "url": ""}
                elif acc.get("mode") == "manual":
                    # you post it yourself: get the file link + text in your notifications
                    if Path(video) != Path(master):  # upload this brand's copy next to the master
                        storage.put(Path(video), clip["tag"])
                    link = storage.public_url(Path(video).name, clip["tag"])
                    text = meta.get("description") or meta.get("text") or ""
                    res = {"id": "manual", "url": link,
                           "note": f"Post this yourself on {acc.platform}: {link}\n"
                                   + (f"Title: {meta['title']}\n" if meta.get("title") else "") + f"Text:\n{text}"}
                else:
                    log.info("Posting %s -> %s (%s)", clip["id"], acc.id, acc.platform)
                    res = get_uploader(acc.platform).post(acc, vault, video, meta, cfg.settings)
                rec = {**base, "status": "posted", "posted_at": iso(utcnow()), "remote_id": res.get("id"), "post_id": res.get("post_id"),
                       "url": res.get("url", ""), "dry_run": dry, "attempts": prev.get("attempts", 0) + 1}
                state.save_result(job["id"], rec)
                counts[acc.id] = counts.get(acc.id, 0) + 1
                done_this_run += 1
                summary["posted"].append(f"✅ {acc.id}: {res.get('url') or res.get('id')}")
                if res.get("note"):
                    summary["notes"].append(f"📥 {acc.id}: {res['note']}")
            except (PostError, VaultError, Exception) as e:  # noqa: BLE001
                retryable = getattr(e, "retryable", not isinstance(e, VaultError))
                attempts = prev.get("attempts", 0) + 1
                give_up = (not retryable) or attempts >= int(p["retry_attempts"])
                rec = {**base, "status": "failed" if give_up else "retry", "error": str(e)[:1500], "attempts": attempts,
                       "at": iso(utcnow())}
                if not give_up:
                    rec["next_attempt_at"] = iso(utcnow() + timedelta(minutes=int(p["retry_backoff_minutes"]) * attempts))
                state.save_result(job["id"], rec)
                line = f"{'❌' if give_up else '⏳'} {acc.id}: {str(e)[:300]}"
                summary["failed" if give_up else "retry"].append(line)
                log.error(line)
            finally:
                vault.save()
    finally:
        vault.save()
        shutil.rmtree(work, ignore_errors=True)

    lines = []
    for k, title in (("posted", "Posted"), ("notes", "Action needed"), ("retry", "Will retry"),
                     ("failed", "Failed"), ("blocked", "Not connected")):
        if summary[k]:
            lines.append(f"**{title}**\n" + "\n".join(summary[k]))
    if lines and not dry:
        notify(cfg.settings, "ClipBot\n" + "\n\n".join(lines))
    return summary
