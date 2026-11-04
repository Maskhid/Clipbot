"""Finds the best moments in a long video.

Signals (all free, all local):
  * loudness spikes relative to the surrounding minute (screaming, explosions, hype)
  * hype words / phrases in the transcript ("no way", "let's go", "bro"...)
  * bursts of fast talking
Clips are snapped to sentence boundaries so they start and end cleanly.
"""
from __future__ import annotations

import math
import re

HOP = 0.5


def _rolling_median(x: list[float], win: int) -> list[float]:
    import numpy as np  # noqa: PLC0415

    a = np.asarray(x, dtype=float)
    n = len(a)
    if n == 0:
        return []
    half = win // 2
    # strided medians are fine at our sizes (a 3h stream = 21600 hops)
    step = max(1, win // 8)
    centers = list(range(0, n, step))
    meds = [float(np.median(a[max(0, c - half): c + half + 1])) for c in centers]
    return np.interp(range(n), centers, meds).tolist()


def _smooth(x, k: int):
    import numpy as np  # noqa: PLC0415

    if k <= 1 or len(x) == 0:
        return np.asarray(x, dtype=float)
    ker = np.ones(k) / k
    return np.convolve(np.asarray(x, dtype=float), ker, mode="same")


def _tokens(segments: list[dict]) -> list[tuple[float, str]]:
    toks = []
    for s in segments:
        words = s.get("words") or []
        if words:
            for w in words:
                t = re.sub(r"[^\w']+", "", w["word"].lower())
                if t:
                    toks.append((w["start"], t))
        else:  # no word timings: spread segment text evenly
            parts = [re.sub(r"[^\w']+", "", p.lower()) for p in s["text"].split()]
            parts = [p for p in parts if p]
            for i, p in enumerate(parts):
                toks.append((s["start"] + (s["end"] - s["start"]) * i / max(1, len(parts)), p))
    return toks


def score_timeline(duration: float, loud_db: list[float], segments: list[dict], hype_words: list[str]):
    import numpy as np  # noqa: PLC0415

    n = max(1, int(math.ceil(duration / HOP)))
    loud = np.full(n, -60.0)
    m = min(n, len(loud_db))
    loud[:m] = loud_db[:m]
    base = np.asarray(_rolling_median(loud.tolist(), int(60 / HOP)))
    dev = loud - base
    mad = float(np.median(np.abs(dev - np.median(dev)))) or 1.0
    energy = np.clip(dev / (1.4826 * mad), -3, 6)

    hype = np.zeros(n)
    density = np.zeros(n)
    toks = _tokens(segments)
    words_only = [t for _, t in toks]
    phrases = [p.lower().split() for p in hype_words if p.strip()]
    for i, (t, _) in enumerate(toks):
        idx = min(n - 1, int(t / HOP))
        density[idx] += 1
        for ph in phrases:
            if words_only[i:i + len(ph)] == ph:
                hype[idx] += 1.0
        # repeated word ("no no no", "go go go") = excitement
        if i >= 2 and words_only[i] == words_only[i - 1] == words_only[i - 2]:
            hype[idx] += 0.7
    density = _smooth(density, int(4 / HOP))
    if density.std() > 0:
        density = (density - density.mean()) / density.std()
    hype = _smooth(hype, int(3 / HOP)) * (3 / HOP)

    score = 1.0 * energy + 1.6 * np.clip(hype, 0, 4) + 0.4 * np.clip(density, -2, 3)
    return _smooth(score, int(3 / HOP))


def _snap_start(t: float, seg_starts: list[float], lo: float) -> float:
    cands = [s for s in seg_starts if lo <= s <= t + 1.5]
    return max(cands) if cands else max(lo, t)


def _snap_end(t: float, seg_ends: list[float], hi: float) -> float:
    cands = [e for e in seg_ends if t <= e <= hi]
    if cands:
        return min(cands) + 0.35
    under = [e for e in seg_ends if e <= hi and e >= t - 6]
    return (max(under) + 0.35) if under else min(hi, t)


def find_highlights(duration: float, loud_db: list[float], segments: list[dict], cfg: dict) -> list[dict]:
    import numpy as np  # noqa: PLC0415

    mn, mx = float(cfg["min_clip_seconds"]), float(cfg["max_clip_seconds"])
    lead, follow = float(cfg["lead_in_seconds"]), float(cfg["follow_seconds"])
    if duration < mn:
        return []
    target = max(1, min(int(cfg["max_clips_per_source"]), math.ceil(duration / 3600 * cfg["clips_per_hour"])))
    if duration <= mx + 1:
        return [{"start": 0.0, "end": round(duration, 2), "peak": duration / 2, "score": 1.0}]

    score = score_timeline(duration, loud_db, segments, cfg.get("hype_words", []))
    seg_starts = [s["start"] for s in segments]
    seg_ends = [s["end"] for s in segments]

    order = np.argsort(-score)
    peaks: list[int] = []
    min_sep = int(mn / HOP)
    for i in order:
        if len(peaks) >= target * 4:
            break
        if all(abs(int(i) - p) >= min_sep for p in peaks):
            peaks.append(int(i))

    cands = []
    for p in peaks:
        tp = p * HOP
        lo = max(0.0, tp - (mx - follow))
        start = _snap_start(max(0.0, tp - lead), seg_starts, lo) if segments else max(0.0, tp - lead)
        want_end = max(tp + follow, start + mn)
        hi = min(duration, start + mx)
        end = _snap_end(want_end, seg_ends, hi) if segments else min(hi, want_end)
        end = min(end, hi, duration)
        if end - start < mn:
            end = min(duration, start + mn)
            if end - start < mn:
                start = max(0.0, end - mn)
        a, b = int(start / HOP), max(int(start / HOP) + 1, int(end / HOP))
        window = np.sort(score[a:b])[::-1]
        top = window[: max(1, len(window) // 3)]
        cands.append({"start": round(start, 2), "end": round(end, 2), "peak": round(tp, 2),
                      "score": round(float(top.mean()), 3)})

    cands.sort(key=lambda c: -c["score"])
    chosen: list[dict] = []
    for c in cands:
        if all(c["end"] + 2 <= o["start"] or c["start"] >= o["end"] + 2 for o in chosen):
            chosen.append(c)
    return chosen  # ranked best first; caller trims to `target` after optional LLM pass


def clip_target(duration: float, cfg: dict) -> int:
    return max(1, min(int(cfg["max_clips_per_source"]), math.ceil(duration / 3600 * cfg["clips_per_hour"])))


def transcript_slice(segments: list[dict], start: float, end: float) -> str:
    return " ".join(s["text"] for s in segments if s["end"] > start and s["start"] < end).strip()


def words_in(segments: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for s in segments:
        for w in s.get("words") or []:
            if w["start"] >= start - 0.05 and w["end"] <= end + 0.05:
                out.append({"start": round(w["start"] - start, 3), "end": round(w["end"] - start, 3), "word": w["word"]})
    return out
