
#!/usr/bin/env python3
"""
MCGM RecZone — Slot Monitor
Polls every 30 minutes. Sends a Telegram message when a NEW slot
appears that is NOT the 05:00 AM - 06:00 AM slot.
"""

import json
import time
import requests
import schedule
from datetime import datetime
from pathlib import Path

# ── ⚙️  Config — fill these in ─────────────────────────────────────────────────
MEMBER_ID        = "ADH11A4665"
CSRF_TOKEN       = "cJACSPoXtnKMRK0t4JTB6aUhdW1m0nwWa7PmMODV"  # update when expired

TELEGRAM_TOKEN   = "7700287699:AAGEq7AeC6bcWcUK8g5rkz-oECmHRJFuWLQ"    # from @BotFather  (step 1 below)
TELEGRAM_CHAT_ID = "1354012677"      # from @userinfobot (step 2 below)

SKIP_SLOT   = "05:00 AM - 06:00 AM"   # never alert on this slot
POLL_EVERY  = 15                       # minutes
STATE_FILE  = Path("seen_slots.json")
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


# ── Telegram ───────────────────────────────────────────────────────────────────

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


# ── API fetch ──────────────────────────────────────────────────────────────────

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


# ── Core check ─────────────────────────────────────────────────────────────────

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

        # Mark 5-6 AM slots as seen without alerting
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
        print(f"[{ts()}] 🔔 {len(new_alerts)} new slot(s) found, sending Telegram alert.")
        send_telegram(msg)
    else:
        print(f"[{ts()}] No new slots. Seen: {len(seen)} slots.")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  MCGM RecZone Slot Monitor — Telegram Edition")
    print(f"  Member  : {MEMBER_ID}")
    print(f"  Polling : every {POLL_EVERY} minutes")
    print(f"  Skipping: {SKIP_SLOT}")
    print("=" * 60)

    if "YOUR_BOT" in TELEGRAM_TOKEN or "YOUR_CHAT" in TELEGRAM_CHAT_ID:
        print("\n❌ ERROR: Please set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in the script first!\n")
        exit(1)

    # Send a startup confirmation to Telegram
    send_telegram(
        f"✅ <b>RecZone Monitor started!</b>\n"
        f"Checking every {POLL_EVERY} mins for member <code>{MEMBER_ID}</code>.\n"
        f"You'll be notified of any new non-5AM slots."
    )

    check_slots()  # run immediately on start

    schedule.every(POLL_EVERY).minutes.do(check_slots)
    print(f"\n[{ts()}] Scheduler running. Press Ctrl+C to stop.\n")

    while True:
        schedule.run_pending()
        time.sleep(30)
