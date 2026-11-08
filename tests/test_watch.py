from datetime import datetime, timezone

from conftest import TEST_ROOT

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class FakeYDL:
    vods = {
        "https://www.twitch.tv/videos/111": {"title": "GTA 6 DAY 1!!", "timestamp": NOW.timestamp() - 3600 * 5, "duration": 8 * 3600},
        "https://www.twitch.tv/videos/110": {"title": "old stream", "timestamp": NOW.timestamp() - 3600 * 80, "duration": 6 * 3600},
        "https://www.twitch.tv/videos/112": {"title": "LIVE NOW", "timestamp": NOW.timestamp(), "duration": None, "is_live": True},
    }

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if "videos?filter=archives" in url:
            return {"entries": [{"url": "112"}, {"url": "https://www.twitch.tv/videos/111"}, {"url": "110"}]}
        v = self.vods[url]
        return {"webpage_url": url, **v}


def test_watch_adds_newest_finished_stream(monkeypatch):
    import clipbot.watch as w
    from clipbot.config import load_config
    from clipbot.state import State

    p = TEST_ROOT / "config" / "sources.yaml"
    p.write_text('sources:\n\nchannels:\n  - url: "https://www.twitch.tv/kaicenat"\n    brands: [gta_chaos]\n'
                 '    credit: "@KaiCenat on Twitch"\n    layout: split\n'
                 '    split: {facecam: [0.0, 0.0, 0.235, 0.23], top_height: 760}\n    max_clips: 5\n')
    monkeypatch.setattr(w, "CONFIG_DIR", TEST_ROOT / "config")
    cfg = load_config()
    st = State(TEST_ROOT / "state_watch")
    added = w.run_watch(cfg, st, now=NOW, ydl_factory=FakeYDL)
    assert added == ["https://www.twitch.tv/videos/111"]  # live one skipped, old one too old
    cfg2 = load_config()
    src = cfg2.sources[0]
    assert src["url"] == "https://www.twitch.tv/videos/111"
    assert src["start"] == "00:30:00" and src["end"] == "02:30:00"
    assert src["layout"] == "split" and src["split"]["top_height"] == 760 and src["max_clips"] == 5
    assert src["brands"] == ["gta_chaos"] and src["credit"] == "@KaiCenat on Twitch"
    assert len(cfg2.channels) == 1
    # second run: nothing new
    assert w.run_watch(cfg2, st, now=NOW, ydl_factory=FakeYDL) == []


def test_gameplay_check(monkeypatch):
    import json
    import clipbot.writer as w
    from clipbot.config import load_config

    s = load_config().settings
    s["llm"]["provider"] = "gemini"
    monkeypatch.setenv("LLM_API_KEY", "k")
    answers = iter([{"gameplay_frames": 7, "game": "Grand Theft Auto VI"}, {"gameplay_frames": 1, "game": ""},
                    {"gameplay_frames": 8, "game": "Fortnite"}])
    monkeypatch.setattr(w, "_call_gemini", lambda *a, **k: json.dumps(next(answers)))
    f = [b"x"] * 8
    assert w.ai_is_gaming(f, "GTA 6 DAY 1", ["GTA", "Grand Theft Auto"], s) == (True, "Grand Theft Auto VI")
    assert w.ai_is_gaming(f, "just chatting w chat", [], s)[0] is False
    assert w.ai_is_gaming(f, "fortnite w the boys", ["GTA", "Grand Theft Auto"], s)[0] is False
    # no AI available -> title keywords decide
    monkeypatch.setenv("LLM_API_KEY", "")
    assert w.ai_is_gaming(f, "PLAYING EVERY GTA", ["GTA"], s) == (True, "")
    assert w.ai_is_gaming(f, "IRL stream", ["GTA"], s) == (None, "")
