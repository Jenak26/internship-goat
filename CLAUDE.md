# InternshipGOAT — Claude Reference

## What This Is
Lightweight Telegram alert agent for internship/new-grad job openings.
- **notifier.py** — scans 53 companies via public ATS APIs every 10 min (GitHub Actions)
- **listen.py** — persistent Telegram bot listener hosted on Render.com (always on)

## Live Deployment
- **GitHub repo:** https://github.com/Jenak26/internship-goat (private)
- **Render service:** https://internship-goat.onrender.com (listen.py)
- **UptimeRobot:** pings Render every 5 min to prevent sleep
- **GitHub Actions:** runs notifier.py every 10 min via scan.yml cron

## File Map
```
notifier.py          — scanner (GitHub Actions, every 10 min)
listen.py            — bot command listener (Render, always on)
add_company.py       — CLI to add new companies interactively
get_chat_id.py       — one-time helper to find your Telegram chat ID
companies.yaml       — 53 companies with ATS configs
portals.yaml         — 130+ manual career portal links (no-API companies)
seen_jobs.json       — dedup state (auto-managed, committed by Actions)
config.json          — filter settings (updated by bot commands)
requirements.txt     — httpx, pyyaml
render.yaml          — Render deployment config
.env                 — secrets (gitignored)
.github/workflows/
  scan.yml           — GitHub Actions cron (every 10 min)
```

## How It Works
1. `notifier.py` loads `companies.yaml` and `seen_jobs.json`
2. Fetches all 53 companies in parallel via asyncio.gather()
3. Any job ID not in `seen_jobs.json` → check title for intern/new-grad keywords
4. Check location for India relevance (or use `india_company`/`global_ok` flags)
5. Apply tech_only and grad_years filters from config.json
6. Send Telegram message → save updated `seen_jobs.json` → commit back to repo
7. **First run**: populates seen_jobs.json WITHOUT sending alerts (avoids flood)

`listen.py` runs separately on Render, polling Telegram every 2 seconds for commands.
It also exposes a `/health` HTTP endpoint (port from $PORT env var) for UptimeRobot.

## Company Flags
| Flag | Meaning |
|---|---|
| `india_company: true` | Indian company — alert for ALL roles, any location |
| `global_ok: true` | Elite firm (HFT, remote-first) — alert for ALL global locations |
| *(neither)* | Alert only if location contains India city/remote keywords |

## Config Defaults (config.json)
```json
{
  "india_only": false,
  "grad_years": ["2028"],
  "internship_only": false,
  "tech_only": true,
  "telegram_offset": 0
}
```

## Bot Commands
| Command | Effect |
|---|---|
| `/help` | Show all commands |
| `/rolesactive` | Scan all 53 companies right now, show active India roles + portal links |
| `/list` | List tracked companies |
| `/filter india on/off` | Toggle India-only mode |
| `/filter year 2028` | Filter by grad year |
| `/filter year all` | Clear year filter |
| `/filter type intern/all` | Internships only or all |
| `/filter role tech/all` | Tech roles only or all |
| `/add <ats> <token> [name]` | Add a company |
| `/remove <name>` | Remove a company |
| `/status` | Show stats and current filters |

## ATS Types & Tokens
| ATS | API pattern | Token source |
|---|---|---|
| greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs` | `boards.greenhouse.io/{TOKEN}` |
| lever | `api.lever.co/v0/postings/{token}` | `jobs.lever.co/{TOKEN}` |
| ashby | `api.ashbyhq.com/posting-api/job-board/{token}` | `jobs.ashbyhq.com/{TOKEN}` |
| smartrecruiters | `api.smartrecruiters.com/v1/companies/{token}/postings` | company ID in SR URL |

## Adding Companies
```bash
python add_company.py                          # interactive (recommended)
python add_company.py --name "X" --ats lever --token x --category "FAANG+"
```
Validates token hits live API before saving.

## Key Filters in Code
- **Intern detection:** regex word-boundary patterns (`\bintern\b` NOT matching "internal")
- **Tech filter:** keyword list in `_TECH_KEYWORDS` — `is_tech_role(title)`
- **Year filter:** `passes_year_filter(title, grad_years)` — if no year in title, passes through
- **India filter:** `is_india_relevant(location, company)` — checks india_company/global_ok flags first

## Companies Covered (53 with verified live APIs)
- **HFT/Quant:** Jane Street, Citadel, Tower Research, IMC, Akuna Capital, Jump Trading, Optiver, DRW
- **AI/ML:** OpenAI, Anthropic, Perplexity, Cohere, Mistral, Pinecone, Replit, Snyk, Stability AI
- **FAANG+:** Netflix, Airbnb, Dropbox, Spotify, Twilio, Cloudflare, Databricks, MongoDB, GitLab, Datadog, Okta, HubSpot, Reddit, Discord, Figma, Notion, Vercel, Netlify, Elastic, Stripe, Robinhood, Plaid, Fastly, Zscaler, Rubrik
- **Fintech/India:** Groww, CRED, Navi, Fi Money, Meesho, InMobi
- **Enterprise:** SAP, Bosch, Confluent, Neo4j, TigerGraph, ThoughtWorks

## Why Only 53?
Other companies use Workday (no public JSON API) or custom JS-rendered pages (need Playwright). These 53 all have reliable public JSON APIs.

## Security Notes
- `.env` is gitignored — never commit it
- Secrets live in `.env` locally, GitHub Actions Secrets, and Render environment variables
- Bot token: if exposed, regenerate via @BotFather immediately
