"""End-to-end: real ffmpeg render of a synthetic stream, then posting to all four platforms
against a fake HTTP API that checks each request has the shape the real API expects."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from conftest import TEST_ROOT, fake_transcript

pytestmark = pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
                                reason="ffmpeg missing")


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.n = 0

    def _id(self):
        self.n += 1
        return str(1000 + self.n)

    def handle(self, method, url, **kw):
        self.calls.append((method, url, {k: v for k, v in kw.items() if k != "data" or not hasattr(v, "read")}))
        data = kw.get("data")
        if hasattr(data, "read"):
            body = data.read()
            assert len(body) > 10_000, "video bytes expected"
        # Google
        if url.startswith("https://oauth2.googleapis.com/token"):
            return Resp(200, {"access_token": "ya29.x", "expires_in": 3599})
        if url.startswith("https://www.googleapis.com/upload/youtube/v3/videos") and method == "POST":
            assert kw["params"]["uploadType"] == "resumable"
            assert kw["json"]["snippet"]["title"]
            assert kw["json"]["snippet"]["categoryId"] == "20"
            return Resp(200, {}, {"Location": "https://upload.example/yt-session"})
        if url == "https://upload.example/yt-session" and method == "PUT":
            assert kw["headers"]["Content-Range"].startswith("bytes 0-")
            return Resp(200, {"id": "yt" + self._id()})
        # TikTok
        if url.startswith("https://open.tiktokapis.com/v2/oauth/token/"):
            return Resp(200, {"access_token": "act.new", "expires_in": 86400, "refresh_token": "rft.new", "refresh_expires_in": 31536000})
        if url.endswith("/v2/post/publish/inbox/video/init/"):
            si = kw["json"]["source_info"]
            assert si["source"] == "FILE_UPLOAD" and si["total_chunk_count"] >= 1
            return Resp(200, {"data": {"publish_id": "p" + self._id(), "upload_url": "https://upload.example/tt"}, "error": {"code": "ok"}})
        if url == "https://upload.example/tt" and method == "PUT":
            assert kw["headers"]["Content-Type"] == "video/mp4"
            return Resp(201, {})
        if url.endswith("/v2/post/publish/status/fetch/"):
            return Resp(200, {"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})
        # Meta
        if "graph.facebook.com" in url and url.endswith("/media") and method == "POST":
            assert data["media_type"] == "REELS" and data["upload_type"] == "resumable" and data["caption"]
            cid = "c" + self._id()
            return Resp(200, {"id": cid, "uri": f"https://rupload.facebook.com/ig-api-upload/v25.0/{cid}"})
        if "rupload.facebook.com" in url:
            assert kw["headers"]["offset"] == "0" and int(kw["headers"]["file_size"]) > 10_000
            assert kw["headers"]["Authorization"].startswith("OAuth ")
            return Resp(200, {"success": True})
        if "graph.facebook.com" in url and method == "GET" and kw.get("params", {}).get("fields") == "status_code,status":
            return Resp(200, {"status_code": "FINISHED"})
        if url.endswith("/media_publish"):
            return Resp(200, {"id": "ig" + self._id()})
        if "graph.facebook.com" in url and kw.get("params", {}).get("fields") == "permalink":
            return Resp(200, {"permalink": "https://www.instagram.com/reel/abc/"})
        if url.endswith("/video_reels") and data.get("upload_phase") == "start":
            vid = "v" + self._id()
            return Resp(200, {"video_id": vid, "upload_url": f"https://rupload.facebook.com/video-upload/v25.0/{vid}"})
        if url.endswith("/video_reels") and data.get("upload_phase") == "finish":
            assert data["video_state"] == "PUBLISHED" and data["description"]
            return Resp(200, {"success": True})
        if "graph.facebook.com" in url and kw.get("params", {}).get("fields") == "status":
            return Resp(200, {"status": {"video_status": "ready"}})
        raise AssertionError(f"unexpected call {method} {url}")


def make_source(path: Path, dur=150):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={dur}",
        "-f", "lavfi", "-i", f"anoisesrc=d={dur}:c=pink:a=0.02", "-f", "lavfi", "-i", f"sine=f=440:d={dur}",
        "-filter_complex", "[2:a]volume='if(between(t,60,64)+between(t,110,113),0.9,0)':eval=frame[s];"
                           "[1:a][s]amix=inputs=2:normalize=0[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-c:a", "aac", str(path)],
        check=True)


def test_clip_and_post_everywhere(monkeypatch):
    from clipbot import pipeline, poster
    from clipbot.config import WORK_DIR, load_config
    from clipbot.state import State, utcnow
    from clipbot.vault import Vault, generate_key

    key = generate_key()
    monkeypatch.setenv("VAULT_KEY", key)
    src_file = TEST_ROOT / "stream.mp4"
    make_source(src_file)

    # sources: one split across 3 brands, one shared to 2 brands
    (TEST_ROOT / "config" / "sources.yaml").write_text(
        f'sources:\n  - url: "{src_file}"\n    brands: [gta_chaos, gta_funny]\n    distribute: all\n'
        f'    max_clips: 2\n    clips_per_hour: 60\n    credit: "@tester"\n')
    cfg = load_config()
    # fewer accounts to keep it quick: disable 3 brands' accounts
    for a in cfg.accounts.values():
        if a.brand not in ("gta_chaos", "gta_funny"):
            a.raw["enabled"] = False

    v = Vault(key=key)
    v.set_app("youtube", {"client_id": "cid", "client_secret": "cs"})
    v.set_app("tiktok", {"client_key": "ck", "client_secret": "cs", "redirect_uri": "https://x"})
    for a in cfg.accounts.values():
        if a.platform == "youtube":
            v.set_account(a.id, {"refresh_token": "1//r"})
        elif a.platform == "tiktok":
            v.set_account(a.id, {"refresh_token": "rft.old", "access_token": "old", "expires_at": 0})
        elif a.platform == "instagram":
            v.set_account(a.id, {"access_token": "EAAP", "ig_user_id": "1784", "page_id": "1"})
        else:
            v.set_account(a.id, {"access_token": "EAAP", "page_id": "123"})
    v.save()

    st = State()
    skey = State.source_key(cfg.sources[0])
    (WORK_DIR / skey).mkdir(parents=True, exist_ok=True)
    (WORK_DIR / skey / "transcript.json").write_text(json.dumps(fake_transcript(150)))

    made = pipeline.run_clip(cfg, st, keep_work=True)
    assert len(made) == 2
    for c in made:
        assert c["shared"] and c["brands"] == ["gta_chaos", "gta_funny"]
        stored = Path(os.environ["CLIPBOT_LOCAL_STORAGE"]) / c["tag"] / c["asset"]
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", str(stored)], capture_output=True, text=True).stdout.strip()
        assert info == "1080,1920"
    jobs = st.jobs()
    assert len(jobs) == 2 * 8  # 2 clips x 2 brands x 4 platforms

    api = FakeAPI()
    import clipbot.platforms.meta as m
    import clipbot.platforms.tiktok as t
    import clipbot.platforms.youtube as y
    for mod in (m, t, y):
        monkeypatch.setattr(mod.requests, "post", lambda url, **kw: api.handle("POST", url, **kw))
        monkeypatch.setattr(mod.requests, "put", lambda url, **kw: api.handle("PUT", url, **kw))
        monkeypatch.setattr(mod.requests, "get", lambda url, **kw: api.handle("GET", url, **kw))
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr("clipbot.poster.notify", lambda *a, **k: True)

    future = utcnow() + timedelta(days=30)
    monkeypatch.setattr(poster, "utcnow", lambda: future)
    s = poster.run_post(cfg, st, dry_run=False)
    assert not s["failed"] and not s["retry"], s
    assert len(s["posted"]) == 12  # max_posts_per_run
    s2 = poster.run_post(cfg, st, dry_run=False)
    assert len(s2["posted"]) == 4
    res = st.results()
    assert all(r["status"] == "posted" for r in res.values())
    assert {r["platform"] for r in res.values()} == {"youtube", "tiktok", "instagram", "facebook"}
    assert any(n.startswith("📥") for n in s["notes"] + s2["notes"])  # TikTok inbox reminder
    # TikTok refresh token rotation was persisted to the vault
    v2 = Vault(key=key)
    assert v2.account("gta_chaos_tt")["refresh_token"] == "rft.new"
    # nothing left
    assert poster.due_jobs(cfg, st, future) == []
