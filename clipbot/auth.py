"""One-time account connection. Run these on your computer or in a GitHub Codespace.
Each flow prints a link, you approve in your browser, and paste back what it asks for."""
from __future__ import annotations

import secrets
import time
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import requests

from .config import Config
from .platforms import youtube as yt
from .vault import Vault

YT_REDIRECT = "http://localhost:8765/"


def _code_from(pasted: str) -> str:
    pasted = pasted.strip()
    if "code=" in pasted:
        q = parse_qs(urlparse(pasted).query)
        if "code" in q:
            return q["code"][0]
        return unquote(pasted.split("code=")[1].split("&")[0])
    return unquote(pasted)


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _accounts_for(cfg: Config, platform: str, account_id: str | None):
    accs = [a for a in cfg.accounts.values() if a.platform == platform]
    if account_id:
        accs = [a for a in accs if a.id == account_id]
        if not accs:
            raise SystemExit(f"No {platform} account with id '{account_id}' in config/accounts.yaml")
    return accs


# ------------------------------------------------------------- YouTube ----

def youtube_app(vault: Vault, client_id: str, client_secret: str):
    vault.set_app("youtube", {"client_id": client_id.strip(), "client_secret": client_secret.strip()})
    vault.save()
    print("Saved YouTube app credentials.")


def youtube_account(cfg: Config, vault: Vault, account_id: str):
    _accounts_for(cfg, "youtube", account_id)
    app = vault.app("youtube")
    state = secrets.token_urlsafe(8)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": app["client_id"], "redirect_uri": YT_REDIRECT, "response_type": "code",
        "scope": " ".join(yt.SCOPES), "access_type": "offline", "prompt": "consent select_account",
        "include_granted_scopes": "true", "state": state})
    print(f"\n[{account_id}] 1) Open this link and sign in. If the Google account has several channels,"
          " pick the channel for THIS account.\n")
    print(url)
    print("\n2) After you click Allow, the browser goes to a page that won't load (localhost). That's expected."
          "\n   Copy the FULL address from the address bar and paste it here.\n")
    code = _code_from(_ask("Paste address: "))
    r = requests.post(yt.TOKEN_URL, timeout=30, data={
        "code": code, "client_id": app["client_id"], "client_secret": app["client_secret"],
        "redirect_uri": YT_REDIRECT, "grant_type": "authorization_code"})
    j = r.json()
    if "refresh_token" not in j:
        raise SystemExit(f"Google did not return a refresh token: {j}")
    creds = {"refresh_token": j["refresh_token"], "access_token": j["access_token"],
             "expires_at": time.time() + int(j.get("expires_in", 3600))}
    ch = requests.get("https://www.googleapis.com/youtube/v3/channels", timeout=30,
                      params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {j['access_token']}"}).json()
    name = (ch.get("items") or [{}])[0].get("snippet", {}).get("title", "?")
    creds["channel_title"] = name
    vault.set_account(account_id, creds)
    vault.save()
    print(f"Connected {account_id} -> YouTube channel '{name}'")


# -------------------------------------------------------------- TikTok ----

def tiktok_app(vault: Vault, client_key: str, client_secret: str, redirect_uri: str):
    vault.set_app("tiktok", {"client_key": client_key.strip(), "client_secret": client_secret.strip(),
                             "redirect_uri": redirect_uri.strip()})
    vault.save()
    print("Saved TikTok app credentials.")


def tiktok_account(cfg: Config, vault: Vault, account_id: str):
    acc = _accounts_for(cfg, "tiktok", account_id)[0]
    app = vault.app("tiktok")
    scopes = "user.info.basic,user.info.stats,video.list,video.upload" + (",video.publish" if acc.get("tiktok_mode") == "direct" else "")
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": app["client_key"], "scope": scopes, "response_type": "code",
        "redirect_uri": app["redirect_uri"], "state": secrets.token_urlsafe(8)})
    print(f"\n[{account_id}] 1) Log into the RIGHT TikTok account in your browser first (or use a private window),"
          " then open:\n")
    print(url)
    print("\n2) After you approve, copy the FULL address of the page you land on and paste it here.\n")
    code = _code_from(_ask("Paste address: "))
    r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", timeout=30,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
                          "client_key": app["client_key"], "client_secret": app["client_secret"], "code": code,
                          "grant_type": "authorization_code", "redirect_uri": app["redirect_uri"]})
    j = r.json()
    if "access_token" not in j:
        raise SystemExit(f"TikTok login failed: {j}")
    creds = {"access_token": j["access_token"], "refresh_token": j["refresh_token"], "open_id": j.get("open_id"),
             "expires_at": time.time() + int(j.get("expires_in", 86400)),
             "refresh_expires_at": time.time() + int(j.get("refresh_expires_in", 31536000))}
    try:
        u = requests.get("https://open.tiktokapis.com/v2/user/info/", params={"fields": "display_name"}, timeout=30,
                         headers={"Authorization": f"Bearer {j['access_token']}"}).json()
        creds["display_name"] = u.get("data", {}).get("user", {}).get("display_name")
    except (requests.RequestException, ValueError):
        pass
    vault.set_account(account_id, creds)
    vault.save()
    print(f"Connected {account_id} -> TikTok '{creds.get('display_name') or creds.get('open_id')}'")


