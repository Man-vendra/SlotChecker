#!/usr/bin/env python3
"""
MCGM RecZone — Slot Monitor
Polls every 30 minutes. Sends a Telegram message when a NEW slot
appears that is NOT the 05:00 AM - 06:00 AM slot.
Any message in the group triggers a fresh fetch of ALL available slots.
"""

import json
import asyncio
import time
import threading
import requests
import schedule
from datetime import datetime
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# ── ⚙️  Config ─────────────────────────────────────────────────────────────────
MEMBER_ID        = "ADH11A4665"
CSRF_TOKEN       = "cJACSPoXtnKMRK0t4JTB6aUhdW1m0nwWa7PmMODV"

TELEGRAM_TOKEN   = "7700287699:AAGEq7AeC6bcWcUK8g5rkz-oECmHRJFuWLQ"
TELEGRAM_CHAT_ID = "@BadmintonSlots2"

SKIP_SLOT  = "05:00 AM - 06:00 AM"
POLL_EVERY = 30
STATE_FILE = Path("seen_slots.json")
# ───────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "origin": "https://reczone.mcgm.gov.in",
    "referer": "https://reczone.mcgm.gov.in/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
    "x-csrf-token": CSRF_TOKEN,
    "x-requested-with": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    return set()

def save_seen(seen: set):
    STATE_FILE.write_text(json.dumps(list(seen)))


# ── Telegram push ──────────────────────────────────────────────────────────────

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[{ts()}] ✅ Telegram message sent.")
        else:
            print(f"[{ts()}] ⚠️  Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[{ts()}] ❌ Telegram send failed: {e}")


# ── API call ───────────────────────────────────────────────────────────────────

def fetch_slots() -> list | None:
    url = (
        f"https://reczone-admin.mcgm.gov.in/api/v1/member-slot-bookings/"
        f"members/{MEMBER_ID}/process-preferred-slots?locale=en"
    )
    files = {
        "no_slot_preference":    (None, "1"),
        "no_subtype_preference": (None, "1"),
    }
    try:
        resp = requests.post(url, headers=HEADERS, files=files, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            print(f"[{ts()}] ⚠️  API code {data.get('code')}")
            return None
        return data.get("data", [])
    except Exception as e:
        print(f"[{ts()}] ❌ Fetch error: {e}")
        return None


# ── Manual fetch — ignores seen_slots.json, returns ALL open slots ─────────────

def fetch_all_slots() -> str:
    slots = fetch_slots()

    if slots is None:
        return "❌ <b>Fetch failed.</b> Could not reach the RecZone API."

    available = []
    for s in slots:
        if s.get("isBooked"):
            continue
        slot_time = s.get("slot", "")
        if slot_time == SKIP_SLOT:
            continue

        available.append({
            "date":   s.get("dateOfBooking", {}).get("formatted", "?"),
            "slot":   slot_time,
            "court":  s.get("facilitySubtype", {}).get("name", "?"),
            "amount": s.get("amount", "?"),
        })

    if not available:
        return "🔍 <b>No slots available right now</b> (excluding 5–6 AM)."

    lines = "\n".join(
        f"• {a['date']}  |  {a['slot']}  |  {a['court']}  |  ₹{a['amount']}"
        for a in available
    )
    return (
        f"🏸 <b>{len(available)} slot(s) available:</b>\n\n"
        f"{lines}\n\n"
        f"👉 https://reczone.mcgm.gov.in"
    )


# ── Scheduled check — alerts only on NEW slots ─────────────────────────────────

def check_slots():
    print(f"[{ts()}] Polling API…")
    slots = fetch_slots()
    if slots is None:
        return

    seen = load_seen()
    new_alerts = []

    for s in slots:
        if s.get("isBooked"):
            continue

        slot_id   = s.get("id")
        slot_time = s.get("slot", "")
        date_str  = s.get("dateOfBooking", {}).get("formatted", "?")
        court     = s.get("facilitySubtype", {}).get("name", "?")
        amount    = s.get("amount", "?")

        if slot_time == SKIP_SLOT:
            seen.add(slot_id)
            continue

        if slot_id not in seen:
            new_alerts.append({
                "id": slot_id, "date": date_str,
                "slot": slot_time, "court": court, "amount": amount,
            })

    save_seen(seen | {a["id"] for a in new_alerts})

    if new_alerts:
        lines = "\n".join(
            f"• {a['date']}  |  {a['slot']}  |  {a['court']}  |  ₹{a['amount']}"
            for a in new_alerts
        )
        msg = (
            f"🏸 <b>{len(new_alerts)} New RecZone Slot(s)!</b>\n\n"
            f"{lines}\n\n"
            f"👉 https://reczone.mcgm.gov.in"
        )
        print(f"[{ts()}] 🔔 {len(new_alerts)} new slot(s) found, sending alert.")
        send_telegram(msg)
    else:
        print(f"[{ts()}] No new slots. Seen: {len(seen)} slots.")


# ── Scheduler thread ───────────────────────────────────────────────────────────

def run_scheduler():
    check_slots()  # immediate check on startup
    schedule.every(POLL_EVERY).minutes.do(check_slots)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Telegram listener — ANY message triggers fresh fetch ──────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.message.from_user.first_name or "Someone"
    print(f"[{ts()}] 📩 Message from {user} — triggering fetch.")

    await update.message.reply_text("🔄 Fetching slots…")

    result = await asyncio.get_event_loop().run_in_executor(None, fetch_all_slots)
    await update.message.reply_text(result, parse_mode="HTML")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  MCGM RecZone Slot Monitor — Telegram Edition")
    print(f"  Member  : {MEMBER_ID}")
    print(f"  Polling : every {POLL_EVERY} minutes")
    print(f"  Skipping: {SKIP_SLOT}")
    print("=" * 60)

    # Send startup confirmation
    send_telegram(
        f"✅ <b>RecZone Monitor started!</b>\n"
        f"Checking every {POLL_EVERY} mins for member <code>{MEMBER_ID}</code>.\n"
        f"Send any message in this group to fetch all current slots."
    )

    # Run scheduler in background thread
    threading.Thread(target=run_scheduler, daemon=True).start()

    # Run Telegram bot listener (blocking — runs in main thread)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"\n[{ts()}] Bot listening for messages. Press Ctrl+C to stop.\n")
    app.run_polling()
