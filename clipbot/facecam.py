"""Finds the streamer's webcam in a clip, so the "face on top, game below" layout only
happens when there really is a face to show.

How: grab ~14 frames across the clip, run OpenCV's face detector, and look for a face
that stays in the SAME spot in many frames. A webcam overlay doesn't move; faces of game
characters do, so they don't count. No stable face -> the caller uses a full-screen layout.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .util import log, run

TOP_H = 760          # height of the facecam section in the 1080x1920 output
OUT_W = 1080


def _frames(video: Path, start: float, end: float, n: int, out_dir: Path) -> list[Path]:
    dur = max(1.0, end - start)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(video),
         "-vf", f"fps={n / dur:.4f},scale=960:-2", "-q:v", "3", str(out_dir / "f%02d.jpg")])
    return sorted(out_dir.glob("f*.jpg"))


MODEL = Path(__file__).resolve().parent.parent / "assets" / "models" / "face_detection_yunet.onnx"


def _detect(paths: list[Path]):
    """Faces per frame. Uses OpenCV's YuNet neural face detector (handles headsets, side angles,
    dim webcams); falls back to the older Haar detector if the model file is missing."""
    import cv2  # noqa: PLC0415

    yunet = None
    if MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
        yunet = cv2.FaceDetectorYN.create(str(MODEL), "", (320, 320), 0.72, 0.3, 50)
    else:
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    found = []  # (frame_index, cx, cy, w, h) normalised 0..1
    size = None
    for i, p in enumerate(paths):
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        size = (w, h)
        if yunet is not None:
            yunet.setInputSize((w, h))
            _, faces = yunet.detect(img)
            boxes = [] if faces is None else [f[:4] for f in faces]
        else:
            gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
            boxes = casc.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=6, minSize=(26, 26))
        for (x, y, fw, fh) in boxes:
            if fw < 14 or fh < 14:
                continue
            found.append((i, (x + fw / 2) / w, (y + fh / 2) / h, fw / w, fh / h))
    return found, size


def _central(cx: float, cy: float) -> bool:
    """Webcams sit near an edge/corner; a face in the middle of the screen is a game character."""
    return 0.3 < cx < 0.7 and 0.25 < cy < 0.75


def _stable_face(found, n_frames: int):
    """The face position seen in the most frames (within a small radius), if it's seen often enough."""
    found = [f for f in found if not _central(f[1], f[2])]
    best, best_frames = None, set()
    for _, cx, cy, fw, _ in found:
        r = max(0.035, fw * 0.6)
        near = [f for f in found if abs(f[1] - cx) < r and abs(f[2] - cy) < r]
        frames = {f[0] for f in near}
        if len(frames) > len(best_frames):
            best, best_frames = near, frames
    need = max(3, round(n_frames * 0.35))
    if not best or len(best_frames) < need:
        return None
    med = lambda k: sorted(f[k] for f in best)[len(best) // 2]  # noqa: E731
    return {"cx": float(med(1)), "cy": float(med(2)), "w": float(med(3)), "h": float(med(4)), "seen": len(best_frames)}


def _rect_around(face: dict, aspect_src: float) -> list[float]:
    """A crop (fractions of the frame) around the face, shaped like the 1080x760 top section."""
    target = OUT_W / TOP_H                       # width / height of the output section
    w = min(0.45, max(0.16, face["w"] * 4.6))    # head and shoulders, like top streamer-clip pages
    h = w * aspect_src / target                  # convert so the pixel aspect matches the target
    if h > 0.6:
        h = 0.6
        w = h * target / aspect_src
    x = min(max(0.0, face["cx"] - w / 2), 1 - w)
    y = min(max(0.0, face["cy"] - h * 0.42), 1 - h)  # face a little above the middle
    return [round(float(v), 4) for v in (x, y, w, h)]


def _edge_box(paths: list[Path], face: dict) -> list[float] | None:
    """Finds the webcam overlay's own borders around the face (they stay put in every frame, while game
    edges move), then fits the 1080x760 shape INSIDE them, so no chat box or game spills into the facecam."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    gx_sum = gy_sum = None
    for p in paths:
        g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        g = g.astype(np.float32)
        gx, gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)), np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        gx_sum = gx if gx_sum is None else gx_sum + gx
        gy_sum = gy if gy_sum is None else gy_sum + gy
    if gx_sum is None:
        return None
    H, W = gy_sum.shape
    cx, cy, fw, fh = face["cx"] * W, face["cy"] * H, face["w"] * W, face["h"] * H

    def best(profile, lo, hi):
        lo, hi = int(max(0, lo)), int(min(len(profile) - 1, hi))
        if hi - lo < 3:
            return None
        seg = profile[lo:hi]
        i = int(np.argmax(seg))
        med = float(np.median(profile[max(0, lo - 40):hi + 40])) or 1.0
        return lo + i if seg[i] > 2.2 * med else None
    cols = slice(int(max(0, cx - 1.6 * fw)), int(min(W, cx + 1.6 * fw)))
    rows = slice(int(max(0, cy - 1.6 * fh)), int(min(H, cy + 1.6 * fh)))
    row_prof = gy_sum[:, cols].mean(axis=1)
    col_prof = gx_sum[rows, :].mean(axis=0)
    bottom = best(row_prof, cy + 0.9 * fh, cy + 5 * fh)
    top = best(row_prof, cy - 5 * fh, cy - 0.9 * fh)
    left = best(col_prof, cx - 6 * fw, cx - 0.9 * fw)
    right = best(col_prof, cx + 0.9 * fw, cx + 6 * fw)
    x0 = left if left is not None else max(0, cx - 3 * fw)
    x1 = right if right is not None else min(W, cx + 3 * fw)
    y0 = top if top is not None else max(0, cy - 2.5 * fh)
    y1 = bottom if bottom is not None else min(H, cy + 2.5 * fh)
    if bottom is None and top is None and left is None and right is None:
        return None
    if not (1.8 * fw <= x1 - x0 <= 9 * fw and 1.6 * fh <= y1 - y0 <= 8 * fh):
        return None
    target = OUT_W / TOP_H
    bw, bh = x1 - x0, y1 - y0
    if bw / bh > target:   # cam wider than the slot: trim the sides around the face
        nw = bh * target
        nx = min(max(x0, cx - nw / 2), x1 - nw)
        x0, x1 = nx, nx + nw
    else:                  # cam taller: trim top/bottom, face a little above the middle
        nh = bw / target
        ny = min(max(y0, cy - nh * 0.42), y1 - nh)
        y0, y1 = ny, ny + nh
    pad = 2  # stay just inside the border line
    x0, y0, x1, y1 = x0 + pad, y0 + pad, x1 - pad, y1 - pad
    log.info("Facecam: webcam borders found (top=%s bottom=%s left=%s right=%s)",
             top is not None, bottom is not None, left is not None, right is not None)
    return [round(float(v), 4) for v in (x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H)]


def find_facecam(video: Path, start: float, end: float, src_w: int, src_h: int,
                 given: list[float] | None = None, samples: int = 14) -> dict | None:
    """Returns a `split` config ({"facecam": [x,y,w,h], "top_height": 760}) or None if no webcam face."""
    try:
        import cv2  # noqa: F401, PLC0415
    except ImportError:
        log.warning("Facecam check skipped (opencv not installed)")
        return {"facecam": given, "top_height": TOP_H} if given else None
    with tempfile.TemporaryDirectory() as td:
        paths = _frames(video, start, end, samples, Path(td))
        found, _ = _detect(paths)
        face = _stable_face(found, len(paths))
        if not face:
            log.info("Facecam: no steady face found in %.0f-%.0fs -> full-screen gameplay", start, end)
            return None
        if given:  # configured webcam box: use it if the face really is inside it
            x, y, w, h = given
            if x <= face["cx"] <= x + w and y <= face["cy"] <= y + h:
                log.info("Facecam: face found in the configured box (%d frames)", face["seen"])
                return {"facecam": list(given), "top_height": TOP_H}
        try:
            rect = _edge_box(paths, face)
        except Exception as e:  # noqa: BLE001
            log.warning("Facecam border search failed: %s", e)
            rect = None
    rect = rect or _rect_around(face, src_w / max(1, src_h))
    log.info("Facecam: face at %.2f,%.2f (%d frames) -> box %s", face["cx"], face["cy"], face["seen"], rect)
    return {"facecam": rect, "top_height": TOP_H}
