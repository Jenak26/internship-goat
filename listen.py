#!/usr/bin/env python3
"""
InternshipGOAT — Live Bot Listener
Runs persistently, polls Telegram every 3 seconds, responds to commands instantly.

Keep this running locally while you use the bot:
    python listen.py

The scanner (notifier.py) still runs on GitHub Actions every 10 min for job alerts.
This listener only handles commands — it never sends job alerts itself.

Commands:
    /help   /list   /add   /remove   /filter   /status
"""

import os, json, asyncio, logging, signal, sys, threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

import httpx, yaml

# ── Shared state files (same as notifier.py) ──────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
COMPANIES_FILE   = Path("companies.yaml")
SEEN_FILE        = Path("seen_jobs.json")
CONFIG_FILE      = Path("config.json")
POLL_INTERVAL    = 2   # seconds between Telegram polls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("listen")

# ── Load .env if present ──────────────────────────────────────────────────────
_env = Path(".env")
if _env.exists() and not TELEGRAM_TOKEN:
    for line in _env.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
    TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Config helpers ─────────────────────────────────────────────────────────────
CONFIG_DEFAULTS = {
    "india_only": False, "grad_years": ["2028"],
    "internship_only": False, "tech_only": True, "telegram_offset": 0,
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

def load_companies() -> list:
    return yaml.safe_load(COMPANIES_FILE.read_text())["companies"]

# ── Telegram helpers ──────────────────────────────────────────────────────────
async def tg_send(client: httpx.AsyncClient, text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured.")
        return False
    try:
        r = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.error(f"Telegram {r.status_code}: {r.text[:120]}")
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False

# ── ATS fetchers (same as notifier.py) ───────────────────────────────────────
async def fetch_greenhouse(client, token, name):
    try:
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                             params={"content":"false"}, timeout=20)
        r.raise_for_status()
        out = []
        for j in r.json().get("jobs",[]):
            loc = j.get("location",{})
            out.append({"id":str(j["id"]),"title":j.get("title",""),
                        "location":loc.get("name","") if isinstance(loc,dict) else str(loc),
                        "url":j.get("absolute_url",f"https://boards.greenhouse.io/{token}"),
                        "posted":(j.get("updated_at") or "")[:10]})
        return out
    except Exception as e:
        log.debug(f"[{name}] GH: {e}"); return []

async def fetch_lever(client, token, name):
    try:
        all_jobs, offset, limit = [], 0, 250
        while True:
            r = await client.get(f"https://api.lever.co/v0/postings/{token}",
                                 params={"mode":"json","limit":limit,"skip":offset}, timeout=20)
            r.raise_for_status()
            batch = r.json()
            if not batch: break
            for j in batch:
                cats = j.get("categories",{})
                all_jobs.append({"id":j.get("id",""),"title":j.get("text",""),
                                 "location":cats.get("location",""),
                                 "url":j.get("hostedUrl") or j.get("applyUrl",""),"posted":""})
            if len(batch) < limit: break
            offset += limit
        return all_jobs
    except Exception as e:
        log.debug(f"[{name}] LV: {e}"); return []

async def fetch_ashby(client, token, name):
    try:
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", timeout=20)
        r.raise_for_status()
        return [{"id":j.get("id",""),"title":j.get("title",""),
                 "location":j.get("location",""),
                 "url":j.get("jobUrl") or j.get("applyUrl",""),
                 "posted":(j.get("publishedAt") or "")[:10]}
                for j in r.json().get("jobs",[])]
    except Exception as e:
        log.debug(f"[{name}] AB: {e}"); return []

async def fetch_smartrecruiters(client, token, name):
    try:
        all_jobs, offset, limit, cap = [], 0, 100, 500
        while True:
            r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
                                 params={"limit":limit,"offset":offset}, timeout=20)
            r.raise_for_status()
            data = r.json(); batch = data.get("content",[])
            if not batch: break
            for j in batch:
                loc=j.get("location",{}); jid=str(j.get("id",""))
                cid=j.get("company",{}).get("identifier",token)
                parts=[loc.get("city",""),loc.get("region",""),loc.get("country","")]
                all_jobs.append({"id":jid,"title":j.get("name",""),
                                 "location":", ".join(p for p in parts if p),
                                 "url":f"https://jobs.smartrecruiters.com/{cid}/{jid}","posted":""})
            offset += limit
            if offset >= data.get("totalFound",0) or len(all_jobs) >= cap: break
        return all_jobs
    except Exception as e:
        log.debug(f"[{name}] SR: {e}"); return []

