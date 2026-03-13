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
CSRF_TOKEN       = "MxSv0NCdk3sBmukEmShugroUAUeMfi7l25ujSGFj"

TELEGRAM_TOKEN   = "7700287699:AAGEq7AeC6bcWcUK8g5rkz-oECmHRJFuWLQ"
TELEGRAM_CHAT_ID = "@BadmintonSlots"

SKIP_SLOT        = "05:00 AM - 06:00 AM"
POLL_EVERY       = 30
STATE_FILE       = Path.home() / "seen_slots.json"
GEN_STATE_FILE   = Path.home() / "seen_general_slots.json"

IDENTIFIER       = "7c72f826-5e81-4504-89a5-94dea8b8edd0"
RECZONE_ID       = 2
FACILITY_ID      = 2
SUBTYPES         = [1, 2, 3, 4, 5, 6, 7]
# ───────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "origin": "https://reczone.mcgm.gov.in",
    "referer": "https://reczone.mcgm.gov.in/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "x-csrf-token": CSRF_TOKEN,
    "x-requested-with": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def load_seen(filepath):
    if filepath.exists():
        try:
            return set(json.loads(filepath.read_text()))
        except Exception:
            pass
    return set()

def save_seen(filepath, seen):
    filepath.write_text(json.dumps(list(seen)))

def is_member_slot_available(s):
    """
    A member slot is available if:
    - isBooked is not True  (booked slots have isBooked=True and no id/slot/amount)
    - AND it has an actual bookable slot (has 'id' and 'slot' fields)
    """
    return not s.get("isBooked", False) and s.get("id") and s.get("slot")


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


# ── Member API ─────────────────────────────────────────────────────────────────

def fetch_member_slots():
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
            print(f"[{ts()}] ⚠️  Member API code {data.get('code')}")
            return None
        return data.get("data", [])
    except Exception as e:
        print(f"[{ts()}] ❌ Member fetch error: {e}")
        return None


# ── General API — Step 1: dates per subtype ───────────────────────────────────

