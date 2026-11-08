"""ClipBot command line.  Run `python -m clipbot --help`."""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from .config import CONFIG_DIR, load_config
from .state import State, parse_iso, utcnow
from .util import setup_logging


def cmd_vault(args):
    from .vault import VAULT_PATH, Vault, generate_key

    if args.action == "init":
        if VAULT_PATH.exists() and not args.force:
            sys.exit("state/vault.enc already exists. Use --force to replace it (all connections are lost).")
        key = generate_key()
        v = Vault(key=key)
        v.save(force=True)
        print("Vault created at state/vault.enc\n")
        print("Your VAULT_KEY (save it as a GitHub secret named VAULT_KEY, and keep a copy somewhere safe):\n")
        print(key)
        print("\nFor the auth commands in this terminal, also run:\n  export VAULT_KEY=" + key)


def cmd_auth(args):
    from . import auth
    from .vault import Vault

    cfg = load_config()
    v = Vault()
    t = args.target
    if t == "youtube-app":
        auth.youtube_app(v, args.client_id, args.client_secret)
    elif t == "youtube":
        ids = [args.account] if args.account else [a.id for a in cfg.accounts.values() if a.platform == "youtube"]
        for aid in ids:
            auth.youtube_account(cfg, v, aid)
    elif t == "tiktok-app":
        auth.tiktok_app(v, args.client_key, args.client_secret, args.redirect_uri)
    elif t == "tiktok":
        ids = [args.account] if args.account else [a.id for a in cfg.accounts.values() if a.platform == "tiktok"]
        for aid in ids:
            auth.tiktok_account(cfg, v, aid)
    elif t == "meta-app":
        auth.meta_app(v, args.app_id, args.app_secret)
    elif t == "meta":
        auth.meta_accounts(cfg, v, args.token)
    elif t == "status":
        for a in cfg.accounts.values():
            ok = v.has_account(a.id)
            extra = ""
            if ok:
                c = v.account(a.id)
                extra = c.get("channel_title") or c.get("display_name") or c.get("ig_username") or c.get("page_name") or ""
            print(f"{'✓' if ok else '✗'} {a.id:28} {a.platform:10} {extra}")


