"""Git-friendly state: one small JSON file per thing, so the clip job and the post job
never edit the same file and their commits never conflict.

state/sources_done/<source_key>.json   written by clip job
state/clips/<clip_id>.json             written by clip job
state/queue/<job_id>.json              written by clip job   (job = one clip -> one account)
state/results/<job_id>.json            written by post job   (posted / failed / attempts)
state/vault.enc                        written by post job (token refresh) and auth commands
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import STATE_DIR


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def short_hash(text: str, n: int = 10) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:n]


class State:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or STATE_DIR)
        for sub in ("sources_done", "clips", "queue", "results"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # generic -----------------------------------------------------------
    def _write(self, sub: str, name: str, data: dict):
        p = self.root / sub / f"{name}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)

    def _read(self, sub: str, name: str) -> dict | None:
        p = self.root / sub / f"{name}.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def _iter(self, sub: str) -> Iterator[dict]:
        for p in sorted((self.root / sub).glob("*.json")):
            try:
                yield json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue

    def _delete(self, sub: str, name: str):
        p = self.root / sub / f"{name}.json"
        if p.exists():
            p.unlink()

    # sources -----------------------------------------------------------
    @staticmethod
    def source_key(src: dict) -> str:
        return short_hash(f"{src['url']}|{src.get('start', '')}|{src.get('end', '')}", 12)

    def source_done(self, src: dict) -> dict | None:
        return self._read("sources_done", self.source_key(src))

    def mark_source(self, src: dict, info: dict):
        self._write("sources_done", self.source_key(src), {"url": src["url"], **info})

    # clips -------------------------------------------------------------
    def save_clip(self, clip: dict):
        self._write("clips", clip["id"], clip)

    def clip(self, clip_id: str) -> dict | None:
        return self._read("clips", clip_id)

    def clips(self) -> list[dict]:
        return list(self._iter("clips"))

    def delete_clip(self, clip_id: str):
        self._delete("clips", clip_id)

    # queue -------------------------------------------------------------
    @staticmethod
    def job_id(clip_id: str, account_id: str) -> str:
        return f"{clip_id}__{account_id}"

    def save_job(self, job: dict):
        self._write("queue", job["id"], job)

    def jobs(self) -> list[dict]:
        return list(self._iter("queue"))

    def delete_job(self, job_id: str):
        self._delete("queue", job_id)

    # results -----------------------------------------------------------
    def result(self, job_id: str) -> dict | None:
        return self._read("results", job_id)

    def save_result(self, job_id: str, res: dict):
        self._write("results", job_id, res)

    def results(self) -> dict[str, dict]:
        return {r["job_id"]: r for r in self._iter("results") if "job_id" in r}

    def delete_result(self, job_id: str):
        self._delete("results", job_id)
