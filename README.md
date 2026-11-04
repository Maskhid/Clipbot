# ClipBot

**[Privacy Policy](docs/PRIVACY.md)** · **[Terms of Service](docs/TERMS.md)** · Uses the **YouTube API Services** ([YouTube Terms of Service](https://www.youtube.com/t/terms) · [Google Privacy Policy](https://policies.google.com/privacy))

A free agent that turns long gameplay videos and streams into vertical clips and posts them to **YouTube Shorts, TikTok, Instagram Reels and Facebook Reels** across many accounts. It runs on GitHub Actions, so it keeps working when your computer is off.

```
 you add a stream/video URL
          │
          ▼
 ┌─────────── Clip workflow (GitHub Actions) ───────────┐
 │ download → transcribe (Whisper) → find hype moments   │
 │ (loudness spikes + hype words + optional free AI)     │
 │ → 9:16 render, animated captions, hook banner         │
 │ → stored in your repo's Releases → posting schedule   │
 └───────────────────────────────────────────────────────┘
          │
          ▼
 ┌─────────── Post workflow (every hour) ───────────────┐
 │ posts whatever is due through each platform's        │
 │ OFFICIAL API, respecting caps, gaps and time windows │
 │ → Discord/Telegram summary                           │
 └──────────────────────────────────────────────────────┘
```

**First-time setup:** follow **[SETUP.md](SETUP.md)**. It takes about an hour, mostly creating the developer apps.

---

## Features

- **Finds the best moments by itself.** It looks for loudness spikes compared with the surrounding minute, hype phrases ("no way", "let's go", "bro"...), repeated words and bursts of fast talking. Clips are cut on sentence boundaries. If you add a free AI key (Gemini or Groq), the AI picks the most viral moments and writes the titles, hooks and captions.
- **Looks like a real Short.** Clips are 1080×1920 with big word-by-word captions (the spoken word is highlighted), a hook banner for the first 3 seconds, loudness normalised to -14 LUFS, and an optional watermark.
- **Three layouts:**
  - `blur`: blurred background with a zoomed gameplay strip.
  - `crop`: fills the screen.
  - `split`: facecam on top, gameplay below.
- **Brands, not just accounts.** A brand is one identity across YouTube, TikTok, IG and FB. Each source can be:
  - `split`: every clip goes to one brand, and the brands get different clips. This is the safest option.
  - `all`: every brand gets every clip, but each brand receives its own slightly altered copy (zoom, speed, colour, fresh encode, stripped metadata), its own caption style, and a posting time at least 2.5 h away from the other brands.
- **Human-like scheduling.** Each account has posts per day, time windows in the audience's time zone, random jitter, a minimum gap between posts, and a 24 h cap.
- **Performance tracking.** Every day it snapshots views, likes, comments, shares and followers for every account. Each week you get a ranking by platform, brand and account, a head-to-head of the same clip across platforms (the fairest comparison), top posts, and a suggestion of which platforms to cut. A platform is only suggested once it trails on both views and follower growth over 10+ posts.
- **Safe failure handling.** Failed posts retry with back-off, and errors that need you (such as an expired login) are reported instead of retried forever.
- **Official APIs only.** There's no browser automation and no password sharing. Tokens are kept in an encrypted vault in the repo, and only your `VAULT_KEY` secret can open it.

## Day-to-day use

| You want to | Do this |
|---|---|
| Clip a new stream/video | GitHub → **Actions → Clip → Run workflow** → paste URL (works from the GitHub mobile app). Or add it to `config/sources.yaml` and commit. |
| See what's scheduled | **Actions → Maintenance → Run workflow** (prints status), or `python -m clipbot status` in a Codespace. |
| Change posting times | Edit `config/accounts.yaml`, then **Maintenance → reschedule**. |
| Pause an account | `enabled: false` on that account. |
| Retry failed posts | **Maintenance → retry-failed**. |
| Test without posting | **Actions → Post → Run workflow** with *Dry run* ticked, or set `posting.dry_run: true`. |
| See what's working | **Actions → Stats** (also runs daily, with a summary every Monday) → open `reports/latest.md`. |
| Post an account yourself | Give it `mode: manual`. You'll get the clip's download link and caption in Discord/Telegram. |

## Platform limits you should know

| Platform | Before approval | After approval |
|---|---|---|
| **YouTube** | Uploads work, but Google **locks them private** until your API project passes YouTube's free compliance audit. Until then, use `mode: manual` for YouTube accounts or treat the uploads as previews. | Public uploads, around 100 a day per project. |
| **TikTok** | `tiktok_mode: inbox`: the clip lands in your TikTok inbox and you tap **Post**. The caption is sent to you to paste. An unaudited app allows up to 5 accounts a day. | Request the Content Posting audit, then use `tiktok_mode: direct` for fully automatic posting. |
| **Instagram** | Works fully while the Meta app is in *Development mode*, because you are the app's admin. No review is needed. Accounts must be Professional accounts linked to a Facebook Page. | Same. |
| **Facebook** | Works fully in Development mode, posting Reels to Pages (not personal profiles). Meta allows 30 API reels per Page per day. | Same. |

## GitHub Actions minutes

- **Public repo:** unlimited free minutes. The token vault is encrypted, but anyone can see your config, post history and rendered clips. Strangers can't run your workflows.
- **Private repo:** 2,000 free minutes a month. Rough costs:
  - Posting: about 1 to 3 minutes per hourly run that has posts due. Runs with nothing due exit in under a minute.
  - Clipping: about 20 to 30 minutes per hour of source video with the `base` speech model.

  To stretch the free minutes, change the Post schedule to every 2 hours (`17 */2 * * *` in `.github/workflows/post.yml`) and use `whisper_model: tiny`.

## Content and account safety

- **Footage:** clip your own gameplay, or streams you have permission to use. Add `credit:` to every source. Rockstar trailers and other copyrighted footage get claimed or struck, and strikes can take down a whole channel.
- **Duplicate content:** don't run several brands that post identical content. Platforms penalise duplicate and spammy networks. `split` mode exists for this, and when you use `all`, give the brands genuinely different angles (funny, news, hype, regional).
- **Posting volume:** start slow (2 to 3 posts a day per account) and increase once the accounts have a history.
- **Disclosure:** Meta and TikTok show a sharing disclosure for API posts, and their terms apply to what you post.

## Project layout

```
config/          accounts.yaml (brands + accounts), sources.yaml, settings.yaml
clipbot/         the agent (python -m clipbot --help)
  pipeline.py    clip job        poster.py   post job        scheduler.py  timing
  analytics.py   stats + weekly report
  highlights.py  moment finder   writer.py   titles/captions (AI optional)
  render.py      ffmpeg 9:16     captions.py word-by-word subtitles
  platforms/     youtube.py, tiktok.py, meta.py (Instagram + Facebook)
  auth.py        one-time account connection
state/           queue + history + metrics + encrypted vault (committed by the workflows)
reports/         latest.md performance report + videos.csv
.github/workflows/  clip.yml, post.yml, stats.yml, maintenance.yml
tests/           python -m pytest
```

## Troubleshooting

| Problem | Fix |
|---|---|
| YouTube: *"login expired or revoked"* every week | Your OAuth consent screen is still in *Testing*. Publish it to *In production* (SETUP.md, YouTube step 3), then reconnect. |
| YouTube: videos stuck private | Expected until the API audit passes. See the table above. |
| TikTok: `unaudited_client_can_only_post_to_private_accounts` | Use `tiktok_mode: inbox` until your app passes the audit. |
| TikTok: `spam_risk_too_many_posts` | Lower `posts_per_day` for that account. |
| Instagram/Facebook: code 190 | Re-run `python -m clipbot auth meta` with a fresh token (you changed your password or removed the app). |
| Clip job: *"Sign in to confirm you're not a bot"* | YouTube is blocking GitHub's servers. Add a `YTDLP_COOKIES` secret (SETUP.md, optional step), or use a Twitch/Kick/direct link. |
| Clip job times out | Long VOD: use `start`/`end`, or `whisper_model: tiny`. |
| Report says "no view counts" or "no follower count" | Reconnect that platform after adding the stats permissions (SETUP.md Part 3c / Part 4 scopes). |
| Push conflicts in Actions | Rare. The next run fixes itself. Avoid connecting accounts at the exact minute a Post run is going. |