# ---------------------------------------------------------------- Meta ----

def meta_app(vault: Vault, app_id: str, app_secret: str):
    vault.set_app("meta", {"app_id": app_id.strip(), "app_secret": app_secret.strip()})
    vault.save()
    print("Saved Meta app credentials.")


def meta_accounts(cfg: Config, vault: Vault, user_token: str | None = None):
    """Exchange a Graph API Explorer user token for permanent Page tokens and match every
    instagram/facebook account in accounts.yaml by its `page` name or `handle`."""
    app = vault.app("meta")
    G = f"https://graph.facebook.com/{cfg.settings['meta']['graph_version']}"
    if not user_token:
        print("\nPaste the User Access Token from Graph API Explorer (see SETUP.md, Meta step 4):")
        user_token = _ask("Token: ")
    r = requests.get(f"{G}/oauth/access_token", timeout=30, params={
        "grant_type": "fb_exchange_token", "client_id": app["app_id"], "client_secret": app["app_secret"],
        "fb_exchange_token": user_token}).json()
    if "access_token" not in r:
        raise SystemExit(f"Token exchange failed: {r}")
    long_user = r["access_token"]
    pages, url = [], f"{G}/me/accounts"
    params = {"fields": "id,name,access_token,instagram_business_account{id,username}", "limit": 100,
              "access_token": long_user}
    while url:
        j = requests.get(url, params=params, timeout=30).json()
        if "error" in j:
            raise SystemExit(f"Could not list Pages: {j['error'].get('message')}")
        pages += j.get("data", [])
        url, params = j.get("paging", {}).get("next"), None

    print("\nPages this login can manage:")
    for p in pages:
        ig = p.get("instagram_business_account") or {}
        print(f"  - {p['name']} (page {p['id']})" + (f"  ↔ Instagram @{ig.get('username')}" if ig else "  (no Instagram linked)"))

    def norm(s):
        return (s or "").lower().lstrip("@").strip()

    connected = 0
    for acc in cfg.accounts.values():
        if acc.platform not in ("facebook", "instagram"):
            continue
        want_page, want_handle = norm(acc.get("page")), norm(acc.get("handle"))
        match = None
        for p in pages:
            ig = p.get("instagram_business_account") or {}
            if acc.platform == "instagram" and ig and want_handle and norm(ig.get("username")) == want_handle:
                match = p
            elif want_page and norm(p["name"]) == want_page:
                match = p
            if match:
                break
        if not match:
            print(f"  ✗ {acc.id}: no match (set `page:` to the exact Page name"
                  f"{' or `handle:` to the Instagram username' if acc.platform == 'instagram' else ''})")
            continue
        creds = {"access_token": match["access_token"], "page_id": match["id"], "page_name": match["name"]}
        if acc.platform == "instagram":
            ig = match.get("instagram_business_account")
            if not ig:
                print(f"  ✗ {acc.id}: Page '{match['name']}' has no linked Instagram professional account")
                continue
            creds.update({"ig_user_id": ig["id"], "ig_username": ig.get("username")})
        vault.set_account(acc.id, creds)
        connected += 1
        print(f"  ✓ {acc.id} -> {match['name']}" + (f" / @{creds.get('ig_username')}" if acc.platform == "instagram" else ""))
    vault.save()
    print(f"\nConnected {connected} Meta account(s).")
