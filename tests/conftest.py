"""Tests run against a throwaway copy of the project (config + assets) so they never touch
your real state/ folder or vault."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_tmp = Path(tempfile.mkdtemp(prefix="clipbot-test-"))
shutil.copytree(REPO / "config", _tmp / "config")
shutil.copytree(REPO / "assets", _tmp / "assets")
os.environ["CLIPBOT_ROOT"] = str(_tmp)
os.environ["CLIPBOT_WORK"] = str(_tmp / ".work")
os.environ["CLIPBOT_STORAGE"] = "local"
os.environ["CLIPBOT_LOCAL_STORAGE"] = str(_tmp / ".storage")
sys.path.insert(0, str(REPO))

TEST_ROOT = _tmp


def fake_transcript(duration=240.0):
    """Calm chatter, with hype at ~60s and ~150s."""
    segs = []
    t = 1.0
    calm = "so we are just driving around the city right now looking for a car".split()
    hype1 = "no way bro no way let's go let's go that was insane".split()
    hype2 = "oh my god what was that help help run run run".split()
    while t < duration - 4:
        words = hype1 if 58 <= t <= 64 else hype2 if 148 <= t <= 154 else calm[:8]
        step = 0.32 if words is not calm[:8] else 0.45
        ws = []
        for i, w in enumerate(words):
            s = t + i * step
            ws.append({"start": round(s, 2), "end": round(s + step * 0.85, 2), "word": w})
        segs.append({"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(words), "words": ws})
        t = ws[-1]["end"] + 1.2
    return segs
