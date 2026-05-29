"""
bunny_agent.py — Bunny Button session logic for Mantis Pro
----------------------------------------------------------
Uses Gmail OAuth API for email alerts (works on Railway — HTTPS only).

Required Railway env vars:
    BUNNY_SESSION_COOKIE   — session cookie from bunnybutton.xyz
    GMAIL_CLIENT_ID        — from Google Cloud Console
    GMAIL_CLIENT_SECRET    — from Google Cloud Console
    GMAIL_REFRESH_TOKEN    — from OAuth Playground
"""

import os
import json
import base64
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bunny_button import BunnyButton, RateLimitError, AuthError

ALERT_TO = "smolobah21@gmail.com"
ALERT_FROM = "smolobah21@gmail.com"


def separator(label=""):
    print(f"\n{'─' * 10} {label} {'─' * 10}")


def get_access_token():
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise Exception("Missing GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, or GMAIL_REFRESH_TOKEN env vars")

    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        return result["access_token"]


def send_email_alert(subject: str, body: str):
    try:
        access_token = get_access_token()

        html_body = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;background:#0f1a0f;border-radius:12px;border:1px solid #2a5a2a;">
            <div style="text-align:center;margin-bottom:20px;">
                <span style="font-size:36px;">🥕</span>
                <h2 style="color:#7fff7f;margin:8px 0;font-size:20px;">Mantis Pro Alert</h2>
            </div>
            <div style="background:#1a2e1a;border-radius:8px;padding:16px;margin-bottom:16px;">
                <p style="color:#ffffff;font-size:16px;margin:0;line-height:1.6;">{body.replace(chr(10), '<br>')}</p>
            </div>
            <div style="text-align:center;">
                <a href="https://bunnybutton.xyz" style="background:#4caf50;color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:bold;font-size:14px;">Go Play Now →</a>
            </div>
            <p style="color:#555;font-size:11px;text-align:center;margin-top:16px;">Mantis Pro · Abstract Chain Agent</p>
        </div>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = ALERT_FROM
        msg["To"] = ALERT_TO
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

        payload = json.dumps({"raw": raw}).encode("utf-8")

        req = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"  [Email] ✅ Alert sent to {ALERT_TO} (id: {result.get('id', '?')})")

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"  [Email] ❌ Gmail API error {e.code}: {error_body}")
    except Exception as e:
        print(f"  [Email] ❌ Failed to send: {e}")


