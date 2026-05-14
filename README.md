# InternshipGOAT 🐐

> Be the **first** to know when internship and new-grad roles open at top tech companies — straight to your Telegram, 24/7, even with your laptop off.

InternshipGOAT polls the public job APIs of 53 top companies every 10 minutes via GitHub Actions and sends you an instant Telegram alert the moment a matching role is posted. No scraping, no browser automation — just clean JSON APIs that are fast and reliable.

---

## What It Does

- Scans **53 companies** across Greenhouse, Lever, Ashby, and SmartRecruiters APIs
- Sends a **Telegram alert** within 10 minutes of a new posting going live
- Filters for **intern / new-grad / fresher** roles only (no senior roles cluttering your feed)
- Filters for **India-relevant** locations (Bangalore, Hyderabad, Delhi, Remote, etc.)
- Optional **tech-role filter** to skip non-technical positions
- Optional **graduation year filter** (e.g. 2028-batch only)
- **Telegram bot commands** to control everything on the fly
- Runs **24/7 on GitHub Actions** — free, no server needed

---

## Companies Covered (53 with live public APIs)

| Category | Companies |
|---|---|
| **HFT / Quant** | Jane Street, Citadel, Tower Research, IMC, Akuna Capital, Jump Trading, Optiver, DRW |
| **AI / ML** | OpenAI, Anthropic, Perplexity, Cohere, Mistral, Pinecone, Replit, Snyk, Stability AI |
| **FAANG+** | Netflix, Airbnb, Dropbox, Spotify, Twilio, Cloudflare, Databricks, MongoDB, GitLab, Datadog, Okta, HubSpot, Reddit, Discord, Figma, Notion, Vercel, Netlify, Elastic, Stripe, Robinhood, Plaid, Fastly, Zscaler, Rubrik |
| **Fintech / India** | Groww, CRED, Navi, Fi Money, Meesho, InMobi |
| **Enterprise** | SAP, Bosch, Confluent, Neo4j, TigerGraph, ThoughtWorks |

> **Why only 53?** The other major companies (Google, Meta, Amazon, Goldman Sachs, etc.) use Workday or custom JS-rendered career pages — no public JSON API. For those, InternshipGOAT includes a curated list of 130+ pre-filtered career portal links you can check manually via `/rolesactive`.

---

## Bot Commands

| Command | What it does |
|---|---|
| `/help` | Show all commands |
| `/rolesactive` | 🔥 Scan all companies right now and show active India roles |
| `/list` | Show all tracked companies |
| `/filter india on/off` | Toggle India/remote-only mode |
| `/filter year 2028` | Only show 2028-batch roles |
| `/filter year 2026,2028` | Multiple years |
| `/filter year all` | Clear year filter |
| `/filter type intern` | Internships only (no new-grad) |
| `/filter type all` | Intern + new-grad roles |
| `/filter role tech` | Tech roles only |
| `/filter role all` | All roles |
| `/add <ats> <token> [name]` | Add a new company |
| `/remove <name>` | Remove a company |
| `/status` | Show stats and current filter settings |

---

## How It Works

```
GitHub Actions (every 10 min)
        │
        ▼
  notifier.py
        │
        ├── Greenhouse API  ──┐
        ├── Lever API         ├── Parallel fetch all 53 companies
        ├── Ashby API         │
        └── SmartRecruiters ──┘
        │
        ▼
  Filter jobs:
  ✓ New (not in seen_jobs.json)?
  ✓ Intern / new-grad title?
  ✓ Tech role?
  ✓ India-relevant location?
  ✓ Right graduation year?
        │
        ▼
  Telegram alert sent → seen_jobs.json updated → committed back to repo
```

Your local `listen.py` runs separately and handles bot commands in real time (the GitHub Actions scanner only sends job alerts, not command responses).

---

## Setup for Yourself

### 1. Clone this repo

