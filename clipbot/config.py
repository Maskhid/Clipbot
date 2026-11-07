"""Loads and validates config/settings.yaml, config/accounts.yaml and config/sources.yaml."""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("CLIPBOT_ROOT", Path(__file__).resolve().parent.parent))
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"
ASSETS_DIR = ROOT / "assets"
WORK_DIR = Path(os.environ.get("CLIPBOT_WORK", ROOT / ".work"))

PLATFORMS = ("youtube", "tiktok", "instagram", "facebook")

DEFAULT_SETTINGS: dict[str, Any] = {
    "timezone": "Africa/Lagos",
    "clipping": {
        "whisper_model": "base",
        "language": None,
        "min_clip_seconds": 15,
        "max_clip_seconds": 60,
        "clips_per_hour": 8,
        "max_clips_per_source": 12,
        "lead_in_seconds": 9,
        "follow_seconds": 7,
        "layout": "blur",  # blur | crop | split
        "fg_zoom": 1.25,  # blur layout: how much to zoom the gameplay strip
        "crop_x": 0.5,  # crop layout: horizontal focus 0..1
        "split": {"facecam": [0.0, 0.0, 0.25, 0.25], "top_height": 640},
        "fps": "auto",  # keep the stream's frame rate (max 60)
        "crf": 18,
        "render_preset": "fast",
        "graphics": {
            "hook_style": "pill",  # pill (gradient banner) | text (plain box)
            "progress_bar": True,
            "colors": ["#00F0FF", "#8A5CFF", "#FF2E93"],
        },
        "hook_seconds": 3.0,
        "loudnorm": True,
        "hype_words": [
            "no way", "oh my god", "omg", "let's go", "lets go", "bro", "what", "wait",
            "yo", "insane", "crazy", "holy", "dude", "run", "help", "clip that", "clip it",
            "wtf", "oh no", "nah", "yes", "haha", "lol", "lmao", "huh", "why",
        ],
        "caption": {
            "enabled": True,
            "font": "Poppins",
            "size": 112,
            "color": "#FFFFFF",
            "highlight": "#FFE600",
            "outline": 8,
            "words_per_line": 3,
            "uppercase": True,
            "position": 0.70,  # 0 = top, 1 = bottom
        },
    },
    "llm": {
        "provider": "none",  # none | gemini | openai_compatible
        "model": "gemini-2.5-flash",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "LLM_API_KEY",
        "language": "English",
    },
    "posting": {
        "min_gap_minutes": 90,
        "jitter_minutes": 20,
        "lead_minutes": 15,
        "same_clip_spread_minutes": 150,
        "max_posts_per_run": 12,
        "retry_attempts": 3,
        "retry_backoff_minutes": 45,
        "dry_run": False,
    },
    "variants": {
        "zoom": [1.0, 1.06],
        "speed": [1.0, 1.03],
        "saturation": [0.94, 1.08],
        "brightness": [-0.03, 0.03],
    },
    "meta": {"graph_version": "v25.0"},
    "storage": {"backend": "github", "release_prefix": "clips-", "keep_days": 21},
    "notify": {
        "discord_webhook_env": "DISCORD_WEBHOOK",
        "telegram_token_env": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_env": "TELEGRAM_CHAT_ID",
    },
}

DEFAULT_ACCOUNT = {
    "enabled": True,
    "posts_per_day": 3,
    "windows": ["11:00-14:00", "17:00-23:00"],
    "timezone": None,  # falls back to settings.timezone
    "privacy": "public",
    "tiktok_mode": "inbox",  # inbox (works before audit) | direct (needs audited app)
}

DEFAULT_BRAND = {
    "niches": [],
    "caption_style": "hype",
    "hashtags": [],
    "watermark": None,
    "credit_prefix": "🎥",
    "language": None,
}


class ConfigError(Exception):
    pass


def _deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must contain a YAML mapping at the top level")
    return data


@dataclass
class Account:
    id: str
    platform: str
    brand: str
    raw: dict

    def __getattr__(self, item):  # convenient access to raw keys
        try:
            return self.__dict__["raw"][item]
        except KeyError as e:
            raise AttributeError(item) from e

    def get(self, key, default=None):
        return self.raw.get(key, default)


@dataclass
class Brand:
    name: str
    raw: dict
    accounts: list[Account] = field(default_factory=list)

    def get(self, key, default=None):
        return self.raw.get(key, default)


@dataclass
class Config:
    settings: dict
    brands: dict[str, Brand]
    accounts: dict[str, Account]
    sources: list[dict]

    @property
    def tz(self) -> str:
        return self.settings["timezone"]

    def account_tz(self, acc: Account) -> str:
        return acc.get("timezone") or self.tz


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def parse_window(w: str) -> tuple[int, int]:
    """'17:00-23:30' -> (1020, 1410) minutes from midnight. End may wrap past midnight."""
    m = _TIME_RE.match(w.replace(" ", ""))
    if not m:
        raise ConfigError(f"Bad time window '{w}', use HH:MM-HH:MM")
    h1, m1, h2, m2 = map(int, m.groups())
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if end <= start:
        end += 24 * 60
    return start, end


def load_config(config_dir: Path | None = None) -> Config:
    cdir = Path(config_dir or CONFIG_DIR)
    settings = _deep_merge(DEFAULT_SETTINGS, _load_yaml(cdir / "settings.yaml"))
    acc_yaml = _load_yaml(cdir / "accounts.yaml")
    src_yaml = _load_yaml(cdir / "sources.yaml")

    brands: dict[str, Brand] = {}
    accounts: dict[str, Account] = {}
    for bname, braw in (acc_yaml.get("brands") or {}).items():
        braw = braw or {}
        b = Brand(name=bname, raw=_deep_merge(DEFAULT_BRAND, {k: v for k, v in braw.items() if k != "accounts"}))
        for araw in braw.get("accounts") or []:
            araw = _deep_merge(DEFAULT_ACCOUNT, araw)
            aid = araw.get("id")
            plat = (araw.get("platform") or "").lower()
            if not aid:
                raise ConfigError(f"Brand '{bname}' has an account without an id")
            if plat not in PLATFORMS:
                raise ConfigError(f"Account '{aid}': platform must be one of {PLATFORMS}")
            if aid in accounts:
                raise ConfigError(f"Duplicate account id '{aid}'")
            for w in araw.get("windows") or []:
                parse_window(w)
            araw["platform"] = plat
            acc = Account(id=aid, platform=plat, brand=bname, raw=araw)
            accounts[aid] = acc
            b.accounts.append(acc)
        brands[bname] = b

    sources = []
    for i, s in enumerate(src_yaml.get("sources") or []):
        if not s or not s.get("url"):
            raise ConfigError(f"sources.yaml entry #{i + 1} needs a url")
        s = dict(s)
        s.setdefault("distribute", "split")
        if s["distribute"] not in ("split", "all"):
            raise ConfigError(f"Source {s['url']}: distribute must be 'split' or 'all'")
        bl = s.get("brands") or list(brands)
        if isinstance(bl, str):
            bl = [x.strip() for x in bl.split(",") if x.strip()]
        for bn in bl:
            if bn not in brands:
                raise ConfigError(f"Source {s['url']}: unknown brand '{bn}'")
        s["brands"] = bl
        sources.append(s)

    return Config(settings=settings, brands=brands, accounts=accounts, sources=sources)
