#!/usr/bin/env python3
"""
Run this ONCE to find your Telegram chat ID.
Steps:
  1. Open Telegram, search for your bot, send it any message (e.g. "hi")
  2. Run:  python get_chat_id.py
  3. Copy the Chat ID printed and put it in your .env as TELEGRAM_CHAT_ID
"""
import httpx, os, sys

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not token:
    token = input("Paste your TELEGRAM_BOT_TOKEN: ").strip()

r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
if r.status_code != 200:
    sys.exit(f"Error {r.status_code}: {r.text}")

updates = r.json().get("result", [])
if not updates:
    print("No messages found.")
    print("→ Open Telegram, send any message to your bot, then run this script again.")
    sys.exit(0)

print("\n── Chats found ──────────────────────────────")
seen = set()
for u in updates:
    msg  = u.get("message") or u.get("channel_post") or {}
    chat = msg.get("chat", {})
    cid  = chat.get("id")
    if not cid or cid in seen:
        continue
    seen.add(cid)
    name = chat.get("first_name","") or chat.get("title","")
    user = chat.get("username","")
    print(f"  Chat ID : {cid}")
    print(f"  Name    : {name} (@{user})")
    print(f"  Type    : {chat.get('type','')}")
    print()

print("Copy your Chat ID and add it to .env as TELEGRAM_CHAT_ID=<id>")
