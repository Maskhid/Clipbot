"""Face-on-top layout only when a real webcam face is on screen."""
import subprocess
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
skdata = pytest.importorskip("skimage.data")


def _video(path: Path, with_face: bool):
    from PIL import Image
    face = path.parent / "face.png"
    Image.fromarray(skdata.astronaut()).resize((300, 300)).save(face)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=12"]
    if with_face:
        cmd += ["-i", str(face), "-filter_complex", "[0:v][1:v]overlay=1560:40"]
    subprocess.run(cmd + ["-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return path


def test_finds_webcam_and_ignores_no_face(tmp_path):
    from clipbot.facecam import find_facecam
    sp = find_facecam(_video(tmp_path / "cam.mp4", True), 0, 12, 1920, 1080)
    x, y, w, h = sp["facecam"]
    assert x < 0.88 < x + w and y < 0.1 < y + h      # box contains the face
    # a configured box in the wrong corner is replaced by the detected one
    sp2 = find_facecam(tmp_path / "cam.mp4", 0, 12, 1920, 1080, given=[0, 0, 0.235, 0.23])
    assert sp2["facecam"][0] > 0.5
    assert find_facecam(_video(tmp_path / "nocam.mp4", False), 0, 12, 1920, 1080) is None
