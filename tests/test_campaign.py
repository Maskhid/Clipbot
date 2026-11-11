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
    for acc, d in by.items():
        if "zzcod-00-bbbb" in d:
            assert d["zzcod-00-bbbb"] < d["kai-00-aaaa"], acc  # campaign clip goes first