def cmd_clip(args):
    cfg = load_config()
    if args.check:
        st = State()
        n = 0
        for src in cfg.sources:
            d = st.source_done(src) or {}
            if src.get("enabled") is not False and d.get("status") != "done" and d.get("attempts", 0) < 3:
                n += 1
        print(f"pending_sources={n}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"has_work={'true' if n else 'false'}\n")
        return
    from .pipeline import run_clip

    errors: list = []
    made = run_clip(cfg, only_url=args.url, keep_work=args.keep_work, errors=errors)
    print(f"Made {len(made)} clip(s).")
    if errors:
        print("\nFAILED SOURCES:")
        for e in errors:
            print("  ✗", e)
        sys.exit(1)  # makes the GitHub run show a red X instead of a misleading green tick


def cmd_post(args):
    from .poster import due_jobs, run_post

    cfg = load_config()
    if args.check:
        n = len(due_jobs(cfg, State()))
        print(f"due={n}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"has_due={'true' if n else 'false'}\n")
        return
    s = run_post(cfg, dry_run=True if args.dry_run else None, only_account=args.account)
    print(f"posted={len(s['posted'])} retry={len(s['retry'])} failed={len(s['failed'])} blocked={len(s['blocked'])}")


def cmd_add_source(args):
    brands = [b.strip() for b in args.brands.split(",") if b.strip()] if args.brands else []
    cfg = load_config()
    for b in brands:
        if b not in cfg.brands:
            sys.exit(f"Unknown brand '{b}'. Brands: {', '.join(cfg.brands)}")
    lines = [f"  - url: \"{args.url}\""]
    if brands:
        lines.append(f"    brands: [{', '.join(brands)}]")
    lines.append(f"    distribute: {args.distribute}")
    for k in ("start", "end", "credit", "game", "title_hint"):
        v = getattr(args, k)
        if v:
            lines.append(f"    {k}: \"{v}\"")
    if args.max_clips:
        lines.append(f"    max_clips: {args.max_clips}")
    path = CONFIG_DIR / "sources.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else "sources:\n"
    if "sources:" not in text:
        text += "\nsources:\n"
    text = text.replace("sources: []", "sources:")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")
    load_config()  # validate
    print(f"Added {args.url}")


def cmd_status(args):
    cfg = load_config()
    st = State()
    res = st.results()
    now = utcnow()
    counts = Counter()
    upcoming = []
    for j in st.jobs():
        s = (res.get(j["id"]) or {}).get("status", "scheduled")
        counts[s] += 1
        if s in ("scheduled", "retry", "blocked"):
            upcoming.append(j)
    print("Jobs:", dict(counts) or "none")
    print(f"Clips: {len(st.clips())}   Sources processed: {len(list((st.root / 'sources_done').glob('*.json')))}")
    tz = ZoneInfo(cfg.tz)
    print(f"\nNext posts (times in {cfg.tz}):")
    for j in sorted(upcoming, key=lambda j: j["due_at"])[: args.n]:
        t = parse_iso(j["due_at"]).astimezone(tz)
        late = " (due now)" if parse_iso(j["due_at"]) <= now else ""
        print(f"  {t:%a %d %b %H:%M}  {j['account']:28} {j['clip_id']}{late}")
    fails = [r for r in res.values() if r.get("status") == "failed" and parse_iso(r.get("at", "2000-01-01T00:00:00Z")) > now - timedelta(days=3)]
    if fails:
        print("\nRecent failures:")
        for r in fails[-10:]:
            print(f"  {r['account']}: {r.get('error', '')[:160]}")


def cmd_stats(args):
    from . import analytics
    from .notify import notify
    from .vault import Vault

    cfg = load_config()
    if not args.report_only:
        res = analytics.collect(cfg, Vault())
        print(f"Collected stats for {len(res['ok'])} account(s); {len(res['errors'])} error(s).")
        for e in res["errors"]:
            print("  !", e)
    path, summary = analytics.write_report(cfg, days=args.days)
    print(f"Report written to {path.relative_to(path.parent.parent)}\n")
    print(summary)
    if args.notify:
        repo = os.environ.get("GITHUB_REPOSITORY")
        link = f"\nFull report: https://github.com/{repo}/blob/HEAD/reports/latest.md" if repo else ""
        notify(cfg.settings, summary + link)


def cmd_upload_test(args):
    """Upload one video file straight to one account (for testing / the YouTube audit demo)."""
    from pathlib import Path

    from .platforms import get_uploader
    from .vault import Vault

    cfg = load_config()
    acc = cfg.accounts.get(args.account)
    if not acc:
        sys.exit(f"No account '{args.account}' in config/accounts.yaml")
    video = Path(args.file)
    if not video.exists():
        sys.exit(f"File not found: {video}")
    title = args.title or "ClipBot test upload"
    meta = {"title": title, "description": args.text or "Test upload from ClipBot.", "tags": ["test"], "text": args.text or title}
    v = Vault()
    print(f"Uploading {video.name} to {acc.id} ({acc.platform})...")
    res = get_uploader(acc.platform).post(acc, v, video, meta, cfg.settings)
    v.save()
    print("Done:", res.get("url") or res.get("id"))
    if res.get("note"):
        print(res["note"])


def cmd_watch(args):
    from .watch import run_watch

    cfg = load_config()
    if not cfg.channels:
        print("No channels in config/sources.yaml (channels: section).")
        return
    added = run_watch(cfg)
    print(f"New broadcasts queued: {len(added)}")
    for u in added:
        print("  +", u)


def cmd_reschedule(args):
    from .pipeline import reschedule_all

    print(f"Rescheduled {reschedule_all(load_config(), State())} job(s).")


def cmd_retry(args):
    st = State()
    n = 0
    for jid, r in st.results().items():
        if r.get("status") in ("failed", "blocked") and (not args.account or r["account"] == args.account):
            st.delete_result(jid)
            n += 1
    print(f"Reset {n} job(s); they'll be tried on the next post run.")


def cmd_cleanup(args):
    from .storage import get_storage

    cfg = load_config()
    st = State()
    storage = get_storage(cfg.settings)
    keep = timedelta(days=int(cfg.settings["storage"]["keep_days"]))
    now = utcnow()
    res = st.results()
    open_tags = set()
    for j in st.jobs():
        if (res.get(j["id"]) or {}).get("status") not in ("posted", "failed"):
            c = st.clip(j["clip_id"])
            if c:
                open_tags.add(c["tag"])
    prefix = cfg.settings["storage"]["release_prefix"]
    removed = 0
    for t in storage.list_tags():
        if t["tag"].startswith(prefix) and t["tag"] not in open_tags and now - t["created"] > keep:
            storage.delete_tag(t["tag"])
            removed += 1
    # prune old state records
    pruned = 0
    for j in st.jobs():
        r = res.get(j["id"]) or {}
        if r.get("status") in ("posted", "failed") and now - parse_iso(r.get("posted_at") or r.get("at")) > timedelta(days=45):
            st.delete_job(j["id"])
            st.delete_result(j["id"])
            pruned += 1
    print(f"Deleted {removed} old clip release(s); pruned {pruned} old record(s).")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clipbot", description="Clip long videos and post them everywhere.")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("vault", help="create the encrypted token vault")
    p.add_argument("action", choices=["init"])
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_vault)

    p = sub.add_parser("auth", help="connect apps and accounts")
    p.add_argument("target", choices=["youtube-app", "youtube", "tiktok-app", "tiktok", "meta-app", "meta", "status"])
    p.add_argument("account", nargs="?", help="account id from accounts.yaml (omit = all of that platform)")
    p.add_argument("--client-id")
    p.add_argument("--client-secret")
    p.add_argument("--client-key")
    p.add_argument("--redirect-uri")
    p.add_argument("--app-id")
    p.add_argument("--app-secret")
    p.add_argument("--token", help="Meta user access token (otherwise you're asked)")
    p.set_defaults(fn=cmd_auth)

    p = sub.add_parser("clip", help="process new sources into clips and schedule them")
    p.add_argument("--url", help="only this source url")
    p.add_argument("--keep-work", action="store_true", help="keep downloaded/rendered files in .work/")
    p.add_argument("--check", action="store_true", help="only report whether any source is waiting")
    p.set_defaults(fn=cmd_clip)

    p = sub.add_parser("post", help="post everything that is due")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true", help="only report whether anything is due")
    p.add_argument("--account")
    p.set_defaults(fn=cmd_post)

    p = sub.add_parser("add-source", help="add a video/stream URL to config/sources.yaml")
    p.add_argument("url")
    p.add_argument("--brands", help="comma separated brand names (default: all)")
    p.add_argument("--distribute", choices=["split", "all"], default="split")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--credit")
    p.add_argument("--game")
    p.add_argument("--title-hint", dest="title_hint")
    p.add_argument("--max-clips", type=int)
    p.set_defaults(fn=cmd_add_source)

    p = sub.add_parser("status", help="show the queue")
    p.add_argument("-n", type=int, default=25)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("stats", help="collect performance stats and write reports/latest.md")
    p.add_argument("--days", type=int, default=7, help="report window (default 7)")
    p.add_argument("--notify", action="store_true", help="send the summary to Discord/Telegram")
    p.add_argument("--report-only", action="store_true", help="don't fetch, just rebuild the report")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("upload-test", help="upload one video file to one account right now")
    p.add_argument("file")
    p.add_argument("--account", required=True)
    p.add_argument("--title")
    p.add_argument("--text")
    p.set_defaults(fn=cmd_upload_test)

    p = sub.add_parser("watch", help="check watched channels for new streams")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("reschedule", help="re-plan unposted jobs after changing posting times")
    p.set_defaults(fn=cmd_reschedule)

    p = sub.add_parser("retry", help="retry failed / blocked posts")
    p.add_argument("--account")
    p.set_defaults(fn=cmd_retry)

    p = sub.add_parser("cleanup", help="delete old posted clips from storage")
    p.set_defaults(fn=cmd_cleanup)

    args = ap.parse_args(argv)
    setup_logging(args.verbose)
    args.fn(args)


if __name__ == "__main__":
    main()
