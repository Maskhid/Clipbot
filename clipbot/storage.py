"""Where rendered clips wait until they are posted.

github: assets on GitHub Releases of your own repo (free, no size cap on the repo itself,
        2 GB per file). Uses the `gh` CLI that is preinstalled on GitHub runners.
local:  a folder on disk (for testing on your own computer).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT


class StorageError(Exception):
    pass


class LocalStorage:
    def __init__(self, base: Path | None = None):
        self.base = Path(base or os.environ.get("CLIPBOT_LOCAL_STORAGE", ROOT / ".storage"))

    def put(self, path: Path, tag: str) -> str:
        d = self.base / tag
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, d / Path(path).name)
        return Path(path).name

    def get(self, name: str, tag: str, dest_dir: Path) -> Path:
        src = self.base / tag / name
        if not src.exists():
            raise StorageError(f"Missing stored clip {tag}/{name}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / name
        shutil.copy2(src, out)
        return out

    def public_url(self, name: str, tag: str) -> str:
        return str(self.base / tag / name)

    def list_tags(self) -> list[dict]:
        if not self.base.exists():
            return []
        out = []
        for d in self.base.iterdir():
            if d.is_dir():
                created = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                out.append({"tag": d.name, "created": created})
        return out

    def delete_tag(self, tag: str):
        shutil.rmtree(self.base / tag, ignore_errors=True)


class GitHubStorage:
    """Releases on the current repo. Requires GH_TOKEN (GITHUB_TOKEN works in Actions)."""

    def __init__(self, repo: str | None = None):
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY")
        if not shutil.which("gh"):
            raise StorageError("The GitHub CLI (gh) is required for storage.backend=github")
        if not self.repo:
            raise StorageError("GITHUB_REPOSITORY is not set (it is set automatically inside GitHub Actions)")

    def _gh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(["gh", *args, "--repo", self.repo], capture_output=True, text=True)
        if check and r.returncode != 0:
            raise StorageError(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:500]}")
        return r

    def _ensure_release(self, tag: str):
        r = self._gh("release", "view", tag, check=False)
        if r.returncode != 0:
            self._gh("release", "create", tag, "--title", tag, "--notes",
                     "Rendered clips waiting to be posted by ClipBot. Safe to delete after posting.",
                     "--prerelease")

    def put(self, path: Path, tag: str) -> str:
        self._ensure_release(tag)
        self._gh("release", "upload", tag, str(path), "--clobber")
        return Path(path).name

    def get(self, name: str, tag: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / name
        if out.exists():
            return out
        self._gh("release", "download", tag, "--pattern", name, "--dir", str(dest_dir), "--clobber")
        if not out.exists():
            raise StorageError(f"Download of {tag}/{name} produced no file")
        return out

    def public_url(self, name: str, tag: str) -> str:
        return f"https://github.com/{self.repo}/releases/download/{tag}/{name}"

    def list_tags(self) -> list[dict]:
        r = self._gh("release", "list", "--limit", "500", "--json", "tagName,createdAt")
        out = []
        for item in json.loads(r.stdout or "[]"):
            created = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00"))
            out.append({"tag": item["tagName"], "created": created})
        return out

    def delete_tag(self, tag: str):
        self._gh("release", "delete", tag, "--yes", "--cleanup-tag", check=False)


def get_storage(settings: dict):
    backend = os.environ.get("CLIPBOT_STORAGE") or settings["storage"]["backend"]
    if backend == "local":
        return LocalStorage()
    if backend == "github":
        return GitHubStorage()
    raise StorageError(f"Unknown storage backend '{backend}'")