# ── ATS token verifier ────────────────────────────────────────────────────────
async def verify_token(client: httpx.AsyncClient, ats: str, token: str) -> tuple[bool, int]:
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
        counts = {
            "greenhouse":      len(data.get("jobs", [])),
            "lever":           len(data) if isinstance(data, list) else 0,
            "ashby":           len(data.get("jobs", [])),
            "smartrecruiters": data.get("totalFound", 0),
        }
        return True, counts.get(ats, 0)
    except Exception:
        return False, 0

# ── Command handlers ──────────────────────────────────────────────────────────
EMOJI = {
    "HFT/Quant": "📈", "AI/ML": "🤖", "FAANG+": "🔥",
    "Fintech/Banking": "💰", "Fintech/India": "🇮🇳", "Unicorns/India": "🦄",
    "Consulting/IT": "💼", "Semiconductor": "⚡", "Cybersecurity": "🔐",
    "Analytics/Data": "📊", "Automotive": "🚗", "Other": "✨",
}

_TECH_KW = [
    "software","swe","sde","engineer","engineering","developer","dev",
    "backend","frontend","full stack","fullstack","full-stack",
    "data","ml","machine learning","deep learning","ai ","artificial intelligence",
    "quant","quantitative","algo","algorithmic","trading","research",
    "infra","infrastructure","devops","platform","cloud","systems",
    "security","cyber","network","embedded","hardware","vlsi","fpga",
    "product manager","program manager"," pm ",
    "data analyst","business analyst","research analyst","quantitative analyst",
    "analytics","scientist","science","computer","cs ","tech",
]

def is_tech_role(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _TECH_KW)

def cmd_help() -> str:
    return (
        "🐐 *InternshipGOAT Commands*\n\n"
        "`/list` — Show tracked companies\n"
        "`/add <ats> <token>` — Add company\n"
        "   `/add greenhouse databricks`\n"
        "   `/add lever razorpay Razorpay`\n"
        "   `/add ashby openai OpenAI`\n"
        "`/remove <name>` — Stop tracking\n\n"
        "*Filters (save between sessions):*\n"
        "`/filter` — Current filter settings\n"
        "`/filter india on` — India/remote only\n"
        "`/filter india off` — Include global roles\n"
        "`/filter year 2028` — 2028-batch roles only\n"
        "`/filter year 2026,2028` — Multiple years\n"
        "`/filter year all` — Clear year filter\n"
        "`/filter type intern` — Internships only\n"
        "`/filter type all` — Intern + new grad\n\n"
        "`/status` — Stats\n"
        "`/rolesactive` — 🔥 Show ALL active India roles right now\n\n"
        "*ATS types:* `greenhouse` `lever` `ashby` `smartrecruiters`\n"
        "_Note: /add and /filter changes apply on next scan cycle_"
    )

def cmd_list() -> str:
    companies = load_companies()
    by_cat: dict[str, list[str]] = {}
    for c in companies:
        cat = c.get("category", "Other")
        by_cat.setdefault(cat, []).append(c["name"])
    lines = [f"📋 *Tracked Companies ({len(companies)})*\n"]
    for cat, names in sorted(by_cat.items()):
        em = EMOJI.get(cat, "✨")
        lines.append(f"{em} *{cat}* ({len(names)})")
        # break into lines of ~4 to avoid mega-long lines
        for i in range(0, len(names), 4):
            lines.append("   " + " · ".join(names[i:i+4]))
    lines.append("\n_Use /add to add more_")
    return "\n".join(lines)

