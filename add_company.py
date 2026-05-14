#!/usr/bin/env python3
"""
InternshipGOAT — Add Company
Validates an ATS token then appends the company to companies.yaml.

Usage:
  python add_company.py
  python add_company.py --name "Palantir" --ats lever --token palantir --category FAANG+
"""

import sys
import argparse
import asyncio
import httpx
import yaml
from pathlib import Path

COMPANIES_FILE = Path("companies.yaml")

VALID_ATS = ["greenhouse", "lever", "ashby", "smartrecruiters"]

CATEGORIES = [
    "FAANG+", "AI/ML", "HFT/Quant", "Fintech/Banking", "Fintech/India",
    "Unicorns/India", "Consulting/IT", "Semiconductor", "Cybersecurity",
    "Analytics/Data", "Automotive", "Other",
]

# ─── Token verification ───────────────────────────────────────────────────────

async def verify(ats: str, token: str) -> tuple[bool, int]:
    """Returns (ok, job_count). job_count may be 0 if API hides total."""
    urls = {
        "greenhouse":     f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        "lever":          f"https://api.lever.co/v0/postings/{token}?mode=json&limit=5",
        "ashby":          f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=5",
    }
    url = urls.get(ats)
    if not url:
        return False, 0
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return False, 0
            data = r.json()
            counts = {
                "greenhouse":     len(data.get("jobs", [])),
                "lever":          len(data) if isinstance(data, list) else 0,
                "ashby":          len(data.get("jobs", [])),
                "smartrecruiters": data.get("totalFound", 0),
            }
            return True, counts.get(ats, 0)
    except Exception as e:
        print(f"  Error: {e}")
        return False, 0

# ─── ATS token hints ──────────────────────────────────────────────────────────

ATS_HELP = {
    "greenhouse":     "The slug in  boards.greenhouse.io/{SLUG}  e.g. 'stripe', 'databricks'",
    "lever":          "The slug in  jobs.lever.co/{SLUG}          e.g. 'razorpay', 'netflix'",
    "ashby":          "The slug in  jobs.ashbyhq.com/{SLUG}       e.g. 'openai', 'figma'",
    "smartrecruiters": "The company identifier in SmartRecruiters  e.g. 'SAP', 'BoschGroup'",
}

# ─── Main ─────────────────────────────────────────────────────────────────────

def interactive() -> dict:
    print("\n🐐  InternshipGOAT — Add Company\n")

    name = input("Company name: ").strip()
    if not name:
        sys.exit("Name required.")

    print(f"\nATS options: {', '.join(VALID_ATS)}")
    ats = input("ATS type: ").strip().lower()
    if ats not in VALID_ATS:
        sys.exit(f"Invalid ATS. Choose from: {', '.join(VALID_ATS)}")

    print(f"  Hint: {ATS_HELP[ats]}")
    token = input("Token/slug: ").strip()
    if not token:
        sys.exit("Token required.")

    print("\nVerifying token…", end=" ", flush=True)
    ok, count = asyncio.run(verify(ats, token))
    if not ok:
        sys.exit(
            f"\n❌  Could not connect to {ats} with token '{token}'.\n"
            "    Double-check the slug and try again."
        )
    print(f"✅  {count} jobs found on board.")

    print(f"\nCategories:\n  " + "\n  ".join(f"{i+1}. {c}" for i,c in enumerate(CATEGORIES)))
    cat_input = input("Category (name or number): ").strip()
    category = "Other"
    if cat_input.isdigit():
        idx = int(cat_input) - 1
        category = CATEGORIES[idx] if 0 <= idx < len(CATEGORIES) else "Other"
    elif cat_input in CATEGORIES:
        category = cat_input

    india = input("\nIndia-based company? (y/n, default n): ").strip().lower() == "y"
    global_ok = False
    if not india:
        global_ok = input("Alert for ALL locations (e.g. elite HFT firm)? (y/n, default n): ").strip().lower() == "y"

    return {
        "name":         name,
        "ats":          ats,
        "token":        token,
        "category":     category,
        "india_company": india,
        "global_ok":    global_ok,
    }

def from_args(args) -> dict:
    if not args.name or not args.ats or not args.token:
        sys.exit("--name, --ats, and --token are required in non-interactive mode.")
    if args.ats not in VALID_ATS:
        sys.exit(f"Invalid --ats. Choose from: {', '.join(VALID_ATS)}")

    print(f"Verifying {args.ats} token '{args.token}'…", end=" ", flush=True)
    ok, count = asyncio.run(verify(args.ats, args.token))
    if not ok:
        sys.exit(f"\n❌  Token '{args.token}' not valid for {args.ats}.")
    print(f"✅  {count} jobs found.")

    return {
        "name":          args.name,
        "ats":           args.ats,
        "token":         args.token,
        "category":      args.category or "Other",
        "india_company": args.india,
        "global_ok":     args.global_ok,
    }

def add_to_yaml(entry: dict):
    data = yaml.safe_load(COMPANIES_FILE.read_text())

    existing = [c["name"].lower() for c in data["companies"]]
    if entry["name"].lower() in existing:
        sys.exit(f"⚠️   '{entry['name']}' already exists in companies.yaml")

    data["companies"].append(entry)
    COMPANIES_FILE.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )
    print(f"\n✅  '{entry['name']}' added to companies.yaml")
    print("    Run  python notifier.py  to include it in the next scan.")


def main():
    parser = argparse.ArgumentParser(description="Add a company to InternshipGOAT")
    parser.add_argument("--name",       help="Company name")
    parser.add_argument("--ats",        help="ATS type (greenhouse|lever|ashby|smartrecruiters)")
    parser.add_argument("--token",      help="ATS board token/slug")
    parser.add_argument("--category",   help="Category label", default="Other")
    parser.add_argument("--india",      action="store_true", help="Mark as India-based company")
    parser.add_argument("--global-ok",  dest="global_ok", action="store_true",
                        help="Alert for all global locations")
    args = parser.parse_args()

    if args.name:
        entry = from_args(args)
    else:
        entry = interactive()

    add_to_yaml(entry)


if __name__ == "__main__":
    main()
