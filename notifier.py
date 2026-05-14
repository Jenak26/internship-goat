#!/usr/bin/env python3
"""
InternshipGOAT Notifier + Telegram Bot
Polls ATS APIs, sends Telegram alerts, and handles bot commands.

Run:   python notifier.py
Force: python notifier.py --force   (re-sends all current matches)

Bot commands (send to your bot on Telegram):
  /help     — all commands
  /list     — companies being tracked
  /add greenhouse databricks
  /remove databricks
  /filter   — show active filters
  /filter india on|off
  /filter year 2028 | /filter year all
  /filter type intern|all
"""

import os, re, sys, json, asyncio, logging
from pathlib import Path
from datetime import datetime, timezone

import httpx, yaml

# ─── Paths & env ──────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SEEN_FILE        = Path("seen_jobs.json")
COMPANIES_FILE   = Path("companies.yaml")
CONFIG_FILE      = Path("config.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("goat")

# ─── Config ───────────────────────────────────────────────────────────────────

CONFIG_DEFAULTS = {
    "india_only":      False,   # strict India/remote filter (overrides global_ok)
    "grad_years":      ["2028"],# show only roles for these years (or unspecified year)
    "internship_only": False,   # if True, skip new-grad-only roles
    "tech_only":       True,    # if True, only alert tech/quant/data roles (filters marketing, legal, etc.)
    "telegram_offset": 0,       # tracks processed Telegram updates
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**CONFIG_DEFAULTS, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    return dict(CONFIG_DEFAULTS)

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

# ─── Intern-role title patterns ───────────────────────────────────────────────

_INTERN_PATTERNS = [
    r"\bintern(?:ship|s)?\b",
    r"\bco-?op\b",
    r"\bsummer\s+(?:analyst|associate|program|intern|\d{4})\b",
    r"\bnew[-\s]?grad(?:uate)?\b",
    r"\bentry[-\s]?level\b",
    r"\bcampus\s+(?:hire|hiring|placement|program(?:me)?|graduate|opportunity|connect|recruitment)\b",
    r"\bfreshers?\b",
    r"\bfresh\s+graduate\b",
    r"\bgraduate\s+(?:engineer|trainee|program|analyst)\b",
    r"\btrainee\b",
    r"\bapprentice\b",
    r"\bearly[-\s]?career\b",
    r"\bassociate\s+(?:engineer|sde|swe|analyst|developer)\b",
    r"\bjunior\s+(?:engineer|developer|sde|swe|analyst)\b",
    r"\banalyst\s+(?:program|trainee)\b",
    r"\b20(?:2[4-9]|30)\s*batch\b",
    r"\bfte\s*20(?:2[4-9])\b",
    r"\bclass\s+of\s+20(?:2[4-9])\b",
]
_INTERN_RE = re.compile("|".join(_INTERN_PATTERNS), re.IGNORECASE)

_INTERN_STRICT_PATTERNS = [   # used when internship_only=True
    r"\bintern(?:ship|s)?\b",
    r"\bco-?op\b",
    r"\bsummer\s+(?:analyst|associate|program|intern|\d{4})\b",
    r"\bcampus\s+(?:hire|hiring|placement|program|recruitment)\b",
    r"\btrainee\b",
    r"\bapprentice\b",
]
_INTERN_STRICT_RE = re.compile("|".join(_INTERN_STRICT_PATTERNS), re.IGNORECASE)

# Tech-role keyword filter — skips marketing, legal, HR, design interns
_TECH_KEYWORDS = [
    "software", "swe", "sde", "engineer", "engineering", "developer", "dev",
    "backend", "frontend", "full stack", "fullstack", "full-stack",
    "data", "ml", "machine learning", "deep learning", "ai ", " ai,", "artificial intelligence",
    "quant", "quantitative", "algo", "algorithmic", "trading", "research",
    "infra", "infrastructure", "devops", "platform", "cloud", "systems",
    "security", "cyber", "network", "embedded", "hardware", "vlsi", "fpga",
    "product manager", "program manager", " pm ", "pm,", "pm-",
    "analytics", "data analyst", "business analyst", "research analyst",
    "quantitative analyst", "scientist", "science",
    "computer", "cs ", " cs,", "tech",
]

def is_tech_role(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _TECH_KEYWORDS)

def is_intern_role(title: str, internship_only: bool = False) -> bool:
    if internship_only:
        return bool(_INTERN_STRICT_RE.search(title))
    return bool(_INTERN_RE.search(title))

# ─── India / location filter ──────────────────────────────────────────────────

INDIA_LOCATIONS = [
    "india", "bangalore", "bengaluru", "hyderabad", "delhi", "mumbai",
    "pune", "gurgaon", "gurugram", "noida", "chennai", "kolkata",
    "ahmedabad", "kochi", "trivandrum", "thiruvananthapuram",
    "remote", "work from anywhere", "anywhere", "worldwide", "global", "hybrid",
]

def is_india_relevant(location: str, company: dict, india_only: bool = False) -> bool:
    if company.get("india_company"):
        return True
    if company.get("global_ok") and not india_only:
        return True
    loc = (location or "").lower().strip()
    if not loc:
        return True
    return any(kw in loc for kw in INDIA_LOCATIONS if kw)

# ─── Year filter ──────────────────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b(202[4-9]|20[3-9][0-9])\b")

def passes_year_filter(title: str, grad_years: list) -> bool:
    """
    Allow the role if:
      - no grad_years filter is configured, OR
      - title has no year mention (open to any batch), OR
      - title mentions a year that matches the filter
    """
    if not grad_years:
        return True
    found = _YEAR_RE.findall(title)
    if not found:
        return True          # no year in title → could be any batch
    return any(y in grad_years for y in found)

# ─── State helpers ────────────────────────────────────────────────────────────

def load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {}
    raw = json.loads(SEEN_FILE.read_text())
    return {k: set(v) for k, v in raw.items() if v}

def save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps({k: list(v) for k, v in seen.items()}, indent=2))

def load_companies() -> list:
    return yaml.safe_load(COMPANIES_FILE.read_text())["companies"]

# ─── ATS fetchers ─────────────────────────────────────────────────────────────

async def fetch_greenhouse(client, token, name):
    try:
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                             params={"content": "false"}, timeout=20)
        r.raise_for_status()
        out = []
        for j in r.json().get("jobs", []):
            loc = j.get("location", {})
            out.append({"id": str(j["id"]), "title": j.get("title", ""),
                        "location": loc.get("name", "") if isinstance(loc, dict) else str(loc),
                        "url": j.get("absolute_url", f"https://boards.greenhouse.io/{token}"),
                        "posted": (j.get("updated_at") or "")[:10]})
        return out
    except Exception as e:
        log.warning(f"[{name}] Greenhouse: {e}")
        return []

