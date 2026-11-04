# Setup guide

Do these parts in order. Everything is free. Anything you type in a terminal happens in a **GitHub Codespace**, a free browser-based computer, so you don't need to install anything.

- **Part 1:** GitHub repo and vault (10 min)
- **Part 2:** describe your brands and accounts (10 min)
- **Part 3:** connect Instagram and Facebook (20 min). Do this first, because it works without any approval.
- **Part 4:** connect TikTok (20 min, plus a few days for TikTok's review)
- **Part 5:** connect YouTube (15 min, plus a few weeks for the YouTube audit)
- **Part 6:** optional extras: AI captions, notifications, YouTube cookies
- **Part 7:** first run

---

## Part 1: GitHub repo and vault

1. Create a free account at https://github.com if you don't have one.
2. Create a new repository: **+ → New repository**.
   - Name: `clipbot`.
   - Visibility: **Public** gives unlimited free Actions minutes. Choose **Private** if you don't want your config and clips visible (see the README for the minutes budget).
   - Tick **Add a README** so the repo isn't empty.
3. On the repo page, click **Code → Codespaces → Create codespace on main**. A VS Code window opens in your browser.
4. Drag `clipbot.zip` into the file list on the left to upload it. Then, in the **Terminal** at the bottom, run:
   ```bash
   unzip -o clipbot.zip -d /tmp/cb && cp -r /tmp/cb/clipbot/. . && rm -rf clipbot.zip /tmp/cb
   pip install -q requests PyYAML cryptography
   python -m clipbot vault init
   ```
5. The last command prints a **VAULT_KEY**. Keep a copy somewhere safe, then:
   - In the repo on GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**, set the name to `VAULT_KEY`, and paste the key as the value.
   - In the Codespace terminal, run `export VAULT_KEY=...` with your key. Repeat this whenever you open a new terminal.
6. Go to **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and click **Save**.

## Part 2: describe your accounts

Open `config/accounts.yaml` in the Codespace. It has 5 example brands × 4 platforms = 20 accounts. Make it match your real accounts:

- Rename brands and account ids to anything you like. Ids are just labels you'll use in commands.
- `handle`: your @username on TikTok and Instagram.
- `page`: the **exact** Facebook Page name. Instagram accounts use the Page they're linked to.
- `timezone`: where the brand's **audience** is, not where you are. For example, US audiences post best in `America/New_York` evenings.
- Delete any account that doesn't exist yet. You can add it later.

Check that it loads:
```bash
python -m clipbot auth status
```
At this stage every account shows ✗, because nothing is connected yet.

## Part 3: Instagram and Facebook (Meta)

### 3a. Get the accounts ready (in the apps)

1. **Each Instagram account must be Professional.** In Instagram, go to **Settings → Account type and tools → Switch to professional account**. Creator or Business both work.
2. **Each brand needs a Facebook Page**, created from your Facebook profile (**Pages → Create**).
3. **Link each Instagram account to its Page:**
   - Open **Meta Business Suite** (business.facebook.com).
   - Pick the Page, go to **Settings → Linked accounts / Instagram**, and connect it.
   - Or, in Instagram, go to **Settings → Accounts Center** and add the Facebook account. Then, in the Page's settings, link the Instagram account.

### 3b. Create the Meta developer app

1. Go to https://developers.facebook.com, log in with the Facebook account that manages the Pages, and register as a developer.
2. Click **My Apps → Create App**. Choose the options for **managing Pages / Business** and **Instagram content publishing**. The wording changes over time. If you're asked for an app type, pick **Business**. Give the app any name.
3. **Leave the app in Development mode.** In that mode it works for anyone with a role on the app, which includes you as the admin, so no App Review is needed.
4. Go to **App settings → Basic** and copy the **App ID** and **App Secret**. Then, in the Codespace:
   ```bash
   python -m clipbot auth meta-app --app-id YOUR_APP_ID --app-secret YOUR_APP_SECRET
   ```

### 3c. Connect all Pages and Instagram accounts at once

1. Open https://developers.facebook.com/tools/explorer.
2. On the right, set **Meta App** to your app and **User or Page** to **User Token**.
3. Under **Permissions**, add:
   `pages_show_list, pages_read_engagement, pages_manage_posts, publish_video, instagram_basic, instagram_content_publish, business_management`
4. Click **Generate Access Token**. In the popup, **select every Page and every Instagram account** you want ClipBot to manage, and approve.
5. Copy the token. Then, in the Codespace:
   ```bash
   python -m clipbot auth meta
   ```
   Paste the token. ClipBot swaps it for **permanent** Page tokens and matches each account using the `page` and `handle` values in accounts.yaml. Any account marked ✗ has a mismatched name: fix the name and run the command again.

> If your Pages are spread across several Facebook profiles, give each profile a role on the app (**App roles → Roles → Add people**), then repeat 3c while logged into that profile.

## Part 4: TikTok

1. Go to https://developers.tiktok.com, log in, and click **Manage apps → Connect an app**.
2. Fill in the app details:
   - Icon and name.
   - Category: Entertainment.
   - Description, for example: *"Posts my own gaming clips to my own TikTok accounts."*
   - **Terms of Service URL** and **Privacy Policy URL:** use the two files in this repo's `docs/` folder. On GitHub, open each file and copy the link from the address bar, for example `https://github.com/YOU/clipbot/blob/main/docs/PRIVACY.md`. This works if the repo is public. For a private repo, paste those files into a free Google Doc or Notion page and use that link.
   - **Platform:** Web. Website URL: your repo URL.
3. **Add products:**
   - **Login Kit:** set the **Redirect URI** to your repo URL, for example `https://github.com/YOU/clipbot`.
   - **Content Posting API:** enable it. **Direct Post** is optional, for later.
4. **Scopes:** `user.info.basic`, `video.upload` (and `video.publish` if you'll use direct posting later).
5. **Submit the app for review.** While you wait (usually a few days), use **Sandbox** mode:
   - Create a sandbox.
   - Under **Target users**, add your TikTok accounts.
   - Use the sandbox's client key and secret in the next step.
6. Copy the **Client key** and **Client secret**. Then, in the Codespace:
   ```bash
   python -m clipbot auth tiktok-app --client-key KEY --client-secret SECRET --redirect-uri https://github.com/YOU/clipbot
   ```
7. Connect each TikTok account. In your browser, **log into that TikTok account first**, or use a private window. Then:
   ```bash
   python -m clipbot auth tiktok gta_chaos_tt
   ```
   Open the link and approve. You land on your GitHub repo page with `?code=...` in the address bar. Copy the whole address and paste it into the terminal. Repeat for each TikTok account.

**How TikTok posting works:** the accounts start with `tiktok_mode: inbox`. Each clip arrives in that account's TikTok inbox, and you get a Discord/Telegram message with the caption. Open TikTok, tap the notification, paste the caption, and post. It takes about 10 seconds per clip.

For fully automatic posting, apply for the **Content Posting API audit** in the developer portal (Content Posting API → Direct Post). Once it's approved, change those accounts to `tiktok_mode: direct` and re-run `auth tiktok` for each one, so the `video.publish` permission is added.

## Part 5: YouTube

1. Go to https://console.cloud.google.com and click **Select a project → New project** (call it `clipbot`).
2. Go to **APIs & Services → Library**, search for **YouTube Data API v3**, and click **Enable**.
3. Set up the OAuth consent screen (also called **Google Auth Platform**):
   - User type / Audience: **External**.
   - Fill in the app name, your email and the developer email.
   - Under **Audience**, click **Publish app** so it's **In production**. Otherwise Google expires your logins every 7 days.
   - You don't need verification for personal use. You'll see an "unverified app" warning when you log in. Click **Advanced → Go to clipbot**.
4. Go to **Credentials → Create credentials → OAuth client ID**, set **Application type** to **Desktop app**, and click **Create**. Copy the Client ID and Client secret. Then, in the Codespace:
   ```bash
   python -m clipbot auth youtube-app --client-id ID --client-secret SECRET
   ```
5. Connect each channel:
   ```bash
   python -m clipbot auth youtube gta_chaos_yt
   ```
   Open the link and choose the Google account. **If you have several channels on one Google account, pick the right channel** on the "Choose an account or brand account" screen. After you approve, the browser shows a "can't connect to localhost" page. That's expected: copy the whole address and paste it into the terminal. Repeat for each channel.
6. **Apply for the audit.** Google keeps API uploads **private** until your project passes the free **YouTube API Services audit**:
   - Search for *"YouTube API Services – Audit and Quota Extension Form"*.
   - Explain that it's an internal tool that uploads your own clips to your own channels.
   - Link the `docs/` policy pages.

   It can take a few weeks. Until it's approved, set YouTube accounts to `mode: manual` so ClipBot sends you each Short to upload yourself. Then remove that line once the audit passes.

## Part 6: optional extras

Add these under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | What it does | Where to get it |
|---|---|---|
| `LLM_API_KEY` | An AI picks the best moments and writes titles, hooks and captions. Also set `llm.provider` in `config/settings.yaml`. | Gemini: https://aistudio.google.com/apikey (`provider: gemini`). Groq: https://console.groq.com/keys (`provider: openai_compatible`, `model: llama-3.3-70b-versatile`). Both have free tiers. |
| `DISCORD_WEBHOOK` | Post summaries, TikTok captions to paste, and errors. | Discord channel → Edit → Integrations → Webhooks → New → Copy URL. |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Same, on Telegram. | Message @BotFather → /newbot gives the token. Send your bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` to find `chat.id`. |
| `YTDLP_COOKIES` | Fixes "confirm you're not a bot" when downloading from YouTube. | Use a *"Get cookies.txt"* browser extension on youtube.com (preferably a spare account) and paste the file's contents. |

## Part 7: first run

1. Save everything to GitHub. In the Codespace terminal:
   ```bash
   git add -A && git commit -m "ClipBot setup" && git push
   ```
2. Test the pipeline:
   - In the GitHub repo, go to **Actions**. If asked, click *"I understand my workflows, enable them"*.
   - Open **Clip → Run workflow**, paste a URL of your own gameplay, pick brands, and click **Run**.
   - When it finishes, the clips are under **Releases** and the schedule is in `state/queue/`.
3. Dry-run posting: run **Post → Run workflow** with **Dry run** ticked. The log shows what would be posted where.
4. Go live. The Post workflow now runs every hour by itself. Open **Maintenance → Run workflow** at any time to see the schedule.
5. Check your phone (Discord or Telegram) for the TikTok inbox reminders.

Whenever you change posting times in `accounts.yaml`, run **Maintenance → reschedule**.

**Running locally instead:** everything also works on your own PC with Python 3.11+ and ffmpeg:
- Install with `pip install -r requirements.txt`.
- Set `CLIPBOT_STORAGE=local` to keep clips on disk.
- Run `python -m clipbot clip`, then `python -m clipbot post`.
