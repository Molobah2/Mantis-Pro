"""
moody_agent.py — Moody Madness assistant reminder for Mantis Pro
----------------------------------------------------------------
Tracks when you last woke your AI assistants and emails you when
they're ready to drive again (every 12 hours).

How to use:
  After waking your assistants, hit this URL in your browser:
  https://mantis-pro-production.up.railway.app/moody/woke

  Mantis records the timestamp and emails you in exactly 12 hours.

Required Railway env vars (same as Bunny Button):
  GMAIL_CLIENT_ID
  GMAIL_CLIENT_SECRET
  GMAIL_REFRESH_TOKEN
"""

import os
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

ALERT_TO = "smolobah21@gmail.com"
ALERT_FROM = "smolobah21@gmail.com"
MOODY_URL = "https://moodymadness.com/ai-assistants"
SESSION_HOURS = 12

# ── IN-MEMORY STATE ────────────────────────────────────────────────────────
_moody_state = {
    "last_woke": None,       # datetime UTC when user last woke assistants
    "reminder_sent": False,  # did we already send the 12hr reminder
    "assistant_count": 3,    # number of assistants
}


def record_woke():
    """Call this when the user hits /moody/woke"""
    now = datetime.now(timezone.utc)
    _moody_state["last_woke"] = now
    _moody_state["reminder_sent"] = False
    print(f"  [Moody] Assistants woken at {now.strftime('%H:%M UTC')} — reminder scheduled for 12hrs later")
    return now


def get_status():
    """Returns current assistant status as a dict"""
    last = _moody_state["last_woke"]
    if not last:
        return {
            "status": "unknown",
            "message": "No wake recorded yet. Hit /moody/woke after waking your assistants.",
            "next_wake": None,
            "hours_remaining": None,
        }

    now = datetime.now(timezone.utc)
    next_wake = last + timedelta(hours=SESSION_HOURS)
    hours_remaining = max(0, (next_wake - now).total_seconds() / 3600)

    if now >= next_wake:
        status = "ready"
        message = "Assistants are ready to drive! Go wake them now."
    else:
        status = "driving"
        message = f"Assistants are driving. Ready in {hours_remaining:.1f} hours."

    return {
        "status": status,
        "message": message,
        "last_woke": last.isoformat(),
        "next_wake": next_wake.isoformat(),
        "hours_remaining": round(hours_remaining, 2),
    }


# ── GMAIL ──────────────────────────────────────────────────────────────────