async def fetch_lever(client, token, name):
    try:
        all_jobs, offset, limit = [], 0, 250
        while True:
            r = await client.get(f"https://api.lever.co/v0/postings/{token}",
                                 params={"mode": "json", "limit": limit, "skip": offset}, timeout=20)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for j in batch:
                cats = j.get("categories", {})
                all_jobs.append({"id": j.get("id", ""), "title": j.get("text", ""),
                                 "location": cats.get("location", ""),
                                 "url": j.get("hostedUrl") or j.get("applyUrl", ""),
                                 "posted": ""})
            if len(batch) < limit:
                break
            offset += limit
        return all_jobs
    except Exception as e:
        log.warning(f"[{name}] Lever: {e}")
        return []

async def fetch_ashby(client, token, name):
    try:
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=20)
        r.raise_for_status()
        return [{"id": j.get("id", ""), "title": j.get("title", ""),
                 "location": j.get("location", ""),
                 "url": j.get("jobUrl") or j.get("applyUrl", ""),
                 "posted": (j.get("publishedAt") or "")[:10]}
                for j in r.json().get("jobs", [])]
    except Exception as e:
        log.warning(f"[{name}] Ashby: {e}")
        return []

async def fetch_smartrecruiters(client, token, name):
    try:
        all_jobs, offset, limit, cap = [], 0, 100, 500
        while True:
            r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
                                 params={"limit": limit, "offset": offset}, timeout=20)
            r.raise_for_status()
            data = r.json()
            batch = data.get("content", [])
            if not batch:
                break
            for j in batch:
                loc = j.get("location", {})
                parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
                jid = str(j.get("id", ""))
                cid = j.get("company", {}).get("identifier", token)
                all_jobs.append({"id": jid, "title": j.get("name", ""),
                                 "location": ", ".join(p for p in parts if p),
                                 "url": f"https://jobs.smartrecruiters.com/{cid}/{jid}",
                                 "posted": (j.get("releasedDate") or "")[:10]})
            offset += limit
            if offset >= data.get("totalFound", 0) or len(all_jobs) >= cap:
                break
        return all_jobs
    except Exception as e:
        log.warning(f"[{name}] SmartRecruiters: {e}")
        return []

FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever,
            "ashby": fetch_ashby, "smartrecruiters": fetch_smartrecruiters}

# ─── Token verifier (for /add command) ────────────────────────────────────────

async def verify_token(client, ats: str, token: str) -> tuple[bool, int]:
    urls = {
        "greenhouse":      f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        "lever":           f"https://api.lever.co/v0/postings/{token}?mode=json&limit=5",
        "ashby":           f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=5",
    }
    url = urls.get(ats)
    if not url:
        return False, 0
    try:
        r = await client.get(url, timeout=12)
        if r.status_code != 200:
            return False, 0
        data = r.json()
        counts = {"greenhouse": len(data.get("jobs", [])),
                  "lever":      len(data) if isinstance(data, list) else 0,
                  "ashby":      len(data.get("jobs", [])),
                  "smartrecruiters": data.get("totalFound", 0)}
        return True, counts.get(ats, 0)
    except Exception:
        return False, 0

# ─── Telegram ─────────────────────────────────────────────────────────────────

EMOJI = {"HFT/Quant": "📈", "AI/ML": "🤖", "FAANG+": "🔥",
         "Fintech/Banking": "💰", "Fintech/India": "🇮🇳", "Unicorns/India": "🦄",
         "Consulting/IT": "💼", "Semiconductor": "⚡", "Cybersecurity": "🔐",
         "Analytics/Data": "📊", "Automotive": "🚗", "Other": "✨"}

def fmt_alert(company: dict, job: dict) -> str:
    em = EMOJI.get(company.get("category", ""), "✨")
    return (f"{em} *{company['name']}* — new opening\n\n"
            f"💼 *{job['title']}*\n"
            f"📍 {job.get('location') or 'Not specified'}\n"
            f"📅 {job.get('posted') or 'Recently'}\n\n"
            f"[Apply Now]({job.get('url','#')})\n\n"
            f"_InternshipGOAT 🐐_")

async def tg_send(client, text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown", "disable_web_page_preview": False},
            timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram: {e}")
        return False

# ─── Bot command handlers ──────────────────────────────────────────────────────

def cmd_help() -> str:
    return (
        "🐐 *InternshipGOAT — Commands*\n\n"
        "`/list` — Show tracked companies\n"
        "`/add <ats> <token>` — Add company\n"
        "   e.g. `/add greenhouse databricks`\n"
        "   e.g. `/add lever razorpay Razorpay`\n"
        "   e.g. `/add ashby openai OpenAI`\n"
        "`/remove <name>` — Remove company\n\n"
        "*Filters:*\n"
        "`/filter` — Show current filters\n"
        "`/filter india on` — India/remote roles only\n"
        "`/filter india off` — Include global roles\n"
        "`/filter year 2028` — Only 2028-batch roles\n"
        "`/filter year 2026,2028` — Multiple years\n"
        "`/filter year all` — Remove year filter\n"
        "`/filter type intern` — Internships only\n"
        "`/filter type all` — Intern + new grad\n\n"
        "*ATS types:* `greenhouse` `lever` `ashby` `smartrecruiters`"
    )

def cmd_list() -> str:
    companies = load_companies()
    by_cat = {}
    for c in companies:
        cat = c.get("category", "Other")
        by_cat.setdefault(cat, []).append(c["name"])

    lines = [f"📋 *Tracked Companies ({len(companies)})*\n"]
    for cat, names in sorted(by_cat.items()):
        em = EMOJI.get(cat, "✨")
        lines.append(f"{em} *{cat}* ({len(names)})")
        lines.append("   " + " · ".join(names))
    lines.append(f"\n_Use /add to add more_")
    return "\n".join(lines)

async def cmd_add(client, args: list) -> str:
    if len(args) < 2:
        return ("Usage: `/add <ats> <token> [name]`\n"
                "ATS: `greenhouse` `lever` `ashby` `smartrecruiters`\n\n"
                "Examples:\n"
                "`/add greenhouse databricks`\n"
                "`/add lever razorpay Razorpay`\n"
                "`/add ashby openai OpenAI`")

    ats   = args[0].lower()
    token = args[1]
    name  = " ".join(args[2:]) if len(args) > 2 else token.replace("-", " ").title()

    valid_ats = ("greenhouse", "lever", "ashby", "smartrecruiters")
    if ats not in valid_ats:
        return f"❌ Invalid ATS `{ats}`\nChoose: {', '.join(valid_ats)}"

    data = yaml.safe_load(COMPANIES_FILE.read_text())
    existing = [c["name"].lower() for c in data["companies"]]
    if name.lower() in existing:
        return f"⚠️ *{name}* is already in the list."

    log.info(f"Verifying {ats} token '{token}'...")
    ok, count = await verify_token(client, ats, token)
    if not ok:
        return (f"❌ Token `{token}` not valid for *{ats}*\n"
                f"Check the slug and try again.\n\n"
                f"Greenhouse: `boards.greenhouse.io/{{slug}}`\n"
                f"Lever: `jobs.lever.co/{{slug}}`\n"
                f"Ashby: `jobs.ashbyhq.com/{{slug}}`")

    data["companies"].append({
        "name": name, "ats": ats, "token": token,
        "category": "Other", "india_company": False, "global_ok": False,
    })
    COMPANIES_FILE.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))
    return f"✅ Added *{name}*\n_{ats}: {token} — {count} jobs on board_\nWill scan on next cycle."

