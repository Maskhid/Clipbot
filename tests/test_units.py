from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from conftest import fake_transcript


def test_config_loads_20_accounts():
    from clipbot.config import load_config

    cfg = load_config()
    assert len(cfg.accounts) == 20
    assert len(cfg.brands) == 5
    assert {a.platform for a in cfg.accounts.values()} == {"youtube", "tiktok", "instagram", "facebook"}


def test_window_parse():
    from clipbot.config import ConfigError, parse_window

    assert parse_window("17:00-23:30") == (1020, 1410)
    assert parse_window("22:00-01:00") == (1320, 1500)
    with pytest.raises(ConfigError):
        parse_window("5pm-9pm")


def test_vault_roundtrip(tmp_path):
    from clipbot.vault import Vault, VaultError, generate_key

    k = generate_key()
    v = Vault(key=k, path=tmp_path / "v.enc")
    v.set_account("a", {"refresh_token": "x"})
    v.save()
    assert b"refresh_token" not in (tmp_path / "v.enc").read_bytes()
    assert Vault(key=k, path=tmp_path / "v.enc").account("a")["refresh_token"] == "x"
    with pytest.raises(VaultError):
        Vault(key=generate_key(), path=tmp_path / "v.enc")


def test_scheduler_respects_windows_gaps_and_caps():
    from clipbot.config import load_config
    from clipbot.scheduler import Scheduler

    cfg = load_config()
    acc = cfg.accounts["gta_chaos_yt"]  # 3/day, 12-14 & 17-23 New York
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    s = Scheduler(cfg, {}, {}, now=now, seed="t")
    slots = [s.next_slot(acc, f"clip{i}") for i in range(9)]
    tz = ZoneInfo("America/New_York")
    per_day = {}
    for t in slots:
        lt = t.astimezone(tz)
        m = lt.hour * 60 + lt.minute
        assert 12 * 60 <= m < 14 * 60 or 17 * 60 <= m < 23 * 60, lt
        per_day[lt.date()] = per_day.get(lt.date(), 0) + 1
        assert t > now
    assert max(per_day.values()) <= 3
    ss = sorted(slots)
    assert all(b - a >= timedelta(minutes=90) for a, b in zip(ss, ss[1:]))


def test_same_clip_spread_across_brands():
    from clipbot.config import load_config
    from clipbot.scheduler import Scheduler

    cfg = load_config()
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    s = Scheduler(cfg, {}, {}, now=now, seed="x")
    a = s.next_slot(cfg.accounts["gta_chaos_tt"], "c1")
    b = s.next_slot(cfg.accounts["gta_funny_tt"], "c1")
    assert abs(a - b) >= timedelta(minutes=150)


def test_highlights_find_the_hype():
    from clipbot.highlights import find_highlights

    from clipbot.config import load_config

    cc = load_config().settings["clipping"]
    loud = [-40.0] * 480
    for t in range(120, 128):
        loud[t] = -8.0  # 60-64s loud
    for t in range(300, 306):
        loud[t] = -8.0  # 150-153s loud
    cands = find_highlights(240, loud, fake_transcript(), cc)
    assert cands
    top2 = cands[:2]
    hits = [any(c["start"] <= p <= c["end"] for c in top2) for p in (61, 151)]
    assert all(hits), top2
    for c in cands:
        assert cc["min_clip_seconds"] <= c["end"] - c["start"] <= cc["max_clip_seconds"] + 0.5


def test_captions_ass(tmp_path):
    from clipbot.captions import build_ass

    words = [{"start": 0.2 + i * 0.4, "end": 0.5 + i * 0.4, "word": w} for i, w in enumerate("no way {bro} that's insane".split())]
    p = build_ass(words, 5.0, "HE DID WHAT", {"enabled": True}, 3.0, tmp_path / "c.ass")
    txt = p.read_text()
    assert "Style: Cap" in txt and "HE DID WHAT" in txt
    assert "{bro}" not in txt  # braces escaped
    assert txt.count("Dialogue:") == 1 + len(words)


def test_compose_formats():
    from clipbot.compose import compose
    from clipbot.config import load_config

    cfg = load_config()
    clip = {"title": "He really jumped the bridge", "captions": {"hype": "bro what 😳", "funny": "💀"},
            "hashtags": ["#gta6", "#fyp"], "credit": "@streamer"}
    b = cfg.brands["gta_chaos"]
    yt = compose(clip, b, cfg.accounts["gta_chaos_yt"])
    assert yt["title"].endswith("#Shorts") and len(yt["title"]) <= 100
    assert "@streamer" in yt["description"]
    assert "#gta6" in yt["description"] and yt["description"].count("#gta6") == 1
    tt = compose(clip, b, cfg.accounts["gta_chaos_tt"])
    assert tt["text"].startswith("bro what")
    ig = compose(clip, cfg.brands["gta_funny"], cfg.accounts["gta_funny_ig"])
    assert ig["text"].startswith("💀")


def test_tiktok_chunking():
    from clipbot.platforms.tiktok import MB, _chunks

    assert _chunks(30 * MB) == (30 * MB, 1)
    size, n = _chunks(130 * MB)
    assert size == 10 * MB and n == 13
    # last chunk absorbs remainder and stays under 128MB
    assert 130 * MB - size * (n - 1) < 128 * MB