```bash
git clone https://github.com/Jenak26/internship-goat.git
cd internship-goat
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create your Telegram bot

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** it gives you
4. Start a chat with your new bot, send any message
5. Run this to get your chat ID:
   ```bash
   python get_chat_id.py
   ```

### 4. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### 5. Bootstrap (first run — no alerts sent)

```bash
python notifier.py
```

This populates `seen_jobs.json` with all current jobs so you only get alerted about **new** postings going forward, not the flood of everything already live.

### 6. Test it

```bash
python notifier.py --force
```

This sends alerts for everything matching your filters right now (good for verifying it works).

### 7. Start the bot listener (for commands)

```bash
python listen.py
```

Keep this running locally. Now you can use all the `/commands` in Telegram.

---

## 24/7 Setup via GitHub Actions (scanner runs even with laptop off)

This is the main value-add — the scanner runs on GitHub's free compute every 10 minutes, no laptop needed.

### 1. Fork this repo

Click **Fork** at the top right of this GitHub page.

### 2. Push your local changes to your fork

If you cloned and made changes (your own `.env`, companies, etc.):
```bash
git remote set-url origin https://github.com/YOUR_USERNAME/internship-goat.git
git push -u origin main
```

### 3. Add secrets to GitHub

Go to your fork → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID (from `get_chat_id.py`) |

### 4. Enable Actions

Go to the **Actions** tab in your fork and click **"I understand my workflows, go ahead and enable them"** if prompted.

### 5. Trigger first run

In the Actions tab, click **InternshipGOAT — Job Scanner** → **Run workflow**.

That's it. The scanner now runs every 10 minutes automatically. Check the Actions tab to see run history and logs.

---

## Adding More Companies

### Interactive (recommended)

```bash
python add_company.py
```

Walks you through it with prompts and validates the token hits a live API before saving.

### One-liner

```bash
python add_company.py --name "Palantir" --ats lever --token palantir --category "FAANG+"
```

### How to find a company's ATS token

Visit the company's careers page and look at the URL or the "Apply" button redirect:

| If the URL contains | ATS type | Token |
|---|---|---|
| `greenhouse.io/TOKEN` | greenhouse | the part after the last `/` |
| `jobs.lever.co/TOKEN` | lever | the part after the last `/` |
| `jobs.ashbyhq.com/TOKEN` | ashby | the part after the last `/` |
| `smartrecruiters.com/TOKEN` | smartrecruiters | the company identifier in the URL |

---

## Configuration

`config.json` stores your filter preferences (auto-updated by bot commands):

```json
{
  "india_only": false,
  "grad_years": ["2028"],
  "internship_only": false,
  "tech_only": true,
  "telegram_offset": 0
}
```

`companies.yaml` stores company configs. Key flags:

| Flag | Meaning |
|---|---|
| `india_company: true` | Indian company — alert for ALL roles regardless of location |
| `global_ok: true` | Elite global firm (HFT, remote-first) — alert for all locations |
| *(neither)* | Alert only if location contains India city / remote keyword |

---

## File Structure

```
notifier.py              — Main scanner (runs via GitHub Actions)
listen.py                — Telegram bot listener (run locally for commands)
add_company.py           — CLI to add new companies
get_chat_id.py           — One-time helper to find your Telegram chat ID
companies.yaml           — 53 companies with ATS configs
portals.yaml             — 130+ manual career portal links (for companies without public APIs)
seen_jobs.json           — Dedup state (auto-managed, committed by Actions)
config.json              — Filter settings (auto-managed by bot commands)
requirements.txt         — httpx, pyyaml
.env                     — Secrets (gitignored — never commit this)
.github/workflows/
  scan.yml               — GitHub Actions cron (every 10 min)
```

---

## Limitations & Known Gaps

- **Google, Meta, Amazon, Microsoft, Goldman Sachs, Flipkart** and ~170 other companies use Workday or custom JS-rendered career pages with no public JSON API. Use `/rolesactive` to see the curated list of their direct career portal links.
- GitHub Actions cron has a minimum interval of ~5 minutes and may occasionally run a few minutes late during high-load periods.
- The `seen_jobs.json` state is committed back to the repo after each scan — this keeps dedup working across runs without any external database.

---

## Security Notes

- **Never commit your `.env` file** — it's in `.gitignore` for a reason.
- Store your bot token and chat ID only in `.env` locally and as GitHub Secrets for Actions.
- If your bot token gets exposed (e.g. shared in a chat), regenerate it immediately via @BotFather — old tokens are instantly invalidated.

---

## License

MIT — use it, fork it, improve it.