def cmd_remove(args: list) -> str:
    if not args:
        return "Usage: `/remove <company name>`\ne.g. `/remove Databricks`"
    name = " ".join(args)
    data = yaml.safe_load(COMPANIES_FILE.read_text())
    before = len(data["companies"])
    data["companies"] = [c for c in data["companies"]
                         if c["name"].lower() != name.lower()]
    if len(data["companies"]) == before:
        # Try partial match
        matches = [c for c in data["companies"] if name.lower() in c["name"].lower()]
        if matches:
            return (f"❌ No exact match for *{name}*\nDid you mean:\n"
                    + "\n".join(f"  · {c['name']}" for c in matches))
        return f"❌ *{name}* not found."
    COMPANIES_FILE.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))
    return f"🗑️ Removed *{name}* from tracking."

def cmd_filter_show(cfg: dict) -> str:
    years = ", ".join(cfg.get("grad_years") or []) or "all years"
    role  = "Internship only" if cfg.get("internship_only") else "All (intern + new grad)"
    india = "ON — India/remote only" if cfg.get("india_only") else "OFF — India + global_ok companies"
    return (
        "⚙️ *Active Filters*\n\n"
        f"🇮🇳 India-only: *{india}*\n"
        f"🎓 Grad years: *{years}*\n"
        f"💼 Role type: *{role}*\n\n"
        "Use `/filter india on|off`, `/filter year 2028`, `/filter type intern|all`"
    )

def cmd_filter(args: list, cfg: dict) -> str:
    if not args:
        return cmd_filter_show(cfg)

    sub = args[0].lower()

    if sub == "india":
        val = args[1].lower() if len(args) > 1 else ""
        if val == "on":
            cfg["india_only"] = True
            return "🇮🇳 India-only mode *ON*\nOnly India/remote roles will alert."
        elif val == "off":
            cfg["india_only"] = False
            return "🌐 India-only mode *OFF*\nGlobal roles from elite firms will also alert."
        return "Usage: `/filter india on` or `/filter india off`"

    elif sub == "year":
        val = " ".join(args[1:]).lower() if len(args) > 1 else ""
        if val in ("all", "clear", "none", ""):
            cfg["grad_years"] = []
            return "🎓 Year filter *cleared* — all grad years will alert."
        years = [y.strip() for y in val.replace(" ", ",").split(",") if re.match(r"20\d\d", y.strip())]
        if not years:
            return "❌ Invalid year. Use `/filter year 2028` or `/filter year 2026,2028`"
        cfg["grad_years"] = years
        return f"🎓 Grad year filter set to *{', '.join(years)}*\nRoles with other years will be skipped."

    elif sub == "type":
        val = args[1].lower() if len(args) > 1 else ""
        if val == "intern":
            cfg["internship_only"] = True
            return "💼 Role type: *Internships only*\nNew-grad-only roles will be skipped."
        elif val in ("all", "any"):
            cfg["internship_only"] = False
            return "💼 Role type: *All* (intern + new grad)"
        return "Usage: `/filter type intern` or `/filter type all`"

    return cmd_filter_show(cfg)

def cmd_status() -> str:
    companies = load_companies()
    seen = load_seen()
    total_seen = sum(len(v) for v in seen.values())
    return (f"📊 *InternshipGOAT Status*\n\n"
            f"Companies tracked: *{len(companies)}*\n"
            f"Jobs in memory: *{total_seen}*\n"
            f"Scan interval: *every 10 min* (GitHub Actions)\n"
            f"Last seen file: *{SEEN_FILE.stat().st_size // 1024} KB*")

