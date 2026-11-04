"""Uploaders. Each module exposes `post(acc, vault, video, meta, settings) -> {"id", "url", "note"?}`
and raises PostError on failure."""
from __future__ import annotations


class PostError(Exception):
    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


# Conservative per-account caps per rolling 24h (platform hard limits are higher for some).
DAILY_CAP = {"youtube": 15, "tiktok": 5, "instagram": 25, "facebook": 25}


def get_uploader(platform: str):
    if platform == "youtube":
        from . import youtube as m
    elif platform == "tiktok":
        from . import tiktok as m
    elif platform in ("instagram", "facebook"):
        from . import meta as m
    else:
        raise ValueError(platform)
    return m