async def cmd_add(client: httpx.AsyncClient, args: list) -> str:
    if len(args) < 2:
        return (
            "Usage: `/add <ats> <token> [name]`\n\n"
            "ATS options: `greenhouse` `lever` `ashby` `smartrecruiters`\n\n"
            "Examples:\n"
            "`/add greenhouse databricks`\n"
            "`/add lever razorpay Razorpay`\n"
            "`/add ashby openai OpenAI`\n\n"
            "*How to find the token:*\n"
            "Greenhouse → look at URL: `boards.greenhouse.io/{TOKEN}`\n"
            "Lever → look at URL: `jobs.lever.co/{TOKEN}`\n"
            "Ashby → look at URL: `jobs.ashbyhq.com/{TOKEN}`"
        )
    ats   = args[0].lower()
    token = args[1].lower()
    name  = " ".join(args[2:]).strip() if len(args) > 2 else token.replace("-", " ").title()

    valid = ("greenhouse", "lever", "ashby", "smartrecruiters")
    if ats not in valid:
        return f"❌ Unknown ATS `{ats}`\nChoose: `{'` `'.join(valid)}`"

    data = yaml.safe_load(COMPANIES_FILE.read_text())
    if any(c["name"].lower() == name.lower() for c in data["companies"]):
        return f"⚠️ *{name}* is already being tracked."

    await tg_send(client, f"⏳ Verifying `{ats}` token `{token}`...")
    ok, count = await verify_token(client, ats, token)
    if not ok:
        return (
            f"❌ Token `{token}` not valid for *{ats}*\n\n"
            f"Double-check the slug — visit the company's jobs page and look at the URL.\n"
            f"Greenhouse: `boards.greenhouse.io/{{slug}}`\n"
            f"Lever: `jobs.lever.co/{{slug}}`\n"
            f"Ashby: `jobs.ashbyhq.com/{{slug}}`"
        )

    data["companies"].append({
        "name": name, "ats": ats, "token": token,
        "category": "Other", "india_company": False, "global_ok": False,
    })
    COMPANIES_FILE.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )
    return (
        f"✅ *{name}* added!\n"
        f"_{ats}: {token} — {count} total jobs on board_\n\n"
        f"Will be scanned on next cycle.\n"
        f"Use `/remove {name}` to undo."
    )

def cmd_remove(args: list) -> str:
    if not args:
        return "Usage: `/remove <company name>`\ne.g. `/remove Databricks`"
    name = " ".join(args)
    data = yaml.safe_load(COMPANIES_FILE.read_text())
    before = len(data["companies"])
    data["companies"] = [c for c in data["companies"]
                         if c["name"].lower() != name.lower()]
    if len(data["companies"]) == before:
        matches = [c["name"] for c in data["companies"]
                   if name.lower() in c["name"].lower()]
        if matches:
            return (f"❌ No exact match for *{name}*\n"
                    f"Did you mean:\n" + "\n".join(f"  · {m}" for m in matches))
        return f"❌ *{name}* not found. Use `/list` to see companies."
    COMPANIES_FILE.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )
    return f"🗑️ *{name}* removed from tracking."

def cmd_filter_show(cfg: dict) -> str:
    years = ", ".join(cfg.get("grad_years") or []) or "All years"
    role  = "Internship only" if cfg.get("internship_only") else "Intern + New Grad"
    india = "ON" if cfg.get("india_only") else "OFF"
    tech  = "ON — SWE/data/quant only" if cfg.get("tech_only", True) else "OFF — all roles"
    return (
        "⚙️ *Active Filters*\n\n"
        f"🇮🇳 India-only: *{india}*\n"
        f"🎓 Grad years: *{years}*\n"
        f"💼 Role type: *{role}*\n"
        f"🔧 Tech-only: *{tech}*\n\n"
        "`/filter india on|off`\n"
        "`/filter year 2028` or `/filter year all`\n"
        "`/filter type intern|all`\n"
        "`/filter role tech|all`"
    )

