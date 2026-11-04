"""Burned-in captions as an .ass subtitle file: big bold words, current word highlighted
and popped (the style that performs on Shorts/TikTok/Reels), plus a hook banner on top."""
from __future__ import annotations

import re
from pathlib import Path

W, H = 1080, 1920


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text: str, width: int = 18) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return r"\N".join(lines[:3])


def group_words(words: list[dict], per_line: int, max_chars: int = 16) -> list[list[dict]]:
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            chars = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            if len(cur) >= per_line or gap > 0.5 or chars > max_chars or re.search(r"[.?!]$", cur[-1]["word"]):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_ass(words: list[dict], duration: float, hook: str | None, cap: dict, hook_seconds: float,
              out: Path, font: str | None = None) -> Path:
    font = font or cap.get("font", "Poppins")
    size = int(cap.get("size", 112))
    margin_v = int((1 - float(cap.get("position", 0.7))) * H)
    white = ass_color(cap.get("color", "#FFFFFF"))
    hi = ass_color(cap.get("highlight", "#FFE600"))
    outline = int(cap.get("outline", 8))
    upper = bool(cap.get("uppercase", True))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{white},{white},&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,{outline},3,2,60,60,{margin_v},1
Style: Hook,{font},70,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,18,0,8,80,80,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook and hook_seconds > 0:
        lines.append(f"Dialogue: 2,{ts(0.05)},{ts(min(hook_seconds, duration))},Hook,,0,0,0,,"
                     f"{{\\fad(120,220)}}{wrap(esc(hook.upper()), 20)}")

    if cap.get("enabled", True) and words:
        groups = group_words(words, int(cap.get("words_per_line", 3)))
        for gi, g in enumerate(groups):
            g_start = g[0]["start"]
            nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else duration
            g_end = min(nxt, g[-1]["end"] + 0.6, duration)
            if g_end <= g_start:
                continue
            texts = [esc(w["word"].upper() if upper else w["word"]) for w in g]
            for j, w in enumerate(g):
                a = g_start if j == 0 else w["start"]
                b = g[j + 1]["start"] if j + 1 < len(g) else g_end
                if b <= a:
                    continue
                parts = []
                for k, t in enumerate(texts):
                    if k == j:
                        parts.append(f"{{\\c{hi}\\fscx100\\fscy100\\t(0,90,\\fscx114\\fscy114)\\t(90,170,\\fscx108\\fscy108)}}{t}{{\\r}}")
                    else:
                        parts.append(t)
                lines.append(f"Dialogue: 1,{ts(a)},{ts(b)},Cap,,0,0,0,,{' '.join(parts)}")

    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return out