def get_access_token():
    payload = urllib.parse.urlencode({
        "client_id": os.environ.get("GMAIL_CLIENT_ID", ""),
        "client_secret": os.environ.get("GMAIL_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN", ""),
        "grant_type": "refresh_token"
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def send_email(subject, html, plain):
    try:
        token = get_access_token()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = ALERT_FROM
        msg["To"] = ALERT_TO
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=json.dumps({"raw": raw}).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            r = json.loads(resp.read())
            print(f"  [Moody Email] ✅ Sent (id: {r.get('id','?')})")
            return True
    except urllib.error.HTTPError as e:
        print(f"  [Moody Email] ❌ {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"  [Moody Email] ❌ {e}")
    return False


def send_ready_alert(last_woke):
    woke_str = last_woke.strftime("%H:%M UTC") if last_woke else "?"
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;background:#0a0a1a;border-radius:14px;overflow:hidden;border:1px solid #2a2a5a;">

      <div style="background:#0f0f2a;padding:20px;text-align:center;border-bottom:1px solid #2a2a5a;">
        <span style="font-size:42px;">🏎️</span>
        <h1 style="color:#7f9fff;margin:8px 0 4px;font-size:22px;">Moody Madness — Assistants Ready!</h1>
        <p style="color:#aaa;margin:0;font-size:13px;">12 hours since last wake · {now_str}</p>
      </div>

      <div style="padding:16px;">

        <div style="background:#1a1a3a;border-radius:8px;padding:14px;margin:10px 0;border-left:4px solid #7f9fff;">
          <p style="color:#fff;font-size:15px;margin:0 0 8px;"><b>Your 3 AI assistants have finished driving.</b></p>
          <p style="color:#aaa;font-size:13px;margin:0;">Last woke: {woke_str}<br>Ready since: {now_str}</p>
        </div>

        <div style="margin:12px 0;">
          <p style="color:#fff;font-size:14px;margin:0 0 8px;"><b>What to do now:</b></p>

          <div style="margin:6px 0;padding:8px 10px;background:#1a1200;border-left:3px solid #ff9900;border-radius:4px;">
            <span style="color:#fff;font-size:13px;">1. Go to <a href="{MOODY_URL}" style="color:#7f9fff;">moodymadness.com/ai-assistants</a></span>
          </div>

          <div style="margin:6px 0;padding:8px 10px;background:#1a1200;border-left:3px solid #ff9900;border-radius:4px;">
            <span style="color:#fff;font-size:13px;">2. Click <b>USE ENERGY CAN</b> on each assistant → sign the transaction</span>
          </div>

          <div style="margin:6px 0;padding:8px 10px;background:#1a1200;border-left:3px solid #ff9900;border-radius:4px;">
            <span style="color:#fff;font-size:13px;">3. Click <b>Wake Up Assistant</b> → sign again → assistant starts driving</span>
          </div>

          <div style="margin:6px 0;padding:8px 10px;background:#1a1200;border-left:3px solid #ff9900;border-radius:4px;">
            <span style="color:#fff;font-size:13px;">4. Repeat for all 3 assistants</span>
          </div>

          <div style="margin:6px 0;padding:8px 10px;background:#0d200d;border-left:3px solid #4caf50;border-radius:4px;">
            <span style="color:#fff;font-size:13px;">5. After waking all 3, hit this link to reset your 12hr timer:<br>
            <a href="https://mantis-pro-production.up.railway.app/moody/woke" style="color:#4caf50;font-weight:bold;">mantis-pro-production.up.railway.app/moody/woke</a></span>
          </div>
        </div>

      </div>

      <div style="padding:12px 14px;text-align:center;">
        <a href="{MOODY_URL}" style="background:#3a3aaa;color:#fff;padding:12px 36px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;display:inline-block;">Wake Assistants →</a>
      </div>

      <p style="color:#333;font-size:11px;text-align:center;padding:10px;margin:0;">Mantis Pro · Moody Madness Monitor · 12hr sessions</p>
    </div>"""

    plain = (
        f"🏎️ Moody Madness — Your 3 AI assistants are ready to drive!\n\n"
        f"Last woke: {woke_str}\n\n"
        f"Steps:\n"
        f"1. Go to {MOODY_URL}\n"
        f"2. Click USE ENERGY CAN on each → sign transaction\n"
        f"3. Click Wake Up Assistant → sign again\n"
        f"4. Repeat for all 3\n"
        f"5. Hit https://mantis-pro-production.up.railway.app/moody/woke to reset timer\n"
    )

    return send_email("🏎️ Moody Madness — Wake Your Assistants Now!", html, plain)


def send_woke_confirmation(woke_time):
    next_wake = woke_time + timedelta(hours=SESSION_HOURS)
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;background:#0a0a1a;border-radius:14px;overflow:hidden;border:1px solid #2a5a2a;">
      <div style="background:#0f2a0f;padding:18px;text-align:center;border-bottom:1px solid #2a5a2a;">
        <span style="font-size:36px;">✅🏎️</span>
        <h2 style="color:#7fff7f;margin:8px 0 4px;font-size:18px;">Assistants Are Driving!</h2>
        <p style="color:#aaa;margin:0;font-size:12px;">Timer reset — next reminder in 12 hours</p>
      </div>
      <div style="padding:14px;text-align:center;">
        <p style="color:#fff;font-size:14px;">Woke at: <b>{woke_time.strftime('%H:%M UTC')}</b></p>
        <p style="color:#fff;font-size:14px;">Next reminder: <b>{next_wake.strftime('%H:%M UTC')}</b></p>
        <p style="color:#aaa;font-size:13px;">Mantis Pro will email you when they're ready to drive again.</p>
      </div>
      <p style="color:#333;font-size:11px;text-align:center;padding:10px;margin:0;">Mantis Pro · Moody Madness Monitor</p>
    </div>"""

    plain = f"✅ Assistants woken at {woke_time.strftime('%H:%M UTC')}. Next reminder at {next_wake.strftime('%H:%M UTC')}."
    send_email("✅ Moody Madness — Timer Reset, Assistants Driving!", html, plain)


# ── SESSION CHECK (called every hour by Mantis) ────────────────────────────

def moody_check():
    print(f"\n{'─'*10} MOODY MADNESS CHECK {'─'*10}")
    status = get_status()
    print(f"  Status: {status['status']} | {status['message']}")

    if status["status"] == "unknown":
        print("  No wake recorded yet — waiting for user to hit /moody/woke")
        return

    if status["status"] == "ready" and not _moody_state["reminder_sent"]:
        print("  🏎️ Assistants ready — sending reminder email")
        if send_ready_alert(_moody_state["last_woke"]):
            _moody_state["reminder_sent"] = True
    elif status["status"] == "ready":
        print("  🏎️ Assistants ready but reminder already sent")
    else:
        h = status["hours_remaining"]
        print(f"  Driving — {h:.1f} hrs remaining")