def cmd_filter(args: list, cfg: dict) -> str:
    if not args:
        return cmd_filter_show(cfg)

    sub = args[0].lower()

    if sub == "india":
        val = (args[1].lower() if len(args) > 1 else "")
        if val == "on":
            cfg["india_only"] = True
            return "🇮🇳 India-only *ON* — only India/remote roles will alert."
        if val == "off":
            cfg["india_only"] = False
            return "🌐 India-only *OFF* — global roles from elite firms included."
        return "Usage: `/filter india on` or `/filter india off`"

    if sub == "year":
        import re
        val = " ".join(args[1:]) if len(args) > 1 else ""
        if val.lower() in ("all", "clear", "none", ""):
            cfg["grad_years"] = []
            return "🎓 Year filter *cleared* — all grad years will alert."
        years = [y.strip() for y in val.replace(" ", ",").split(",")
                 if re.match(r"20\d\d", y.strip())]
        if not years:
            return "❌ Invalid. Use `/filter year 2028` or `/filter year 2026,2028`"
        cfg["grad_years"] = years
        return (f"🎓 Grad year filter → *{', '.join(years)}*\n"
                f"Roles mentioning other years will be skipped.\n"
                f"Roles with no year are always shown.")

    if sub == "type":
        val = (args[1].lower() if len(args) > 1 else "")
        if val == "intern":
            cfg["internship_only"] = True
            return "💼 Role type → *Internships only*"
        if val in ("all", "any", "both"):
            cfg["internship_only"] = False
            return "💼 Role type → *All* (intern + new grad)"
        return "Usage: `/filter type intern` or `/filter type all`"

    if sub == "role":
        val = (args[1].lower() if len(args) > 1 else "")
        if val == "tech":
            cfg["tech_only"] = True
            return ("🔧 Role filter → *Tech only*\n"
                    "Marketing, legal, design, HR interns will be skipped.\n"
                    "Only SWE/SDE/data/quant/ML/product roles.")
        if val in ("all", "any"):
            cfg["tech_only"] = False
            return "🔧 Role filter → *All roles* (including non-tech)"
        return "Usage: `/filter role tech` or `/filter role all`"

    return cmd_filter_show(cfg)

