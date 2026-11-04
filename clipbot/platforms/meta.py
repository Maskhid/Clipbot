"""Instagram Reels and Facebook Page Reels via Meta's official Graph API.

Both upload the local file straight to rupload.facebook.com, so no public video URL is needed.
Tokens are Page access tokens made from a long-lived user token, which don't expire unless
you change your password or remove the app.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from . import PostError

RATE_CODES = {4, 17, 32, 341, 613, 80001, 80002}
AUTH_CODES = {102, 190, 200, 10}


def _g(settings: dict) -> str:
    return f"https://graph.facebook.com/{settings['meta']['graph_version']}"


def _check(r: requests.Response, what: str) -> dict:
    try:
        j = r.json()
    except ValueError as e:
        raise PostError(f"Meta {what}: bad response {r.status_code} {r.text[:200]}") from e
    if r.status_code >= 400 or "error" in j:
        e = j.get("error", {})
        code = e.get("code")
        msg = e.get("error_user_msg") or e.get("message") or r.text[:300]
        retry = code in RATE_CODES or r.status_code >= 500 or e.get("is_transient", False)
        if code in AUTH_CODES:
            msg += " — reconnect with: python -m clipbot auth meta (see SETUP.md)"
        raise PostError(f"Meta {what} failed (code {code}): {msg}", retryable=retry)
    return j


def _rupload(url: str, token: str, video: Path):
    size = Path(video).stat().st_size
    with open(video, "rb") as f:
        try:
            r = requests.post(url, data=f, timeout=600, headers={
                "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                "Content-Type": "application/octet-stream"})
        except requests.RequestException as e:
            raise PostError(f"Meta video upload network error: {e}") from e
    _check(r, "video upload")


def post_instagram(acc, vault, video: Path, meta: dict, settings: dict) -> dict:
    creds = vault.account(acc.id)
    token, ig = creds["access_token"], creds["ig_user_id"]
    G = _g(settings)
    j = _check(requests.post(f"{G}/{ig}/media", timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": meta["text"],
        "share_to_feed": "true", "access_token": token}), "IG container")
    cid = j["id"]
    uri = j.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{settings['meta']['graph_version']}/{cid}"
    _rupload(uri, token, video)

    status = ""
    for _ in range(60):  # up to ~10 min of processing
        time.sleep(10)
        s = _check(requests.get(f"{G}/{cid}", timeout=30, params={"fields": "status_code,status", "access_token": token}), "IG status")
        status = s.get("status_code", "")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            raise PostError(f"Instagram rejected the video: {s.get('status')}", retryable=False)
    else:
        raise PostError("Instagram processing timed out")

    pub = _check(requests.post(f"{G}/{ig}/media_publish", timeout=60,
                               data={"creation_id": cid, "access_token": token}), "IG publish")
    mid = pub["id"]
    url = ""
    try:
        url = requests.get(f"{G}/{mid}", timeout=30, params={"fields": "permalink", "access_token": token}).json().get("permalink", "")
    except (requests.RequestException, ValueError):
        pass
    return {"id": mid, "url": url}


def post_facebook(acc, vault, video: Path, meta: dict, settings: dict) -> dict:
    creds = vault.account(acc.id)
    token, page = creds["access_token"], creds["page_id"]
    G = _g(settings)
    j = _check(requests.post(f"{G}/{page}/video_reels", timeout=60,
                             data={"upload_phase": "start", "access_token": token}), "FB reel start")
    vid = j["video_id"]
    _rupload(j.get("upload_url") or f"https://rupload.facebook.com/video-upload/{settings['meta']['graph_version']}/{vid}", token, video)
    _check(requests.post(f"{G}/{page}/video_reels", timeout=60, data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": meta["text"], "title": meta.get("title", "")[:250], "access_token": token}), "FB reel publish")
    for _ in range(18):
        time.sleep(10)
        try:
            st = requests.get(f"{G}/{vid}", timeout=30, params={"fields": "status", "access_token": token}).json().get("status", {})
        except (requests.RequestException, ValueError):
            continue
        perr = (st.get("processing_phase") or {}).get("error")
        if perr:
            raise PostError(f"Facebook rejected the reel: {perr.get('message')}", retryable=False)
        if (st.get("publishing_phase") or {}).get("status") == "complete" or st.get("video_status") == "ready":
            break
    return {"id": vid, "url": f"https://www.facebook.com/reel/{vid}"}


def post(acc, vault, video: Path, meta: dict, settings: dict) -> dict:
    if acc.platform == "instagram":
        return post_instagram(acc, vault, video, meta, settings)
    return post_facebook(acc, vault, video, meta, settings)
