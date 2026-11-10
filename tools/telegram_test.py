#!/usr/bin/env python3
"""Telegram alert test (stdlib only).

Setup (once, by one team member):
  1. In Telegram talk to @BotFather -> /newbot -> copy the token.
  2. Send any message to your new bot (or add it to a group and write there).
  3. put TELEGRAM_BOT_TOKEN=<token> in .env (copy .env.example), or export it
  4. python tools/telegram_test.py --get-chat-id      # prints the chat id
  5. put TELEGRAM_CHAT_ID=<id> in .env
  6. python tools/telegram_test.py --send "LensGuard test: box_A RH 68% > 60%"
Token lives only in environment variables / .env (git-ignored).
"""
import argparse, json, os, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netpie import load_dotenv  # noqa: E402  (tiny .env reader, no MQTT needed)

API = "https://api.telegram.org/bot{token}/{method}"


def call(token, method, params=None):
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(API.format(token=token, method=method), data=data, timeout=15) as r:
        return json.load(r)


def alert_text(box, rh, thr=60.0, pred=None):
    s = f"⚠️ LensGuard: {box} ความชื้น {rh:.0f}% (เกิน {thr:.0f}%)"  # same text as ai/alerts.py
    if pred is not None:
        s += f"\nAI คาดว่าอีก 10 นาที ≈ {pred:.0f}%"
    return s + "\nพัดลมกำลังทำงาน / กรุณาตรวจสอบกล่อง"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--get-chat-id", action="store_true")
    ap.add_argument("--send", metavar="TEXT")
    ap.add_argument("--demo", action="store_true", help="send a sample alert message")
    ap.add_argument("--dry-run", action="store_true", help="print the message, do not call Telegram")
    a = ap.parse_args()
    load_dotenv()
    text = alert_text("box_A", 68, pred=72) if a.demo else a.send
    if a.dry_run and text:
        print(text); return
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or sys.exit("Set TELEGRAM_BOT_TOKEN")
    if a.get_chat_id:
        for u in call(token, "getUpdates").get("result", []):
            m = u.get("message") or {}
            print("chat_id =", m.get("chat", {}).get("id"), "| from:", m.get("chat", {}).get("first_name"))
        return
    if text:
        chat = os.environ.get("TELEGRAM_CHAT_ID") or sys.exit("Set TELEGRAM_CHAT_ID")
        print(call(token, "sendMessage", {"chat_id": chat, "text": text}))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
