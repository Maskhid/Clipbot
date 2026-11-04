"""Performance tracking: which platforms, brands and accounts are actually working.

Daily: for every connected account, list its recent videos (last 30 days) through the
official APIs, read views / likes / comments / shares and the follower count, and save a
snapshot to state/metrics/<account>.json. Videos are matched back to the clips ClipBot
posted (by post id, or by time for TikTok-inbox and manual posts).

Weekly (or on demand): write reports/latest.md + reports/videos.csv and send a summary.
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .config import ROOT, STATE_DIR, Config
from .platforms import tiktok as tt
from .platforms import youtube as yt
from .state import State, iso, parse_iso, utcnow
from .util import log

METRICS_DIR = STATE_DIR / "metrics"
REPORTS_DIR = ROOT / "reports"
LOOKBACK_DAYS = 30
PLATFORM_ORDER = ["youtube", "tiktok", "instagram", "facebook"]
NAMES = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "facebook": "Facebook"}


class StatsError(Exception):
    pass


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ fetchers ----
# Each returns (followers or None, [video dicts], [notes])
# video dict: id, created (iso), views, likes, comments, shares, url, title

def fetch_youtube(acc, vault, settings, since: datetime):
    app, creds = vault.app("youtube"), vault.account(acc.id)
    h = {"Authorization": f"Bearer {yt.access_token(app, creds)}"}
    base = "https://www.googleapis.com/youtube/v3"
    ch = requests.get(f"{base}/channels", headers=h, timeout=30,
                      params={"part": "statistics,contentDetails", "mine": "true"}).json()
    if "error" in ch:
        raise StatsError(ch["error"].get("message", "channels.list failed"))
    item = (ch.get("items") or [{}])[0]
    followers = _int(item.get("statistics", {}).get("subscriberCount"))
    uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads:
        return followers, [], []
    pl = requests.get(f"{base}/playlistItems", headers=h, timeout=30, params={
        "part": "contentDetails", "playlistId": uploads, "maxResults": 50}).json()
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])
           if parse_iso(i["contentDetails"].get("videoPublishedAt", "2000-01-01T00:00:00Z")) >= since]
    videos = []
    if ids:
        vs = requests.get(f"{base}/videos", headers=h, timeout=30, params={
            "part": "statistics,snippet", "id": ",".join(ids[:50])}).json()
        for v in vs.get("items", []):
            s = v.get("statistics", {})
            videos.append({"id": v["id"], "created": v["snippet"]["publishedAt"], "title": v["snippet"].get("title", ""),
                           "views": _int(s.get("viewCount")), "likes": _int(s.get("likeCount")),
                           "comments": _int(s.get("commentCount")), "shares": None,
                           "url": f"https://youtube.com/shorts/{v['id']}"})
    return followers, videos, []


def fetch_tiktok(acc, vault, settings, since: datetime):
    app, creds = vault.app("tiktok"), vault.account(acc.id)
    token = tt.access_token(app, creds)
    vault.update_account(acc.id, creds)
    h = {"Authorization": f"Bearer {token}"}
    notes, followers = [], None
    u = requests.get(f"{tt.API}/v2/user/info/", headers=h, timeout=30,
                     params={"fields": "follower_count,likes_count,video_count"}).json()
    if (u.get("error") or {}).get("code", "ok") == "ok":
        followers = _int(u.get("data", {}).get("user", {}).get("follower_count"))
    else:
        notes.append("no follower count (reconnect TikTok to grant user.info.stats)")
    videos, cursor = [], None
    for _ in range(6):
        body = {"max_count": 20}
        if cursor:
            body["cursor"] = cursor
        r = requests.post(f"{tt.API}/v2/video/list/", headers={**h, "Content-Type": "application/json"}, timeout=30,
                          params={"fields": "id,create_time,title,share_url,view_count,like_count,comment_count,share_count"},
                          json=body).json()
        if (r.get("error") or {}).get("code", "ok") != "ok":
            notes.append("no video stats (reconnect TikTok to grant video.list)")
            break
        d = r.get("data", {})
        stop = False
        for v in d.get("videos", []):
            created = datetime.fromtimestamp(int(v.get("create_time", 0)), tz=timezone.utc)
            if created < since:
                stop = True
                continue
            videos.append({"id": str(v["id"]), "created": iso(created), "title": v.get("title", ""),
                           "views": _int(v.get("view_count")), "likes": _int(v.get("like_count")),
                           "comments": _int(v.get("comment_count")), "shares": _int(v.get("share_count")),
                           "url": (v.get("share_url") or "").split("?")[0]})
        cursor = d.get("cursor")
        if stop or not d.get("has_more"):
            break
    return followers, videos, notes


def _insight_value(item: dict):
    if item.get("total_value"):
        return _int(item["total_value"].get("value"))
    vals = item.get("values") or [{}]
    return _int(vals[0].get("value"))


def fetch_instagram(acc, vault, settings, since: datetime):
    creds = vault.account(acc.id)
    G = f"https://graph.facebook.com/{settings['meta']['graph_version']}"
    tok, ig = creds["access_token"], creds["ig_user_id"]
    me = requests.get(f"{G}/{ig}", timeout=30, params={"fields": "followers_count", "access_token": tok}).json()
    if "error" in me:
        raise StatsError(me["error"].get("message"))
    followers = _int(me.get("followers_count"))
    media = requests.get(f"{G}/{ig}/media", timeout=30, params={
        "fields": "id,timestamp,like_count,comments_count,media_product_type,permalink,caption",
        "limit": 50, "access_token": tok}).json()
    videos, notes, insights_ok = [], [], True
    for m in media.get("data", []):
        created = parse_iso(m["timestamp"].replace("+0000", "+00:00"))
        if created < since or m.get("media_product_type") != "REELS":
            continue
        v = {"id": m["id"], "created": iso(created), "title": (m.get("caption") or "")[:80],
             "views": None, "likes": _int(m.get("like_count")), "comments": _int(m.get("comments_count")),
             "shares": None, "url": m.get("permalink", "")}
        if insights_ok:
            ins = requests.get(f"{G}/{m['id']}/insights", timeout=30,
                               params={"metric": "views,shares", "access_token": tok}).json()
            if "error" in ins:
                insights_ok = False
                notes.append("no IG view counts (add instagram_manage_insights permission and reconnect Meta)")
            else:
                for it in ins.get("data", []):
                    if it.get("name") == "views":
                        v["views"] = _insight_value(it)
                    elif it.get("name") == "shares":
                        v["shares"] = _insight_value(it)
        videos.append(v)
    return followers, videos, notes


def fetch_facebook(acc, vault, settings, since: datetime):
    creds = vault.account(acc.id)
    G = f"https://graph.facebook.com/{settings['meta']['graph_version']}"
    tok, page = creds["access_token"], creds["page_id"]
    p = requests.get(f"{G}/{page}", timeout=30, params={"fields": "followers_count,fan_count", "access_token": tok}).json()
    if "error" in p:
        raise StatsError(p["error"].get("message"))
    followers = _int(p.get("followers_count") or p.get("fan_count"))
    reels = requests.get(f"{G}/{page}/video_reels", timeout=30, params={
        "fields": "id,created_time,description,permalink_url", "limit": 50, "access_token": tok}).json()
    videos, notes, insights_ok = [], [], True
    for r in reels.get("data", []):
        created = parse_iso(r["created_time"].replace("+0000", "+00:00"))
        if created < since:
            continue
        v = {"id": str(r["id"]), "created": iso(created), "title": (r.get("description") or "")[:80],
             "views": None, "likes": None, "comments": None, "shares": None,
             "url": f"https://www.facebook.com/reel/{r['id']}"}
        if insights_ok:
            ins = requests.get(f"{G}/{r['id']}/video_insights", timeout=30, params={
                "metric": "fb_reels_total_plays,blue_reels_play_count", "access_token": tok}).json()
            if "error" in ins:
                insights_ok = False
                notes.append("no FB view counts (add read_insights permission and reconnect Meta)")
            else:
                vals = {it.get("name"): _insight_value(it) for it in ins.get("data", [])}
                v["views"] = vals.get("fb_reels_total_plays") or vals.get("blue_reels_play_count")
        eng = requests.get(f"{G}/{r['id']}", timeout=30, params={
            "fields": "likes.summary(true).limit(0),comments.summary(true).limit(0)", "access_token": tok}).json()
        v["likes"] = _int(((eng.get("likes") or {}).get("summary") or {}).get("total_count"))
        v["comments"] = _int(((eng.get("comments") or {}).get("summary") or {}).get("total_count"))
        videos.append(v)
    return followers, videos, notes


FETCHERS = {"youtube": fetch_youtube, "tiktok": fetch_tiktok, "instagram": fetch_instagram, "facebook": fetch_facebook}


# ------------------------------------------------------------ matching ----

def match_videos(videos: list[dict], jobs: list[dict]) -> None:
    """Attach clip_id to each video: exact post id first, then by time for inbox/manual posts."""
    by_id = {}
    for j in jobs:
        for key in (j.get("remote_id"), j.get("post_id")):
            if key and key not in ("manual", "dry-run"):
                by_id[str(key)] = j
    used = set()
    for v in videos:
        j = by_id.get(str(v["id"]))
        if j:
            v["clip_id"], v["match"] = j["clip_id"], "id"
            used.add(j["job_id"])
    loose = sorted([j for j in jobs if j["job_id"] not in used and j.get("posted_at")], key=lambda j: j["posted_at"])
    for v in sorted([v for v in videos if not v.get("clip_id")], key=lambda v: v["created"]):
        t = parse_iso(v["created"])
        best = None
        for j in loose:
            pj = parse_iso(j["posted_at"])
            if j["job_id"] in used:
                continue
            if pj - timedelta(hours=1) <= t <= pj + timedelta(hours=72):
                if best is None or pj > parse_iso(best["posted_at"]):
                    best = j
        if best:
            v["clip_id"], v["match"] = best["clip_id"], "time"
            used.add(best["job_id"])


# ------------------------------------------------------------- collect ----

def _metrics_path(acc_id: str) -> Path:
    return METRICS_DIR / f"{acc_id}.json"


def load_metrics(acc_id: str) -> dict:
    p = _metrics_path(acc_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"account": acc_id, "followers": [], "videos": {}}


def collect(cfg: Config, vault, state: State | None = None, now: datetime | None = None) -> dict:
    state = state or State()
    now = now or utcnow()
    since = now - timedelta(days=LOOKBACK_DAYS)
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    results = list(state.results().values())
    out = {"ok": [], "errors": []}
    for acc in cfg.accounts.values():
        if not acc.get("enabled", True) or not vault.has_account(acc.id):
            continue
        try:
            followers, videos, notes = FETCHERS[acc.platform](acc, vault, cfg.settings, since)
        except Exception as e:  # noqa: BLE001
            log.warning("stats %s: %s", acc.id, str(e)[:200])
            out["errors"].append(f"{acc.id}: {str(e)[:160]}")
            continue
        finally:
            vault.save()
        jobs = [r for r in results if r.get("account") == acc.id and r.get("status") == "posted" and not r.get("dry_run")]
        match_videos(videos, jobs)
        m = load_metrics(acc.id)
        today = now.date().isoformat()
        if followers is not None:
            m["followers"] = [f for f in m["followers"] if f["date"] != today] + [{"date": today, "followers": followers}]
            m["followers"] = m["followers"][-120:]
        for v in videos:
            v["updated_at"] = iso(now)
            m["videos"][v["id"]] = {**m["videos"].get(v["id"], {}), **v}
        cutoff = now - timedelta(days=90)
        m["videos"] = {k: v for k, v in m["videos"].items() if parse_iso(v["created"]) >= cutoff}
        m.update({"account": acc.id, "platform": acc.platform, "brand": acc.brand, "notes": notes, "updated_at": iso(now)})
        _metrics_path(acc.id).write_text(json.dumps(m, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        out["ok"].append(acc.id)
        log.info("stats %s: %d videos, followers=%s", acc.id, len(videos), followers)
    return out


# -------------------------------------------------------------- report ----

def _follower_delta(hist: list[dict], now: datetime, days: int):
    if not hist:
        return None
    cur = hist[-1]["followers"]
    target = (now - timedelta(days=days)).date().isoformat()
    older = [h for h in hist if h["date"] <= target]
    base = older[-1] if older else hist[0]
    if base is hist[-1]:
        return None
    return cur - base["followers"]


def _fmt(n) -> str:
    if n is None:
        return "–"
    if isinstance(n, float):
        return f"{n:.1%}" if n < 1.5 else f"{n:,.0f}"
    if abs(n) >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if abs(n) >= 10_000:
        return f"{n / 1e3:.0f}K"
    return f"{n:,}"


def build_report(cfg: Config, days: int = 7, now: datetime | None = None) -> tuple[str, str]:
    """Returns (markdown report, short chat summary)."""
    now = now or utcnow()
    start = now - timedelta(days=days)
    rows, all_videos = [], []
    for acc in cfg.accounts.values():
        m = load_metrics(acc.id)
        if not m.get("updated_at"):
            continue
        vids = [v for v in m["videos"].values() if parse_iso(v["created"]) >= start]
        for v in m["videos"].values():
            all_videos.append({**v, "account": acc.id, "platform": acc.platform, "brand": acc.brand})
        views = [v["views"] for v in vids if v.get("views") is not None]
        eng = sum((v.get("likes") or 0) + (v.get("comments") or 0) + (v.get("shares") or 0) for v in vids)
        best = max(vids, key=lambda v: v.get("views") or -1, default=None)
        rows.append({"acc": acc, "posts": len(vids), "views": sum(views) if views else None,
                     "avg": (sum(views) / len(views)) if views else None,
                     "median": statistics.median(views) if views else None,
                     "eng_rate": (eng / sum(views)) if views and sum(views) else None,
                     "followers": m["followers"][-1]["followers"] if m["followers"] else None,
                     "growth": _follower_delta(m["followers"], now, days), "best": best, "notes": m.get("notes", [])})

    if not rows:
        msg = "No stats yet: run the Stats workflow after your accounts have posted for a day or two."
        return f"# ClipBot performance\n\n{msg}\n", msg

    def agg(key_fn):
        groups: dict = {}
        for r in rows:
            groups.setdefault(key_fn(r), []).append(r)
        out = []
        for k, rs in groups.items():
            posts = sum(r["posts"] for r in rs)
            views = sum(r["views"] or 0 for r in rs)
            counted = sum(r["posts"] for r in rs if r["views"] is not None)
            growth = [r["growth"] for r in rs if r["growth"] is not None]
            out.append({"key": k, "accounts": len(rs), "posts": posts, "views": views if counted else None,
                        "avg": views / counted if counted else None,
                        "growth": sum(growth) if growth else None,
                        "growth_per_acc": sum(growth) / len(growth) if growth else None})
        return sorted(out, key=lambda x: -(x["avg"] or -1))

    plats = agg(lambda r: r["acc"].platform)
    brands = agg(lambda r: r["acc"].brand)

    # head-to-head: the same clip on several platforms of one brand
    h2h: dict = {}
    by_clip: dict = {}
    for v in all_videos:
        if v.get("clip_id") and v.get("views") is not None and parse_iso(v["created"]) >= start - timedelta(days=days):
            by_clip.setdefault((v["clip_id"], v["brand"]), {})[v["platform"]] = v["views"]
    contests = 0
    for res in by_clip.values():
        if len(res) >= 2:
            contests += 1
            winner = max(res, key=res.get)
            h2h[winner] = h2h.get(winner, 0) + 1

    total_posts = sum(p["posts"] for p in plats)
    L = [f"# ClipBot performance — last {days} days",
         f"_Generated {now:%a %d %b %Y %H:%M} UTC · views are counted so far, so very new posts look smaller._", ""]

    L += ["## Verdict", ""]
    ranked = [p for p in plats if p["avg"] is not None and p["posts"] > 0]
    if total_posts < 10 or len(ranked) < 2:
        L.append(f"- Not enough data yet ({total_posts} posts with stats). Give it at least a week of steady posting.")
    else:
        lead = ranked[0]
        best_growth = max((p["growth_per_acc"] or 0) for p in plats) or 0
        L.append(f"- **Winning platform: {NAMES[lead['key']]}**, {_fmt(int(lead['avg']))} views per post on average.")
        for p in ranked[1:]:
            ratio = (p["avg"] or 0) / lead["avg"] if lead["avg"] else 0
            g_ratio = ((p["growth_per_acc"] or 0) / best_growth) if best_growth > 0 else 0
            if p["posts"] >= 10 and ratio < 0.25 and g_ratio < 0.25:
                L.append(f"- **{NAMES[p['key']]} is a cut candidate**: {ratio:.0%} of {NAMES[lead['key']]}'s views per post"
                         f" and {g_ratio:.0%} of the best follower growth, over {p['posts']} posts.")
            else:
                L.append(f"- {NAMES[p['key']]}: {ratio:.0%} of the leader's views per post ({p['posts']} posts).")
        growers = [p for p in plats if p["growth"] is not None]
        if growers:
            g = max(growers, key=lambda p: p["growth_per_acc"] or 0)
            L.append(f"- Most followers gained per account: **{NAMES[g['key']]}** (+{_fmt(int(g['growth_per_acc'] or 0))}).")
        if contests:
            wins = ", ".join(f"{NAMES[k]} {v}" for k, v in sorted(h2h.items(), key=lambda x: -x[1]))
            L.append(f"- Same clip, head-to-head ({contests} clips): {wins}. This is the fairest comparison.")
        if brands and brands[0]["avg"]:
            L.append(f"- Best brand: **{brands[0]['key']}** ({_fmt(int(brands[0]['avg']))} views/post).")
    L.append("")

    L += ["## By platform", "", "| Platform | Accounts | Posts | Views | Views/post | Followers gained |", "|---|---:|---:|---:|---:|---:|"]
    for p in sorted(plats, key=lambda x: PLATFORM_ORDER.index(x["key"])):
        L.append(f"| {NAMES[p['key']]} | {p['accounts']} | {p['posts']} | {_fmt(p['views'])} | "
                 f"{_fmt(int(p['avg'])) if p['avg'] is not None else '–'} | {_fmt(p['growth'])} |")
    L += ["", "## By brand", "", "| Brand | Posts | Views | Views/post | Followers gained |", "|---|---:|---:|---:|---:|"]
    for b in brands:
        L.append(f"| {b['key']} | {b['posts']} | {_fmt(b['views'])} | {_fmt(int(b['avg'])) if b['avg'] is not None else '–'} | {_fmt(b['growth'])} |")
    L += ["", "## By account", "", "| Account | Platform | Posts | Views | Median | Engagement | Followers | Gained | Best post |",
          "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda r: -(r["avg"] or -1)):
        b = r["best"]
        best = f"[{_fmt(b.get('views'))}]({b['url']})" if b and b.get("url") else "–"
        L.append(f"| {r['acc'].id} | {r['acc'].platform} | {r['posts']} | {_fmt(r['views'])} | {_fmt(int(r['median'])) if r['median'] is not None else '–'} | "
                 f"{_fmt(r['eng_rate'])} | {_fmt(r['followers'])} | {_fmt(r['growth'])} | {best} |")
    notes = [f"- {r['acc'].id}: {n}" for r in rows for n in r["notes"]]
    if notes:
        L += ["", "## Missing data", ""] + sorted(set(notes))
    top = sorted([v for v in all_videos if v.get("views") is not None and parse_iso(v["created"]) >= start],
                 key=lambda v: -v["views"])[:10]
    if top:
        L += ["", "## Top 10 posts", "", "| Views | Platform | Account | Post |", "|---:|---|---|---|"]
        for v in top:
            title = (v.get("title") or "").replace("|", " ").replace("\n", " ")[:60] or v["id"]
            L.append(f"| {_fmt(v['views'])} | {v['platform']} | {v['account']} | [{title}]({v.get('url', '')}) |")

    # short chat version
    S = [f"📊 ClipBot weekly ({days}d) — {total_posts} posts"]
    for p in sorted(plats, key=lambda x: -(x["avg"] or -1)):
        S.append(f"{NAMES[p['key']]:10} {_fmt(int(p['avg'])) if p['avg'] is not None else '–':>7} views/post · "
                 f"{p['posts']} posts · " + (f"{p['growth']:+,} followers" if p['growth'] is not None else "followers –"))
    verdict = [line[2:].replace("**", "") for line in L if line.startswith("- ")][:4]
    S += [""] + verdict
    return "\n".join(L) + "\n", "\n".join(S)


def write_report(cfg: Config, days: int = 7, now: datetime | None = None) -> tuple[Path, str]:
    now = now or utcnow()
    md, summary = build_report(cfg, days, now)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "latest.md").write_text(md, encoding="utf-8")
    (REPORTS_DIR / f"{now:%Y-%m-%d}.md").write_text(md, encoding="utf-8")
    with open(REPORTS_DIR / "videos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["platform", "brand", "account", "created", "views", "likes", "comments", "shares", "clip_id", "url", "title"])
        for acc in cfg.accounts.values():
            for v in sorted(load_metrics(acc.id)["videos"].values(), key=lambda v: v["created"], reverse=True):
                w.writerow([acc.platform, acc.brand, acc.id, v["created"], v.get("views"), v.get("likes"),
                            v.get("comments"), v.get("shares"), v.get("clip_id", ""), v.get("url", ""),
                            (v.get("title") or "").replace("\n", " ")])
    return REPORTS_DIR / "latest.md", summary
