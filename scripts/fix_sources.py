"""Repairs sources.yaml entries that older ClipBot versions wrote with broken nested settings
(e.g. a campaign block copied from a watched channel). Usage: python scripts/fix_sources.py"""
import json
import re
from pathlib import Path

import yaml

p = Path("config/sources.yaml")
text = p.read_text(encoding="utf-8")
i = text.index("\nchannels:")
channels = yaml.safe_load(text[i:]).get("channels") or []
by_credit = {c.get("credit"): c for c in channels if c.get("credit")}

lines = text[:i].split("\n")
out, credit, fixed = [], None, 0
block_start = 0
for n, ln in enumerate(lines):
    if re.match(r"\s*- url:", ln):
        credit = None
        # look ahead in this block for its credit line
        for nxt in lines[n + 1:]:
            if re.match(r"\s*- url:", nxt):
                break
            m = re.match(r'\s+credit:\s*"?(.*?)"?\s*$', nxt)
            if m:
                credit = m.group(1)
                break
    m = re.match(r"^(\s+)(campaign|split|games|brands|end_card): (\{.*|\[.*)$", ln)
    if m:
        try:
            yaml.safe_load(f"x: {m.group(3)}")
        except yaml.YAMLError:
            ch = by_credit.get(credit)
            if ch and m.group(2) in ch:
                ln = f"{m.group(1)}{m.group(2)}: {json.dumps(ch[m.group(2)], ensure_ascii=False)}"
                fixed += 1
    out.append(ln)
p.write_text("\n".join(out) + text[i:], encoding="utf-8")
yaml.safe_load(p.read_text(encoding="utf-8"))
print(f"Fixed {fixed} line(s); sources.yaml is valid now.")
