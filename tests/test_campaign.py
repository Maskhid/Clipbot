"""Paid clipping campaigns: caption format, platform filter, front-of-queue scheduling."""
from clipbot.compose import compose
from clipbot.config import Account, Brand
from clipbot.writer import campaign_rules

CAMP = {"name": "COD MW4", "platforms": ["instagram", "tiktok"], "mention": "@callofduty",
        "disclosure": "#Ad", "hashtags": ["#MW4", "#CallOfDuty", "#Warzone", "#extra"], "max_hashtags": 3,
        "content_type": "the official MW4 Season 01 trailer", "watermark": False}


def _brand():
    b = Brand(name="peak", raw={"caption_style": "hype", "hashtags": ["#gaming"]}, accounts=[])
    return b


def test_campaign_caption_format():
    clip = {"title": "New MW4 maps #fire", "captions": {"hype": "The new MW4 maps look clean 🔥 #gaming @someone"},
            "campaign": CAMP, "credit": None, "hashtags": ["#gta6"]}
    for plat in ("instagram", "tiktok"):
        acc = Account(id="x", platform=plat, brand="peak", raw={})
        text = compose(clip, _brand(), acc)["text"]
        first, second = text.split("\n")
        assert first == "The new MW4 maps look clean 🔥 @callofduty"
        assert second == "#Ad #MW4 #CallOfDuty #Warzone"  # disclosure first, own line, max 3 more
        assert "#gta6" not in text and "#gaming" not in text


def test_campaign_rules_in_prompt():
    r = campaign_rules(CAMP)
    assert "never 'gameplay'" in r and "TRUE" in r
    assert campaign_rules(None) == ""


def test_campaign_scheduling_platforms_and_priority():
    from clipbot.config import load_config
    from clipbot.pipeline import busy_times, reschedule_all, schedule_clip
    from clipbot.scheduler import Scheduler
    from clipbot.state import State, utcnow, parse_iso

    cfg = load_config()
    state = State()
    bname = next(iter(cfg.brands))
    plats = {a.platform for a in cfg.brands[bname].accounts}
    now = utcnow()
    busy, ct, _ = busy_times(state, now)
    sched = Scheduler(cfg, busy, ct, now=now)
    normal = {"id": "kai-00-aaaa", "brands": [bname]}
    state.save_clip(normal)
    schedule_clip(cfg, state, sched, normal)
    camp = {"id": "zzcod-00-bbbb", "brands": [bname], "campaign": {**CAMP}}
    state.save_clip(camp)
    jobs = schedule_clip(cfg, state, sched, camp)
    assert {j["platform"] for j in jobs} == plats & {"instagram", "tiktok"}

    reschedule_all(cfg, state)
    by = {}
    for j in state.jobs():
        by.setdefault(j["account"], {})[j["clip_id"]] = parse_iso(j["due_at"])
    try:
        for acc, d in by.items():
            if "zzcod-00-bbbb" in d:
                assert d["zzcod-00-bbbb"] < d["kai-00-aaaa"], acc  # campaign clip goes first
    finally:  # leave the shared test state clean for the other tests
        for j in state.jobs():
            if j["clip_id"] in ("kai-00-aaaa", "zzcod-00-bbbb"):
                state.delete_job(j["id"])
        state.delete_clip("kai-00-aaaa")
        state.delete_clip("zzcod-00-bbbb")


def test_gemini_falls_back_to_listed_models(monkeypatch):
    import clipbot.writer as w

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, str(body)

        def json(self):
            return self._b

    tried = []

    def post(url, **kw):
        m = url.split("/models/")[1].split(":")[0]
        tried.append(m)
        if m == "gemini-9.9-flash":
            return R(200, {"candidates": [{"content": {"parts": [{"text": "{\"ok\":1}"}]}}]})
        return R(404, {"error": "gone"})

    def get(url, **kw):
        return R(200, {"models": [{"name": "models/gemini-9.9-flash", "supportedGenerationMethods": ["generateContent"]},
                                  {"name": "models/gemini-9.9-flash-image", "supportedGenerationMethods": ["generateContent"]}]})

    monkeypatch.setattr(w.requests, "post", post)
    monkeypatch.setattr(w.requests, "get", get)
    assert w._call_gemini("gemini-2.5-flash", "k", "hi") == "{\"ok\":1}"
    assert tried[-1] == "gemini-9.9-flash" and "gemini-9.9-flash-image" not in tried


LHM = {"name": "LHM", "platforms": ["instagram", "tiktok", "youtube"],
       "mention": {"instagram": "@lethimmansplain", "tiktok": "@lethimmansplain", "youtube": "@LetHimMansplain"},
       "first_line": "Full episode on {mention}", "hashtags": ["#lethimmansplain", "#hottakes", "#podcast"]}


def test_first_line_mention_per_platform_no_ad():
    clip = {"title": "He had an answer for everything", "captions": {"hype": "He had an answer for EVERYTHING 💀 #fyp"},
            "campaign": LHM}
    ig = compose(clip, _brand(), Account(id="i", platform="instagram", brand="peak", raw={}))["text"]
    assert ig.split("\n") == ["Full episode on @lethimmansplain", "He had an answer for EVERYTHING 💀",
                              "#lethimmansplain #hottakes #podcast"]
    yt = compose(clip, _brand(), Account(id="y", platform="youtube", brand="peak", raw={}))
    assert yt["description"].startswith("Full episode on @LetHimMansplain")
    assert "@" not in yt["title"] and "#Ad" not in yt["description"]


def test_logo_overlay_renders(tmp_path):
    import subprocess
    from PIL import Image
    from clipbot import render
    from clipbot.util import ffprobe
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-shortest", str(src)], check=True)
    logo = tmp_path / "logo.png"
    img = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (50, 50, 350, 150))
    img.save(logo)
    out = tmp_path / "o.mp4"
    cfg = {"layout": "blur", "fps": 30, "render_preset": "ultrafast", "graphics": {"progress_bar": False},
           "logo_overlay": {"path": str(logo), "width": 200, "position": "top-right", "y": 230}}
    render.render_clip(src, ffprobe(src), 0, 2.5, None, out, cfg)
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2.3", "-i", str(out), "-frames:v", "1", str(frame)], check=True)
    px = Image.open(frame).convert("RGB").getpixel((1080 - 40 - 100, 230 + 50))
    assert px[0] > 180 and px[1] < 90  # red logo still visible at the end of the clip
