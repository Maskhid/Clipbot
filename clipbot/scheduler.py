"""Picks posting times: each account posts `posts_per_day` times inside its time windows
(in the audience's time zone), at least `min_gap_minutes` apart, with random jitter so
nothing looks robotic. The same clip on different brands is spread out in time."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Account, Config, parse_window


def _day_slots(day: datetime, acc: Account, tz: ZoneInfo, rnd: random.Random, jitter: int) -> list[datetime]:
    """Evenly spread posts_per_day slots over the account's windows for one local day."""
    windows = [parse_window(w) for w in (acc.get("windows") or ["00:00-23:59"])]
    total = sum(e - s for s, e in windows)
    n = max(0, int(acc.get("posts_per_day", 3)))
    if n == 0 or total <= 0:
        return []
    slots = []
    for i in range(n):
        pos = total * (i + 0.5) / n  # center of each equal share
        for s, e in windows:
            if pos <= e - s:
                minute = s + pos + rnd.uniform(-jitter, jitter)
                minute = min(max(minute, s), e - 1)
                base = datetime(day.year, day.month, day.day, tzinfo=tz)
                slots.append((base + timedelta(minutes=minute)).astimezone(timezone.utc))
                break
            pos -= e - s
    return slots


class Scheduler:
    def __init__(self, cfg: Config, busy: dict[str, list[datetime]], clip_times: dict[str, list[tuple[datetime, str]]] | None = None,
                 now: datetime | None = None, seed: str = ""):
        self.cfg = cfg
        self.busy = {k: sorted(v) for k, v in busy.items()}  # account -> taken times (utc)
        self.clip_times = clip_times or {}  # clip_id -> [(time, brand)] already planned
        self.now = now or datetime.now(timezone.utc)
        self.rnd = random.Random(seed or self.now.isoformat())
        p = cfg.settings["posting"]
        self.gap = timedelta(minutes=int(p["min_gap_minutes"]))
        self.jitter = int(p["jitter_minutes"])
        self.lead = timedelta(minutes=int(p["lead_minutes"]))
        self.spread = timedelta(minutes=int(p["same_clip_spread_minutes"]))

    def next_slot(self, acc: Account, clip_id: str, not_before: datetime | None = None,
                  horizon_days: int = 60) -> datetime | None:
        tz = ZoneInfo(self.cfg.account_tz(acc))
        earliest = max(self.now + self.lead, not_before or self.now)
        taken = self.busy.setdefault(acc.id, [])
        others = self.clip_times.setdefault(clip_id, [])
        # windows may be wider than a day share; also count posts already planned per local day
        per_day: dict = {}
        for t in taken:
            d = t.astimezone(tz).date()
            per_day[d] = per_day.get(d, 0) + 1
        local0 = earliest.astimezone(tz)
        for d in range(horizon_days):
            day = (local0 + timedelta(days=d)).date()
            if per_day.get(day, 0) >= int(acc.get("posts_per_day", 3)):
                continue
            for slot in sorted(_day_slots(datetime(day.year, day.month, day.day), acc, tz, self.rnd, self.jitter)):
                if slot < earliest:
                    continue
                if any(abs(slot - t) < self.gap for t in taken):
                    continue
                if any(b != acc.brand and abs(slot - t) < self.spread for t, b in others):
                    continue
                taken.append(slot)
                taken.sort()
                others.append((slot, acc.brand))
                return slot
        return None
