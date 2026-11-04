"""TikTok via the official Content Posting API.

tiktok_mode: inbox  -> video lands in the account's TikTok inbox/drafts; you tap Post in the
                       app (works before TikTok audits your app; caption is sent to you).
tiktok_mode: direct -> posts straight to the profile. Until your app passes TikTok's audit,
                       TikTok forces these posts to private (SELF_ONLY).
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import requests

from . import PostError

API = "https://open.tiktokapis.com"
MB = 1024 * 1024


def access_token(app: dict, creds: dict) -> str:
    if creds.get("access_token") and creds.get("expires_at", 0) > time.time() + 300:
        return creds["access_token"]
    try:
        r = requests.post(f"{API}/v2/oauth/token/", timeout=30, data={
            "client_key": app["client_key"], "client_secret": app["client_secret"],
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        j = r.json()
    except (requests.RequestException, ValueError) as e:
        raise PostError(f"TikTok token refresh error: {e}") from e
    if "access_token" not in j:
        retry = j.get("error") not in ("invalid_grant", "invalid_client", "access_token_invalid")
        raise PostError(f"TikTok token refresh failed: {j.get('error')} {j.get('error_description', '')}. "
                        "If it persists, re-run: python -m clipbot auth tiktok <account>", retryable=retry)
    creds["access_token"] = j["access_token"]
    creds["expires_at"] = time.time() + int(j.get("expires_in", 86400))
    if j.get("refresh_token"):
        creds["refresh_token"] = j["refresh_token"]
        creds["refresh_expires_at"] = time.time() + int(j.get("refresh_expires_in", 31536000))
    return creds["access_token"]


def _check(r: requests.Response, what: str) -> dict:
    try:
        j = r.json()
    except ValueError as e:
        raise PostError(f"TikTok {what}: bad response {r.status_code} {r.text[:200]}") from e
    err = j.get("error") or {}
    code = err.get("code", "ok")
    if code != "ok":
        permanent = code in ("spam_risk_user_banned_from_posting", "unaudited_client_can_only_post_to_private_accounts",
                             "scope_not_authorized", "access_token_invalid", "url_ownership_unverified",
                             "privacy_level_option_mismatch", "invalid_param")
        hint = ""
        if code == "unaudited_client_can_only_post_to_private_accounts":
            hint = " (Your TikTok app isn't audited yet: use tiktok_mode: inbox for this account.)"
        raise PostError(f"TikTok {what} failed: {code} {err.get('message', '')}{hint}", retryable=not permanent)
    return j.get("data") or {}


def _chunks(size: int) -> tuple[int, int]:
    if size <= 64 * MB:
        return size, 1
    chunk = 10 * MB
    return chunk, math.floor(size / chunk)


def post(acc, vault, video: Path, meta: dict, settings: dict) -> dict:
    app = vault.app("tiktok")
    creds = vault.account(acc.id)
    token = access_token(app, creds)
    vault.update_account(acc.id, creds)
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    size = Path(video).stat().st_size
    chunk, count = _chunks(size)
    source_info = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    mode = acc.get("tiktok_mode", "inbox")

    try:
        if mode == "direct":
            info = _check(requests.post(f"{API}/v2/post/publish/creator_info/query/", headers=h, timeout=30), "creator info")
            opts = info.get("privacy_level_options") or ["SELF_ONLY"]
            want = {"public": "PUBLIC_TO_EVERYONE", "friends": "MUTUAL_FOLLOW_FRIENDS",
                    "followers": "FOLLOWER_OF_CREATOR", "private": "SELF_ONLY"}.get(acc.get("privacy", "public"), "PUBLIC_TO_EVERYONE")
            level = want if want in opts else ("SELF_ONLY" if "SELF_ONLY" in opts else opts[0])
            body = {"post_info": {"title": meta["text"], "privacy_level": level, "disable_duet": False,
                                  "disable_comment": False, "disable_stitch": False, "video_cover_timestamp_ms": 1000},
                    "source_info": source_info}
            data = _check(requests.post(f"{API}/v2/post/publish/video/init/", headers=h, json=body, timeout=60), "init")
        else:
            data = _check(requests.post(f"{API}/v2/post/publish/inbox/video/init/", headers=h,
                                        json={"source_info": source_info}, timeout=60), "inbox init")
    except requests.RequestException as e:
        raise PostError(f"TikTok init network error: {e}") from e

    publish_id, upload_url = data["publish_id"], data["upload_url"]
    with open(video, "rb") as f:
        for i in range(count):
            start = i * chunk
            end = size - 1 if i == count - 1 else start + chunk - 1
            f.seek(start)
            part = f.read(end - start + 1)
            for attempt in range(4):
                try:
                    r = requests.put(upload_url, data=part, timeout=300, headers={
                        "Content-Type": "video/mp4", "Content-Length": str(len(part)),
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                    if r.status_code in (200, 201, 206):
                        break
                    if r.status_code < 500:
                        raise PostError(f"TikTok chunk upload rejected: {r.status_code} {r.text[:200]}")
                except requests.RequestException:
                    pass
                time.sleep(4 * (attempt + 1))
            else:
                raise PostError("TikTok chunk upload kept failing")

    status, post_ids = "PROCESSING_UPLOAD", []
    for _ in range(36):
        time.sleep(5)
        try:
            d = _check(requests.post(f"{API}/v2/post/publish/status/fetch/", headers=h,
                                     json={"publish_id": publish_id}, timeout=30), "status")
        except PostError:
            continue
        status = d.get("status", status)
        post_ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
        if status == "FAILED":
            raise PostError(f"TikTok processing failed: {d.get('fail_reason')}", retryable=False)
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            break
    handle = (acc.get("handle") or "").lstrip("@")
    url = f"https://www.tiktok.com/@{handle}/video/{post_ids[0]}" if (post_ids and handle) else (
        f"https://www.tiktok.com/@{handle}" if handle else "")
    note = None
    if mode != "direct":
        note = "In your TikTok inbox: open TikTok → notifications → tap to finish posting. Caption to paste:\n" + meta["text"]
    return {"id": publish_id, "url": url, "status": status, "note": note}