def fetch_dates_for_subtype(subtype_id):
    url = (
        f"https://reczone-admin.mcgm.gov.in/api/v1/general-slot-bookings/"
        f"reczones/{RECZONE_ID}/facilities/{FACILITY_ID}/"
        f"facility-subtypes/{subtype_id}/dates?locale=en"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 404:
            print(f"[{ts()}] ⚠️  Subtype {subtype_id} dates not found, skipping.")
            return []
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            return []
        dates = []
        for month_obj in data.get("data", []):
            for d in month_obj.get("dates", []):
                if not d.get("isClosed") and not d.get("isBooked"):
                    dates.append(d["date"])
        return dates
    except Exception as e:
        print(f"[{ts()}] ❌ Dates fetch error (subtype {subtype_id}): {e}")
        return []


# ── General API — Step 2: timeslots per subtype + date ────────────────────────

def fetch_timeslots(subtype_id, date):
    url = (
        f"https://reczone-admin.mcgm.gov.in/api/v1/general-slot-bookings/"
        f"reczones/{RECZONE_ID}/facilities/{FACILITY_ID}/"
        f"facility-subtypes/{subtype_id}/timeslots"
    )
    params = {
        "identifier": IDENTIFIER,
        "date_of_booking": date,
        "locale": "en",
    }
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 404:
            print(f"[{ts()}] ⚠️  Subtype {subtype_id} / {date} not found, skipping.")
            return []
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            return []
        return data.get("data", [])
    except Exception as e:
        print(f"[{ts()}] ❌ Timeslots fetch error (subtype {subtype_id}, {date}): {e}")
        return []


# ── General API — full scan ───────────────────────────────────────────────────

def fetch_all_general_slots():
    available = []
    for subtype_id in SUBTYPES:
        dates = fetch_dates_for_subtype(subtype_id)
        print(f"[{ts()}] Subtype {subtype_id} → {len(dates)} open date(s)")
        for date in dates:
            timeslots = fetch_timeslots(subtype_id, date)
            for slot in timeslots:
                if slot.get("is_booked"):
                    continue
                if slot.get("is_busy"):
                    continue
                if slot.get("is_reserved"):
                    continue
                if slot.get("slot") == SKIP_SLOT:
                    continue
                available.append({
                    "id":      f"gen_{subtype_id}_{date}_{slot.get('id')}",
                    "subtype": subtype_id,
                    "date":    date,
                    "slot":    slot.get("slot", "?"),
                })
    return available


# ── Manual fetch — ALL slots, ignores seen files ──────────────────────────────

def fetch_all_slots() -> str:
    lines = []

    # ── member slots — grouped by date ────────────────────────────────────────
    member_slots = fetch_member_slots()
    member_lines = []

    if member_slots:
        filtered = [
            s for s in member_slots
            if is_member_slot_available(s) and s.get("slot") != SKIP_SLOT
        ]
        sorted_member = sorted(
            filtered,
            key=lambda x: x.get("dateOfBooking", {}).get("date", "")  # sort by YYYY-MM-DD for correct order
        )
        current_date = None
        for s in sorted_member:
            date_str = s.get("dateOfBooking", {}).get("formatted", "?")
            if date_str != current_date:
                current_date = date_str
                member_lines.append(f"\n📅 <b>{date_str}</b>")
            member_lines.append(
                f"  • {s.get('slot', '?')}  |  "
                f"{s.get('facilitySubtype', {}).get('name', '?')}  |  "
                f"₹{s.get('amount', '?')}"
            )

    if member_lines:
        lines.append(f"👤 <b>Member Slots ({len([l for l in member_lines if l.startswith('  •')])}):</b>")
        lines.extend(member_lines)
    else:
        lines.append("👤 <b>Member Slots:</b> None available")

    lines.append("")

    # ── general slots — grouped by date ───────────────────────────────────────
    general_slots = fetch_all_general_slots()

    if general_slots:
        sorted_slots = sorted(general_slots, key=lambda x: (x["date"], x["subtype"]))
        current_date = None
        general_lines = []

        for s in sorted_slots:
            if s["date"] != current_date:
                current_date = s["date"]
                general_lines.append(f"\n📅 <b>{s['date']}</b>")
            general_lines.append(f"  • {s['slot']}  |  Court {s['subtype']}")

        lines.append(f"🏸 <b>General Slots ({len(general_slots)}):</b>")
        lines.extend(general_lines)
    else:
        lines.append("🏸 <b>General Slots:</b> None available")

    lines.append("")
    lines.append("👉 https://reczone.mcgm.gov.in")

    return "\n".join(lines)


# ── Scheduled check — alerts only on NEW slots ────────────────────────────────

def check_slots():
    print(f"[{ts()}] Polling APIs…")

    # ── member slots ───────────────────────────────────────────────────────────
    member_slots = fetch_member_slots()
    seen_member  = load_seen(STATE_FILE)
    new_member   = []

    if member_slots:
        for s in member_slots:
            # Use same availability check: must have id+slot and not be booked
            if not is_member_slot_available(s):
                continue
            slot_id   = s.get("id")
            slot_time = s.get("slot", "")
            if slot_time == SKIP_SLOT:
                seen_member.add(slot_id)
                continue
            if slot_id not in seen_member:
                new_member.append({
                    "id":     slot_id,
                    "date":   s.get("dateOfBooking", {}).get("formatted", "?"),
                    "slot":   slot_time,
                    "court":  s.get("facilitySubtype", {}).get("name", "?"),
                    "amount": s.get("amount", "?"),
                })
        save_seen(STATE_FILE, seen_member | {a["id"] for a in new_member})

    # ── general slots ──────────────────────────────────────────────────────────
    general_slots = fetch_all_general_slots()
    seen_general  = load_seen(GEN_STATE_FILE)
    new_general   = [s for s in general_slots if s["id"] not in seen_general]
    save_seen(GEN_STATE_FILE, seen_general | {s["id"] for s in new_general})

    # ── nothing new ────────────────────────────────────────────────────────────
    if not new_member and not new_general:
        print(f"[{ts()}] No new slots found.")
        return

    # ── build alert message ────────────────────────────────────────────────────
    lines = ["🔔 <b>New RecZone Slot(s) Found!</b>\n"]

    if new_member:
        lines.append(f"👤 <b>New Member Slot(s) — {len(new_member)}:</b>")
        current_date = None
        for a in sorted(new_member, key=lambda x: x["date"]):
            if a["date"] != current_date:
                current_date = a["date"]
                lines.append(f"\n📅 <b>{a['date']}</b>")
            lines.append(f"  • {a['slot']}  |  {a['court']}  |  ₹{a['amount']}")
        lines.append("")

    if new_general:
        lines.append(f"🏸 <b>New General Slot(s) — {len(new_general)}:</b>")
        current_date = None
        for s in sorted(new_general, key=lambda x: (x["date"], x["subtype"])):
            if s["date"] != current_date:
                current_date = s["date"]
                lines.append(f"\n📅 <b>{s['date']}</b>")
            lines.append(f"  • {s['slot']}  |  Court {s['subtype']}")
        lines.append("")

    lines.append("👉 https://reczone.mcgm.gov.in")

    msg = "\n".join(lines)
    print(f"[{ts()}] 🔔 {len(new_member)} member + {len(new_general)} general new slots.")
    send_telegram(msg)


# ── Scheduler thread ───────────────────────────────────────────────────────────

def run_scheduler():
    check_slots()
    schedule.every(POLL_EVERY).minutes.do(check_slots)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Telegram listener ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.message.from_user.first_name or "Someone"
    print(f"[{ts()}] 📩 Message from {user} — triggering full fetch.")

    await update.message.reply_text("🔄 Fetching all slots…")

    result = await asyncio.get_event_loop().run_in_executor(None, fetch_all_slots)

    if len(result) <= 4096:
        await update.message.reply_text(result, parse_mode="HTML")
    else:
        for i in range(0, len(result), 4096):
            await update.message.reply_text(result[i:i+4096], parse_mode="HTML")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  MCGM RecZone Slot Monitor — Telegram Edition")
    print(f"  Member  : {MEMBER_ID}")
    print(f"  Polling : every {POLL_EVERY} minutes")
    print(f"  Skipping: {SKIP_SLOT}")
    print(f"  Subtypes: {SUBTYPES}")
    print("=" * 60)

    threading.Thread(target=run_scheduler, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print(f"\n[{ts()}] Bot listening. Press Ctrl+C to stop.\n")
    app.run_polling()