async def cmd_rolesactive(client: httpx.AsyncClient, cfg: dict) -> None:
    """
    Scan all companies in parallel, filter active India-relevant intern roles,
    send results as Telegram messages. Also lists manual-check portals.
    """
    import re as _re

    # --- intern pattern (same as notifier.py) ---
    _INTERN_PATTERNS = [
        r"\bintern(?:ship|s)?\b", r"\bco-?op\b",
        r"\bsummer\s+(?:analyst|associate|program|intern|\d{4})\b",
        r"\bnew[-\s]?grad(?:uate)?\b", r"\bentry[-\s]?level\b",
        r"\bcampus\s+(?:hire|hiring|placement|program(?:me)?|graduate|opportunity|connect|recruitment)\b",
        r"\bfreshers?\b", r"\bfresh\s+graduate\b",
        r"\bgraduate\s+(?:engineer|trainee|program|analyst)\b",
        r"\btrainee\b", r"\bapprentice\b", r"\bearly[-\s]?career\b",
        r"\bassociate\s+(?:engineer|sde|swe|analyst|developer)\b",
        r"\bjunior\s+(?:engineer|developer|sde|swe|analyst)\b",
        r"\banalyst\s+(?:program|trainee)\b",
        r"\b20(?:2[4-9]|30)\s*batch\b", r"\bfte\s*20(?:2[4-9])\b",
        r"\bclass\s+of\s+20(?:2[4-9])\b",
    ]
    INTERN_RE = _re.compile("|".join(_INTERN_PATTERNS), _re.IGNORECASE)
    YEAR_RE   = _re.compile(r"\b(202[4-9]|20[3-9][0-9])\b")

    INDIA_KW = [
        "india","bangalore","bengaluru","hyderabad","delhi","mumbai","pune",
        "gurgaon","gurugram","noida","chennai","kolkata","ahmedabad","kochi",
        "remote","anywhere","worldwide","global","hybrid",
    ]

    india_only      = cfg.get("india_only", False)
    grad_years      = cfg.get("grad_years", [])
    internship_only = cfg.get("internship_only", False)
    tech_only       = cfg.get("tech_only", True)

    def is_intern(title):
        if internship_only:
            return bool(_re.search(r"\bintern(?:ship|s)?\b|\bco-?op\b|\btrainee\b|\bapprentice\b", title, _re.IGNORECASE))
        return bool(INTERN_RE.search(title))

    def year_ok(title):
        if not grad_years: return True
        found = YEAR_RE.findall(title)
        return (not found) or any(y in grad_years for y in found)

    def india_ok(location, company):
        if company.get("india_company"): return True
        if company.get("global_ok") and not india_only: return True
        loc = (location or "").lower().strip()
        if not loc: return True
        return any(kw in loc for kw in INDIA_KW if kw)

    FETCH_MAP = {
        "greenhouse": fetch_greenhouse,
        "lever": fetch_lever,
        "ashby": fetch_ashby,
        "smartrecruiters": fetch_smartrecruiters,
    }

    await tg_send(client, "⏳ Scanning all companies for active India roles… (30–60 sec)")

    companies = load_companies()
    found_by_company = {}

    # Parallel fetch
    async def scan_one(company):
        ats     = company.get("ats", "")
        token   = company.get("token", "")
        name    = company["name"]
        fetcher = FETCH_MAP.get(ats)
        if not fetcher or not token:
            return name, []
        jobs = await fetcher(client, token, name)
        hits = []
        for j in jobs:
            if not is_intern(j["title"]): continue
            if tech_only and not is_tech_role(j["title"]): continue
            if not year_ok(j["title"]): continue
            if not india_ok(j.get("location",""), company): continue
            hits.append(j)
        return name, hits

    tasks = [scan_one(c) for c in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total = 0
    for r in results:
        if isinstance(r, Exception): continue
        name, hits = r
        if hits:
            found_by_company[name] = hits
            total += len(hits)

    if total == 0:
        await tg_send(client, "✅ Scan complete — no active India intern roles found right now.\n\nThey may have filled recently or not posted yet.\nCheck `/rolesactive` again in a few hours.")
        return

    # Build response — split into chunks (Telegram 4096 char limit)
    EMOJI_MAP = {"HFT/Quant":"📈","AI/ML":"🤖","FAANG+":"🔥",
                 "Fintech/Banking":"💰","Fintech/India":"🇮🇳","Unicorns/India":"🦄",
                 "Consulting/IT":"💼","Semiconductor":"⚡","Cybersecurity":"🔐",
                 "Analytics/Data":"📊","Automotive":"🚗","Other":"✨"}

    # Map company name → category for emoji
    cat_map = {c["name"]: c.get("category","Other") for c in companies}

    header = f"🐐 *Active India Roles — {total} found*\n_(filtered: {'intern only' if internship_only else 'intern+newgrad'}, years: {', '.join(grad_years) or 'all'})_\n\n"
    chunks = [header]
    current = header

    for cname, jobs in sorted(found_by_company.items()):
        cat = cat_map.get(cname, "Other")
        em  = EMOJI_MAP.get(cat, "✨")
        block = f"{em} *{cname}* ({len(jobs)} role{'s' if len(jobs)>1 else ''})\n"
        for j in jobs[:6]:  # max 6 per company to keep messages reasonable
            loc  = j.get("location") or "location N/A"
            url  = j.get("url","#")
            line = f"  • [{j['title']}]({url}) — _{loc}_\n"
            block += line
        if len(jobs) > 6:
            block += f"  _+{len(jobs)-6} more roles_\n"
        block += "\n"

        if len(current) + len(block) > 3800:
            await tg_send(client, current.rstrip())
            await asyncio.sleep(0.5)
            current = block
        else:
            current += block

    if current.strip() and current != header:
        await tg_send(client, current.rstrip())

    # --- Manual portals section ---
    if Path("portals.yaml").exists():
        portals = yaml.safe_load(Path("portals.yaml").read_text()).get("portals", [])
        # Group by category
        by_cat: dict[str, list] = {}
        for p in portals:
            by_cat.setdefault(p.get("category","Other"), []).append(p)

        portal_msg = "🔗 *Also check manually* (no public API):\n\n"
        for cat, items in sorted(by_cat.items()):
            em = EMOJI_MAP.get(cat, "✨")
            portal_msg += f"{em} *{cat}:*\n"
            for p in items:
                portal_msg += f"  • [{p['name']}]({p['url']})\n"
            portal_msg += "\n"
            if len(portal_msg) > 3500:
                await tg_send(client, portal_msg.rstrip())
                await asyncio.sleep(0.4)
                portal_msg = ""

        if portal_msg.strip():
            await tg_send(client, portal_msg.rstrip())


def cmd_status() -> str:
    companies = load_companies()
    seen_size = SEEN_FILE.stat().st_size // 1024 if SEEN_FILE.exists() else 0
    cfg = load_config()
    years = ", ".join(cfg.get("grad_years") or []) or "all"
    return (
        f"📊 *InternshipGOAT Status*\n\n"
        f"Companies tracked: *{len(companies)}*\n"
        f"Seen-jobs cache: *{seen_size} KB*\n"
        f"Grad years filter: *{years}*\n"
        f"India-only: *{'ON' if cfg.get('india_only') else 'OFF'}*\n"
        f"Role type: *{'Intern only' if cfg.get('internship_only') else 'All'}*\n\n"
        f"_Listener: running ✅_\n"
        f"_Scanner: GitHub Actions every 10 min_"
    )

# ── Poll loop ─────────────────────────────────────────────────────────────────
async def poll_once(client: httpx.AsyncClient, cfg: dict) -> dict:
    """Fetch one batch of Telegram updates and handle any commands."""
    try:
        r = await client.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": cfg.get("telegram_offset", 0), "timeout": 0},
            timeout=10,
        )
        if r.status_code != 200:
            return cfg
        updates = r.json().get("result", [])
    except Exception as e:
        log.debug(f"getUpdates: {e}")
        return cfg

    for upd in updates:
        cfg["telegram_offset"] = upd["update_id"] + 1

        msg  = upd.get("message", {})
        text = (msg.get("text") or "").strip()
        cid  = str(msg.get("chat", {}).get("id", ""))

        if cid != TELEGRAM_CHAT_ID:
            continue
        if not text.startswith("/"):
            await tg_send(client, "Send /help for available commands.")
            continue

        parts = text.split()
        cmd   = parts[0].lower().split("@")[0]
        args  = parts[1:]

        log.info(f"CMD {cmd} {args}")

        try:
            if   cmd == "/help":         reply = cmd_help()
            elif cmd == "/list":         reply = cmd_list()
            elif cmd == "/add":          reply = await cmd_add(client, args)
            elif cmd == "/remove":       reply = cmd_remove(args)
            elif cmd == "/filter":       reply = cmd_filter(args, cfg)
            elif cmd == "/status":       reply = cmd_status()
            elif cmd == "/start":        reply = "👋 InternshipGOAT bot is running!\nSend /help to see commands."
            elif cmd == "/rolesactive":
                await cmd_rolesactive(client, cfg)
                reply = None   # already sent inside the function
            else:
                reply = f"Unknown command `{cmd}`\nSend /help for the list."

            if reply:
                await tg_send(client, reply)
        except Exception as e:
            log.exception(f"Error handling {cmd}")
            await tg_send(client, f"❌ Error running `{cmd}`:\n`{type(e).__name__}: {e}`")

        # Persist any config changes immediately
        save_config(cfg)

    return cfg

async def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        sys.exit(1)

    log.info("InternshipGOAT listener started — waiting for commands...")
    log.info(f"Bot token: ...{TELEGRAM_TOKEN[-10:]}")
    log.info(f"Chat ID: {TELEGRAM_CHAT_ID}")

    cfg = load_config()

    # Announce startup
    async with httpx.AsyncClient(
        headers={"User-Agent": "InternshipGOAT/2.0"},
        follow_redirects=True,
        timeout=15,
    ) as client:
        await tg_send(client, (
            "🐐 *InternshipGOAT listener is ON*\n\n"
            "Commands respond instantly now.\n"
            "Send /help to see all commands."
        ))

        log.info(f"Polling every {POLL_INTERVAL}s — Ctrl+C to stop")
        try:
            while True:
                cfg = await poll_once(client, cfg)
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass

    log.info("Listener stopped.")

def _start_health_server():
    port = int(os.environ.get("PORT", 8080))
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", port), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=_start_health_server, daemon=True).start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
