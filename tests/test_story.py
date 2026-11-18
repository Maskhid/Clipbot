"""Story videos: series memory, scene timing, and a full render with placeholder pictures/voice."""
import subprocess
from pathlib import Path

from clipbot import story


def test_series_memory_runs_three_parts_then_starts_new():
    n = {"format": "series", "parts": 3}
    mem = {}
    for part in (1, 2, 3):
        _, _, p = story._series_block(n, mem)
        assert p == part
        mem = story.update_memory(n, mem, {"part": p, "series_title": "Frozen City", "summary": f"s{p}"})
    assert mem["arc"] == {} and mem["history"][-1] == "Frozen City"
    assert story._series_block(n, mem)[2] == 1


def test_scene_times_follow_the_voice():
    scenes = [{"narration": "one two"}, {"narration": "three four five six"}]
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("one two three four five six".split())]
    st = story._scene_times(scenes, words, 3.0)
    assert st[0] == 0 and abs(st[1] - 1.0) < 0.01


def test_render_story(tmp_path):
    from PIL import Image
    imgs = []
    for i, c in enumerate([(200, 60, 40), (40, 80, 200), (50, 150, 70)]):
        p = tmp_path / f"s{i}.png"
        Image.new("RGB", (512, 512), c).save(p)
        imgs.append(p)
    script = {"title": "T", "series_title": "Frozen City", "part": 2,
              "scenes": [{"narration": "a b c", "image": ""}, {"narration": "d e f", "image": ""}, {"narration": "g h", "image": ""}]}
    words = [{"word": w, "start": 0.2 + i * 0.4, "end": 0.5 + i * 0.4} for i, w in enumerate("a b c d e f g h".split())]
    voice = tmp_path / "v.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=3.6", str(voice)], check=True)
    out = story.render_story({"accent": "#FF5A36"}, script, imgs, voice, words, tmp_path / "o.mp4", tmp_path)
    info = story.ffprobe(out)
    assert info["width"] == 1080 and info["height"] == 1920 and info["duration"] >= 3.6