# ─── Telegram command dispatcher ──────────────────────────────────────────────

async def process_commands(client, cfg: dict) -> dict:
    """Fetch new Telegram messages, handle commands, return updated cfg."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return cfg
    try:
        r = await client.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": cfg.get("telegram_offset", 0), "timeout": 0},
            timeout=10)
        updates = r.json().get("result", [])
    except Exception as e:
        log.warning(f"getUpdates failed: {e}")
        return cfg

    for upd in updates:
        cfg["telegram_offset"] = upd["update_id"] + 1
        msg  = upd.get("message", {})
        text = msg.get("text", "").strip()
        cid  = str(msg.get("chat", {}).get("id", ""))

        if cid != TELEGRAM_CHAT_ID or not text.startswith("/"):
            continue

        parts = text.split()
        cmd   = parts[0].lower().split("@")[0]
        args  = parts[1:]
        log.info(f"Bot command: {cmd} {args}")

        reply = None
        if   cmd == "/help":    reply = cmd_help()
        elif cmd == "/list":    reply = cmd_list()
        elif cmd == "/add":     reply = await cmd_add(client, args)
        elif cmd == "/remove":  reply = cmd_remove(args)
        elif cmd == "/filter":  reply = cmd_filter(args, cfg)
        elif cmd == "/status":  reply = cmd_status()
        else:
            reply = "Unknown command. Send /help for the list."

        if reply:
            await tg_send(client, reply)

    return cfg

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    force = "--force" in sys.argv
    cfg   = load_config()
    seen  = load_seen()
    first_run = (not seen) and not force

    if first_run:
        log.info("First run — populating seen_jobs.json without sending alerts.")
    elif force:
        log.info("--force mode — resending all current matches.")

    async with httpx.AsyncClient(
        headers={"User-Agent": "InternshipGOAT/2.0"},
        follow_redirects=True,
    ) as client:

        # ── Process any pending Telegram commands first ──────────────────────
        cfg = await process_commands(client, cfg)
        save_config(cfg)

        # ── Scan ─────────────────────────────────────────────────────────────
        companies   = load_companies()
        total_alerts = 0
        scanned      = 0

        india_only      = cfg.get("india_only", False)
        grad_years      = cfg.get("grad_years", [])
        internship_only = cfg.get("internship_only", False)
        tech_only       = cfg.get("tech_only", True)

        for company in companies:
            ats     = company.get("ats", "")
            token   = company.get("token", "")
            name    = company["name"]
            fetcher = FETCHERS.get(ats)
            if not fetcher or not token:
                continue

            jobs = await fetcher(client, token, name)
            if not jobs:
                continue

            scanned += 1
            key      = name.lower().replace(" ", "_").replace(".", "")
            seen_set = seen.setdefault(key, set())
            alerts   = []

            for j in jobs:
                jid = j.get("id", "")
                if not jid or jid in seen_set:
                    continue
                seen_set.add(jid)

                if not is_intern_role(j["title"], internship_only):
                    continue
                if tech_only and not is_tech_role(j["title"]):
                    continue
                if not passes_year_filter(j["title"], grad_years):
                    continue
                if not is_india_relevant(j.get("location", ""), company, india_only):
                    continue

                alerts.append(j)

            if alerts and not first_run:
                for j in alerts:
                    log.info(f"  ALERT [{name}] {j['title']} — {j.get('location','?')}")
                    await tg_send(client, fmt_alert(company, j))
                    total_alerts += 1
                    await asyncio.sleep(0.4)
            elif alerts and first_run:
                log.info(f"[{name}] Bootstrap: {len(alerts)} intern roles noted")

            log.info(f"[{name}] {len(jobs)} total  |  {len(alerts)} new relevant")

        # ── Summary ───────────────────────────────────────────────────────────
        if not first_run and total_alerts > 0:
            ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            msg = (f"🐐 *Scan complete* — {ts}\n"
                   f"Scanned: *{scanned}* companies\n"
                   f"Alerts sent: *{total_alerts}*")
            await tg_send(client, msg)

    save_seen(seen)
    save_config(cfg)

    if first_run:
        log.info("Bootstrap done. Run again to start receiving alerts.")
    else:
        log.info(f"Done. {total_alerts} alerts sent across {scanned} companies.")

if __name__ == "__main__":
    asyncio.run(main())
