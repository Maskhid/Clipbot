"""Stats collection against fake platform APIs, matching to posted clips, and the report."""
from datetime import datetime, timedelta, timezone


class R:
    def __init__(self, body):
        self._b = body
        self.status_code = 200
        self.content = b"{}"

    def json(self):
        return self._b


NOW = datetime(2026, 12, 7, 6, 0, tzinfo=timezone.utc)


def ts(days_ago, hours=0):
    return (NOW - timedelta(days=days_ago, hours=hours)).isoformat().replace("+00:00", "Z")


def fake(method, url, params=None, **kw):
    params = params or {}
    # tokens
    if "oauth2.googleapis.com" in url:
        return R({"access_token": "a", "expires_in": 3600})
    if url.endswith("/v2/oauth/token/"):
        return R({"access_token": "a", "expires_in": 86400, "refresh_token": "r2"})
    # youtube
    if url.endswith("/channels"):
        return R({"items": [{"statistics": {"subscriberCount": "1500"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
    if url.endswith("/playlistItems"):
        return R({"items": [{"contentDetails": {"videoId": "yt1", "videoPublishedAt": ts(2)}},
                            {"contentDetails": {"videoId": "yt2", "videoPublishedAt": ts(3)}}]})
    if url.endswith("/videos") and "googleapis" in url:
        return R({"items": [
            {"id": "yt1", "snippet": {"publishedAt": ts(2), "title": "bridge jump"}, "statistics": {"viewCount": "12000", "likeCount": "900", "commentCount": "40"}},
            {"id": "yt2", "snippet": {"publishedAt": ts(3), "title": "cop chase"}, "statistics": {"viewCount": "3000", "likeCount": "100", "commentCount": "5"}}]})
    # tiktok
    if url.endswith("/v2/user/info/"):
        return R({"data": {"user": {"follower_count": 4200}}, "error": {"code": "ok"}})
    if url.endswith("/v2/video/list/"):
        t = int((NOW - timedelta(days=2, hours=-2)).timestamp())
        return R({"data": {"videos": [{"id": 7001, "create_time": t, "title": "x", "view_count": 50000, "like_count": 5000,
                                       "comment_count": 100, "share_count": 300, "share_url": "https://tiktok.com/@a/video/7001?x=1"}],
                           "has_more": False}, "error": {"code": "ok"}})
    # instagram
    if url.endswith("/1784") and params.get("fields") == "followers_count":
        return R({"followers_count": 800})
    if url.endswith("/1784/media"):
        return R({"data": [{"id": "ig1", "timestamp": ts(2).replace("Z", "+0000"), "like_count": 60, "comments_count": 3,
                            "media_product_type": "REELS", "permalink": "https://instagram.com/reel/1"}]})
    if url.endswith("/ig1/insights"):
        return R({"data": [{"name": "views", "values": [{"value": 2000}]}, {"name": "shares", "values": [{"value": 9}]}]})
    # facebook
    if url.endswith("/123") and "followers_count" in params.get("fields", ""):
        return R({"followers_count": 300})
    if url.endswith("/123/video_reels"):
        return R({"data": [{"id": "fb1", "created_time": ts(2).replace("Z", "+0000")}]})
    if url.endswith("/fb1/video_insights"):
        return R({"data": [{"name": "blue_reels_play_count", "values": [{"value": 400}]}]})
    if url.endswith("/fb1"):
        return R({"likes": {"summary": {"total_count": 7}}, "comments": {"summary": {"total_count": 1}}})
    raise AssertionError(f"unexpected {method} {url} {params}")


def test_collect_and_report(monkeypatch, tmp_path):
    import clipbot.analytics as an
    import clipbot.platforms.tiktok as t
    import clipbot.platforms.youtube as y
    from clipbot.config import load_config
    from clipbot.state import State
    from clipbot.vault import Vault, generate_key

    monkeypatch.setattr(an, "METRICS_DIR", tmp_path / "metrics")
    monkeypatch.setattr(an, "REPORTS_DIR", tmp_path / "reports")
    for mod in (an, t, y):
        monkeypatch.setattr(mod.requests, "get", lambda url, **kw: fake("GET", url, **kw))
        monkeypatch.setattr(mod.requests, "post", lambda url, **kw: fake("POST", url, **kw))

    cfg = load_config()
    v = Vault(key=generate_key(), path=tmp_path / "v.enc")
    v.set_app("youtube", {"client_id": "c", "client_secret": "s"})
    v.set_app("tiktok", {"client_key": "c", "client_secret": "s", "redirect_uri": "x"})
    v.set_account("gta_chaos_yt", {"refresh_token": "r"})
    v.set_account("gta_chaos_tt", {"refresh_token": "r", "access_token": "a", "expires_at": 0})
    v.set_account("gta_chaos_ig", {"access_token": "t", "ig_user_id": "1784", "page_id": "123"})
    v.set_account("gta_chaos_fb", {"access_token": "t", "page_id": "123"})

    st = State(tmp_path / "state")
    # ClipBot posted clip c1 to all four; TikTok went to the inbox (no id) 2 days ago
    for acc, rid, hours in (("gta_chaos_yt", "yt1", 0), ("gta_chaos_ig", "ig1", 0), ("gta_chaos_fb", "fb1", 0),
                            ("gta_chaos_tt", "p_inbox", 1)):
        jid = f"c1__{acc}"
        st.save_result(jid, {"job_id": jid, "account": acc, "clip_id": "c1", "platform": acc.split("_")[-1],
                             "status": "posted", "remote_id": rid, "posted_at": ts(2, hours + 0.5 if hours else 0)})

    out = an.collect(cfg, v, st, now=NOW)
    assert sorted(out["ok"]) == ["gta_chaos_fb", "gta_chaos_ig", "gta_chaos_tt", "gta_chaos_yt"], out
    m = an.load_metrics("gta_chaos_tt")
    assert m["videos"]["7001"]["clip_id"] == "c1" and m["videos"]["7001"]["match"] == "time"
    assert an.load_metrics("gta_chaos_yt")["videos"]["yt1"]["clip_id"] == "c1"
    assert an.load_metrics("gta_chaos_fb")["videos"]["fb1"]["views"] == 400

    # a week-old snapshot so follower growth shows
    import json
    p = tmp_path / "metrics" / "gta_chaos_tt.json"
    d = json.loads(p.read_text())
    d["followers"].insert(0, {"date": (NOW - timedelta(days=8)).date().isoformat(), "followers": 3000})
    p.write_text(json.dumps(d))

    path, summary = an.write_report(cfg, days=7, now=NOW)
    md = path.read_text()
    assert "By platform" in md and "TikTok" in md
    assert "head-to-head" in md.lower() or "Not enough data" in md
    assert "+1,200 followers" in summary  # TikTok growth
    assert (tmp_path / "reports" / "videos.csv").read_text().count("\n") == 6
    print(md)
    print(summary)
