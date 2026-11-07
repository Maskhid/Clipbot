from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("clipbot")


def setup_logging(verbose: bool = False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def need(binary: str):
    if not shutil.which(binary):
        raise RuntimeError(f"'{binary}' is not installed or not on PATH")


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(map(str, cmd)))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-2000:]
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed ({r.returncode}):\n{tail}")
    return r


def ffprobe(path: Path) -> dict:
    need("ffprobe")
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    data = json.loads(r.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    return {
        "duration": dur,
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "has_audio": a is not None,
        "fps": _fps((v or {}).get("avg_frame_rate") or (v or {}).get("r_frame_rate")),
        "size": int(data.get("format", {}).get("size") or Path(path).stat().st_size),
    }


def _fps(rate) -> float:
    try:
        n, d = str(rate).split("/")
        return float(n) / float(d) if float(d) else 30.0
    except (ValueError, ZeroDivisionError):
        return 30.0


def retry(fn, attempts: int = 3, delay: float = 3.0, what: str = "request"):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                log.warning("%s failed (%s), retrying in %.0fs", what, str(e)[:200], delay * (i + 1))
                time.sleep(delay * (i + 1))
    raise last  # type: ignore[misc]
