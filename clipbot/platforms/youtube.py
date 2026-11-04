"""YouTube Shorts upload via the official YouTube Data API v3 (resumable upload)."""
from __future__ import annotations

import time
from pathlib import Path

import requests

from . import PostError

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
CHUNK = 16 * 1024 * 1024  # multiple of 256 KiB


def access_token(app: dict, creds: dict) -> str:
    if creds.get("access_token") and creds.get("expires_at", 0) > time.time() + 120:
        return creds["access_token"]
    try:
        r = requests.post(TOKEN_URL, timeout=30, data={
            "client_id": app["client_id"], "client_secret": app["client_secret"],
            "refresh_token": creds["refresh_token"], "grant_type": "refresh_token"})
    except requests.RequestException as e:
        raise PostError(f"YouTube token refresh network error: {e}") from e
    j = r.json() if r.content else {}
    if r.status_code != 200:
        if j.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
            raise PostError("YouTube login expired or was revoked. Re-run: python -m clipbot auth youtube "
                            "<account>. (If this happens every 7 days, publish your OAuth consent screen "
                            "to 'In production' — see SETUP.md.)", retryable=False)
        raise PostError(f"YouTube token refresh failed: {r.status_code} {r.text[:300]}")
    creds["access_token"] = j["access_token"]
    creds["expires_at"] = time.time() + int(j.get("expires_in", 3600))
    return creds["access_token"]


def _raise_for(r: requests.Response, what: str):
    try:
        err = r.json().get("error", {})
        reasons = {e.get("reason") for e in err.get("errors", [])}
        msg = err.get("message", r.text[:300])
    except ValueError:
        reasons, msg = set(), r.text[:300]
    permanent = r.status_code in (400, 401) or reasons & {"forbidden", "youtubeSignupRequired", "invalidTitle", "invalidDescription"}
    if reasons & {"quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}:
        permanent = False
    raise PostError(f"YouTube {what} failed ({r.status_code} {','.join(reasons)}): {msg}", retryable=not permanent)


def post(acc, vault, video: Path, meta: dict, settings: dict) -> dict:
    app = vault.app("youtube")
    creds = vault.account(acc.id)
    token = access_token(app, creds)  # Google refresh tokens don't rotate: nothing to save
    size = Path(video).stat().st_size
    body = {
        "snippet": {"title": meta["title"], "description": meta.get("description", ""),
                    "tags": meta.get("tags", []), "categoryId": str(acc.get("category_id", 20))},
        "status": {"privacyStatus": acc.get("privacy", "public"), "selfDeclaredMadeForKids": False,
                   "embeddable": True},
    }
    try:
        r = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
                          timeout=60, headers={"Authorization": f"Bearer {token}",
                                               "X-Upload-Content-Length": str(size),
                                               "X-Upload-Content-Type": "video/mp4"})
    except requests.RequestException as e:
        raise PostError(f"YouTube upload init network error: {e}") from e
    if r.status_code not in (200, 201) or "Location" not in r.headers:
        _raise_for(r, "upload init")
    session = r.headers["Location"]

    offset, tries = 0, 0
    with open(video, "rb") as f:
        while True:
            f.seek(offset)
            chunk = f.read(CHUNK)
            end = offset + len(chunk) - 1
            try:
                pr = requests.put(session, data=chunk, timeout=300, headers={
                    "Authorization": f"Bearer {token}", "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}"})
            except requests.RequestException as e:
                tries += 1
                if tries > 5:
                    raise PostError(f"YouTube upload kept failing: {e}") from e
                time.sleep(5 * tries)
                sr = requests.put(session, timeout=60, headers={"Authorization": f"Bearer {token}",
                                                               "Content-Range": f"bytes */{size}"})
                if sr.status_code == 308:
                    rng = sr.headers.get("Range")
                    offset = int(rng.split("-")[1]) + 1 if rng else 0
                    continue
                pr = sr
            if pr.status_code == 308:
                rng = pr.headers.get("Range")
                offset = int(rng.split("-")[1]) + 1 if rng else 0
                continue
            if pr.status_code in (200, 201):
                vid = pr.json()["id"]
                return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}
            if pr.status_code >= 500 and tries < 5:
                tries += 1
                time.sleep(5 * tries)
                continue
            _raise_for(pr, "upload")
