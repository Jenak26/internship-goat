# InternshipGOAT — Claude Reference

## What This Is
Lightweight Telegram alert agent for internship/new-grad job openings.
Polls public ATS APIs every 10 minutes (via GitHub Actions), dedupes, filters for India-relevant roles, sends Telegram message instantly on new posts.

## File Map
```
notifier.py          — main script (run this)
add_company.py       — CLI to add new companies interactively
get_chat_id.py       — one-time helper to find your Telegram chat ID
companies.yaml       — list of 96 companies with ATS configs
seen_jobs.json       — dedup state (auto-managed, committed by Actions)
requirements.txt     — httpx, pyyaml
.env                 — secrets (gitignored)
.github/workflows/
  scan.yml           — GitHub Actions cron (every 10 min)
```

## How It Works
1. `notifier.py` loads `companies.yaml` and `seen_jobs.json`
2. For each company, hits the public ATS API (Greenhouse/Lever/Ashby/SmartRecruiters)
3. Any job ID not in `seen_jobs.json` → check title for intern/new-grad keywords
4. Check location for India relevance (or use `india_company`/`global_ok` flags)
5. Send Telegram message → save updated `seen_jobs.json`
6. **First run**: populates seen_jobs.json WITHOUT sending alerts (avoids flood)

## Company Flags
| Flag | Meaning |
|---|---|
| `india_company: true` | Indian company — alert for ALL roles, any location |
| `global_ok: true` | Elite firm (HFT, remote-first) — alert for ALL global locations |
| *(neither)* | Alert only if location contains India city/remote keywords |

## Adding Companies
```bash
# Interactive (recommended):
python add_company.py

# One-liner:
python add_company.py --name "Palantir" --ats lever --token palantir --category FAANG+
```
Validates the token hits a live API before adding.

## ATS Types & How to Find Tokens
| ATS | Token source |
|---|---|
| greenhouse | `boards.greenhouse.io/{TOKEN}` |
| lever | `jobs.lever.co/{TOKEN}` |
| ashby | `jobs.ashbyhq.com/{TOKEN}` |
| smartrecruiters | company identifier in SmartRecruiters URL |

## Setup (one-time)
```bash
pip install -r requirements.txt

# Get chat ID:
python get_chat_id.py          # send any msg to bot first

# Fill .env:
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Bootstrap (run once, no alerts sent):
python notifier.py

# Then run again — now alerts fire on new jobs:
python notifier.py
```

## GitHub Actions Setup (for 24/7 scanning with laptop off)
1. Push this repo to GitHub (private repo)
2. Go to Settings → Secrets → Actions
3. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as secrets
4. The workflow in `.github/workflows/scan.yml` runs every 10 minutes automatically

## Companies Covered (96 total with live APIs)
- **HFT/Quant (12):** Jane Street, Citadel, HRT, Two Sigma, Optiver, Tower Research, IMC, Akuna, Jump, DRW, Five Rings, Maven
- **AI/ML (20):** OpenAI, Anthropic, xAI, Perplexity, Cohere, Mistral, HuggingFace, Scale AI, W&B, Character.AI, Runway, Stability, Replit, Together, Pinecone, Lambda Labs, Cerebras, SambaNova, Groq, Glean, AI21, Adept, Inflection
- **FAANG+ (25):** Netflix, Uber, Airbnb, Dropbox, Spotify, Atlassian, Twilio, Cloudflare, Databricks, Snowflake, GitHub, GitLab, MongoDB, Confluent, HashiCorp, Datadog, Fastly, Okta, HubSpot, Reddit, Discord, Figma, Notion, Vercel, Netlify, Elastic, Stripe, Robinhood, Plaid
- **Fintech/India (8):** Razorpay, CRED, Groww, Upstox, JUSPAY, Navi, Fi Money, CoinDCX
- **Unicorns/India (10):** Meesho, Zepto, Dream11, ShareChat, Urban Company, InMobi, BrowserStack, Postman, Freshworks, Innovaccer
- **Other:** ThoughtWorks, CrowdStrike, Zscaler, Rubrik, SentinelOne, Snyk, Grafana, Redis, Neo4j, DataStax, Bosch, Arista, TigerGraph, SAP

## Why These 96 Only?
The other ~174 companies in the original list use:
- **Workday** without a myworkdayjobs.com URL → falls back to HTML scraping → unreliable
- **Generic** (Google, Meta, Amazon, etc.) → custom JS-rendered pages → need Playwright → heavy

These 96 all have **public JSON APIs** → 100% reliable, no browser needed.

## Adding More Companies (Research How-To)
To check if a company uses a supported ATS, visit their careers page and look at:
- URL for `greenhouse.io`, `lever.co`, `ashbyhq.com`, `smartrecruiters.com`
- Or check their job listings on LinkedIn and copy the "Apply" redirect URL

## Token Sharing Warning
⚠️ The Telegram bot token was shared in chat. Consider regenerating it via @BotFather if security is a concern. Store tokens only in `.env` (gitignored) or GitHub Secrets — never commit them.