def bunny_session():
    separator("BUNNY BUTTON SESSION")
    cookie = os.environ.get("BUNNY_SESSION_COOKIE")
    bb = BunnyButton(session_cookie=cookie)

    # ── PUBLIC: Leaderboard snapshot ──────────────────────────────────────
    try:
        separator("LEADERBOARD (Top 10)")
        print(bb.leaderboard_summary(limit=10))
    except RateLimitError as e:
        print(f"Rate limited on leaderboard, skipping. Retry in {e.retry_after}s")
    except Exception as e:
        print(f"Leaderboard error: {e}")

    # ── PUBLIC: Presale status ─────────────────────────────────────────────
    try:
        separator("PRESALE STATUS")
        presale = bb.get_presale_status()
        print(f"  Active:              {presale.get('active')}")
        print(f"  Total raised (ETH):  {presale.get('totalRaisedEth', '?')}")
        print(f"  Remaining alloc:     {presale.get('remainingAllocation', '?'):,.0f} CARROT")
        print(f"  Status:              {presale.get('statusLabel', '?')}")
    except Exception as e:
        print(f"Presale error: {e}")

    # ── SESSION-GATED checks ───────────────────────────────────────────────
    if not cookie:
        separator("PLAYER CHECKS SKIPPED")
        print("  No BUNNY_SESSION_COOKIE set. Add it to Railway env vars for full tracking.")
        separator("SESSION COMPLETE")
        return

    # ── Energy status + email alert ────────────────────────────────────────
    energy_full = False
    try:
        separator("ENERGY")
        energy = bb.energy_status()
        current = energy['energyCurrent']
        maximum = energy['energyMax']
        pct = energy['percentFull']
        print(f"  {current:.0f} / {maximum:.0f}  ({pct}% full)")

        if energy["isFull"]:
            print("  ⚡ Energy is FULL — time to go play!")
            energy_full = True
            send_email_alert(
                subject="⚡ Bunny Button — Your Energy is Full!",
                body=(
                    f"Your energy is at {current:.0f}/{maximum:.0f} (100% full).\n\n"
                    f"Head to Bunny Button now and press the button to steal carrots.\n\n"
                    f"Don't wait — energy doesn't overflow, it just sits there full."
                )
            )
        else:
            h = energy["hoursToFull"]
            if h is not None:
                print(f"  Full in ~{h:.1f} hours")
    except (AuthError, Exception) as e:
        print(f"Energy check error: {e}")

    # ── Farm cap warning + email alert ─────────────────────────────────────
    try:
        separator("CARROT FARM")
        farm = bb.farm_cap_warning()
        cps = farm.get("carrotsPerSecond", 0)
        print(f"  Yield:       {cps:.4f} carrots/sec  ({cps * 3600:.1f}/hr)")

        if farm.get("capWarning"):
            print("  ⚠️  FARM CAP WARNING — less than 1hr until 8h offline cap hits!")
            print("  Log in to the game and claim your farm carrots now.")
            if not energy_full:
                send_email_alert(
                    subject="⚠️ Bunny Button — Farm Cap Almost Full!",
                    body=(
                        f"Your carrot farm is almost at the 8-hour offline cap.\n\n"
                        f"Farm yield: {cps * 3600:.1f} carrots/hr\n\n"
                        f"Log in now and claim your offline carrots before they stop accumulating."
                    )
                )
        else:
            h = farm.get("hoursToCapFull")
            if h is not None:
                print(f"  Cap hits in: ~{h:.1f} hours")
    except Exception as e:
        print(f"Farm check error: {e}")

    # ── Effective stats ────────────────────────────────────────────────────
    try:
        separator("EFFECTIVE STATS")
        stats = bb.effective_stats()
        print(f"  Breed:        {stats['breed']}  |  Class: {stats['bunnyClass']}")
        print(f"  Steal:        {stats['baseSteal']}x base  +{stats['gearStealBonus']}x gear  = {stats['effectiveSteal']}x")
        print(f"  Energy max:   {stats['baseEnergyMax']} base  +{stats['gearEnergyBonus']} gear  = {stats['effectiveEnergyMax']}")
        print(f"  Regen/hr:     {stats['baseRegen']} base  +{stats['gearRegenBonus']} gear  = {stats['effectiveRegen']}")
        print(f"  Rank:         #{stats['rank']}")
        print(f"  Total earned: {stats['totalCarrotsEarned']:,.0f} carrots")
        print(f"  Balance:      {stats['carrotBalance']:,.0f} carrots")
    except Exception as e:
        print(f"Stats error: {e}")

    # ── Quests ─────────────────────────────────────────────────────────────
    try:
        separator("QUESTS")
        quests = bb.get_quests()
        quest_list = quests.get("quests", []) if isinstance(quests, dict) else []
        done = sum(1 for q in quest_list if q.get("status") == "complete")
        pending = [q for q in quest_list if q.get("status") not in ("complete",)]
        print(f"  Completed: {done} / {len(quest_list)}")
        if pending:
            next_q = pending[0]
            print(f"  Next:      {next_q.get('name', '?')}  ({next_q.get('status', '?')})")
    except Exception as e:
        print(f"Quests error: {e}")

    # ── Streaks ────────────────────────────────────────────────────────────
    try:
        separator("STREAKS")
        streaks = bb.get_streaks()
        streak_len = streaks.get("streakLength") or streaks.get("currentStreak", 0)
        print(f"  Current streak: {streak_len} days")
    except Exception as e:
        print(f"Streaks error: {e}")

    separator("BUNNY SESSION COMPLETE")
