"""
bunny_agent.py — Bunny Button daily advisor + energy alert for Mantis Pro
-------------------------------------------------------------------------
Two separate email flows:
  1. Hourly advisor — runs every hour, sends tasks not yet done today
  2. Energy alert  — fires only when energy is 100% full

State tracking uses a simple in-memory dict (resets on redeploy, which is fine).
"""

import os
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bunny_button import BunnyButton, RateLimitError, AuthError

ALERT_TO = "smolobah21@gmail.com"
ALERT_FROM = "smolobah21@gmail.com"

# ── IN-MEMORY STATE (tracks what's been done today) ────────────────────────
_daily_state = {
    "date": "",           # UTC date string e.g. "2026-05-30"
    "energy_alerted": False,   # did we send the full-energy email today
    "tasks_done": set(),       # set of task keys completed today
}

def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _reset_if_new_day():
    today = _today()
    if _daily_state["date"] != today:
        _daily_state["date"] = today
        _daily_state["energy_alerted"] = False
        _daily_state["tasks_done"] = set()
        print(f"  [State] New day {today} — daily state reset")

def _mark_done(task_key):
    _daily_state["tasks_done"].add(task_key)

def _is_done(task_key):
    return task_key in _daily_state["tasks_done"]


# ── GMAIL OAUTH ────────────────────────────────────────────────────────────

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
            print(f"  [Email] ✅ Sent (id: {r.get('id','?')})")
            return True
    except urllib.error.HTTPError as e:
        print(f"  [Email] ❌ {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"  [Email] ❌ {e}")
    return False


# ── HTML HELPERS ───────────────────────────────────────────────────────────

def card(title, emoji, content, color="#1a3a1a"):
    return f"""
    <div style="margin:10px 0;border-radius:8px;overflow:hidden;border:1px solid {color};">
      <div style="background:#111;padding:9px 14px;border-bottom:1px solid {color};">
        <b style="color:#7fff7f;font-size:14px;">{emoji} {title}</b>
      </div>
      <div style="padding:11px 14px;background:#0d150d;">{content}</div>
    </div>"""

def task_item(done, text, detail=""):
    icon = "✅" if done else "🔲"
    bg = "#0d200d" if done else "#1a0d00"
    border = "#2a5a2a" if done else "#3a2000"
    opacity = "0.6" if done else "1"
    detail_html = f'<div style="color:#888;font-size:12px;margin-top:3px;">{detail}</div>' if detail else ""
    return f"""
    <div style="margin:5px 0;padding:8px 10px;background:{bg};border-left:3px solid {border};border-radius:4px;opacity:{opacity};">
      <span style="font-size:13px;color:#fff;">{icon} {text}</span>
      {detail_html}
    </div>"""

def stat_pill(label, value, color="#4caf50"):
    return f'<span style="display:inline-block;margin:3px;padding:4px 10px;background:#111;border:1px solid {color};border-radius:20px;font-size:12px;"><span style="color:#888;">{label}</span> <b style="color:{color};">{value}</b></span>'

def tip_box(text, color="#ff9900"):
    return f'<div style="margin:6px 0;padding:8px 10px;background:#1a1200;border-left:3px solid {color};border-radius:4px;color:#ddd;font-size:13px;">{text}</div>'


# ── TASK EVALUATION ────────────────────────────────────────────────────────

def evaluate_tasks(player, inventory, farm, quests_data, streaks, stake):
    """
    Returns a list of tasks: {key, text, detail, done, priority}
    Priority: 1=urgent, 2=important, 3=nice to do
    """
    tasks = []

    total_carrots = player.get("totalCarrotsEarned", 0)
    balance = player.get("carrotBalance", 0)
    burned = player.get("carrotsBurned", 0)
    steal = player.get("stealMultiplier", 0.2)
    steal_level = player.get("stealLevel", 0)
    regen_level = player.get("regenLevel", 0)
    energy_level = player.get("maxEnergyLevel", 0)
    breed = player.get("bunnyBreed") or "None"
    bunny_class = player.get("bunnyClass") or "None"
    referral_code = player.get("referralCode", "?")
    potion_used = player.get("energyPotionUsedToday", False)
    potion_cost = player.get("energyPotionCost", 0)
    total_clicks = player.get("totalClicks", 0)
    # Try all known streak field names
    streak = (streaks.get("streakLength") or
              streaks.get("currentStreak") or
              streaks.get("streak") or
              streaks.get("loginStreak") or
              streaks.get("days") or 0)
    print(f"  [Debug] Streak raw data: {streaks}")

    farm_cps = farm.get("carrotsPerSecond", 0)
    farm_cap_warn = farm.get("capWarning", False)

    quest_list = quests_data.get("quests", []) if isinstance(quests_data, dict) else []
    quest_names_done = {q.get("name") for q in quest_list if q.get("status") == "complete"}
    quest_names_pending = [q.get("name") for q in quest_list if q.get("status") != "complete"]

    equipped = inventory.get("equipped", {})
    has_cape = bool(equipped.get("cape"))
    has_boots = bool(equipped.get("boots"))
    has_hat = bool(equipped.get("hat"))

    stake_data = stake.get("stakes", []) if isinstance(stake, dict) else []
    active_stakes = [s for s in stake_data if s.get("status") == "active"]

    # ── DAILY MUST-DO TASKS ────────────────────────────────────────────────

    # 1. Press button — mark done if total clicks increased since last check
    energy_cur = player.get("energyCurrent", 500)
    prev_clicks = _daily_state.get("prev_total_clicks", total_clicks)
    if total_clicks > prev_clicks:
        _mark_done("press_button")
    _daily_state["prev_total_clicks"] = total_clicks

    # Also mark done if energy is low (they clearly played)
    if energy_cur < 100:
        _mark_done("press_button")

    tasks.append({
        "key": "press_button",
        "priority": 1,
        "text": "Press the button until energy runs out",
        "detail": f"Go to bunnybutton.xyz → keep pressing until energy hits 0. Your steal is {steal}x — each press earns carrots. (Energy: {energy_cur:.0f}/500)",
        "done": _is_done("press_button")
    })

    # 2. Use daily energy potion
    potion_done = potion_used or _is_done("energy_potion")
    if not potion_used and balance >= potion_cost and potion_cost > 0:
        tasks.append({
            "key": "energy_potion",
            "priority": 1,
            "text": "Use your free daily Energy Potion",
            "detail": f"After draining energy, go to Shop → Energy Potion. Costs {potion_cost:,.0f} carrots and fully refills your energy. Free extra session every day!",
            "done": potion_done
        })
    elif potion_used:
        tasks.append({
            "key": "energy_potion",
            "priority": 1,
            "text": "Use your free daily Energy Potion",
            "detail": "Already used today ✅",
            "done": True
        })

    # 3. Claim farm carrots (if farm active)
    if farm_cps > 0:
        farm_done = not farm_cap_warn or _is_done("claim_farm")
        tasks.append({
            "key": "claim_farm",
            "priority": 1 if farm_cap_warn else 2,
            "text": "Collect your farm carrots" + (" ⚠️ CAP ALMOST HIT!" if farm_cap_warn else ""),
            "detail": f"Go to Farm tab → collect offline carrots. Farm makes {farm_cps*3600:.1f} carrots/hr. You must collect at least every 8 hours or you lose yield.",
            "done": farm_done
        })

    # 4. Log in streak
    # Streak done if streak >= 1 (they've logged in at some point) or manually marked
    streak_done = streak >= 1 or _is_done("streak")
    tasks.append({
        "key": "streak",
        "priority": 1,
        "text": f"Keep your daily login streak alive (currently {streak} days)",
        "detail": "Just signing in counts. Streak milestones give rewards. Don't break it!",
        "done": streak_done
    })

    # ── UPGRADE TASKS ──────────────────────────────────────────────────────

    # 5. Buy steal upgrade — detect if level increased since last check
    prev_steal_level = _daily_state.get("prev_steal_level", steal_level)
    if steal_level > prev_steal_level:
        _mark_done("steal_upgrade")
    _daily_state["prev_steal_level"] = steal_level

    steal_target = 1.0
    steal_done = steal >= steal_target or _is_done("steal_upgrade")
    tasks.append({
        "key": "steal_upgrade",
        "priority": 1 if steal < 0.5 else 2,
        "text": f"Buy a Steal upgrade in the Shop (currently {steal}x, target 1.0x+)",
        "detail": "Shop → Steal. This is the most important stat — directly multiplies every carrot you earn. Keep buying until you hit at least 1.0x.",
        "done": steal_done
    })

    # 6. Buy regen upgrade — detect level increase
    prev_regen_level = _daily_state.get("prev_regen_level", regen_level)
    if regen_level > prev_regen_level:
        _mark_done("regen_upgrade")
    _daily_state["prev_regen_level"] = regen_level

    regen_done = regen_level >= 3 or _is_done("regen_upgrade")
    tasks.append({
        "key": "regen_upgrade",
        "priority": 2,
        "text": f"Buy a Regen upgrade in the Shop (level {regen_level}, target level 3+)",
        "detail": "Shop → Regen. Faster regen = more presses per day = more carrots. Alternate between Steal and Regen upgrades.",
        "done": regen_done
    })

    # 7. Buy farm if not active — auto-detect purchase
    if farm_cps > 0:
        _mark_done("buy_farm")
    if farm_cps == 0:
        tasks.append({
            "key": "buy_farm",
            "priority": 1,
            "text": "Buy Farm Level 1 — passive income while you sleep!",
            "detail": "Go to Farm tab → buy Level 1. Even a tiny farm adds up over hours. Do this before buying more than 2-3 stat upgrades.",
            "done": _is_done("buy_farm")
        })

    # 8. Breed upgrade (Sprout is bad)
    if breed == "sprout" or breed == "Sprout" or breed == "None":
        tasks.append({
            "key": "breed_upgrade",
            "priority": 2,
            "text": "Upgrade your breed — Sprout only gives 0.2x steal",
            "detail": f"Save 2,500 carrots for a Breed Change, or earn 25,000 total carrots to unlock Jackalope (1.5x steal) for FREE. You have {total_carrots:,.0f}/{25000:,.0f} carrots toward Jackalope.",
            "done": _is_done("breed_upgrade")
        })

    # ── SOCIAL / QUEST TASKS ───────────────────────────────────────────────

    # 9. Connect X — check quest AND any x-related player fields
    x_handle = player.get("xHandle") or player.get("twitterHandle") or player.get("xUsername")
    x_done = "Blue Check Toll Bridge" in quest_names_done or bool(x_handle) or _is_done("connect_x")
    tasks.append({
        "key": "connect_x",
        "priority": 2,
        "text": "Connect your X (Twitter) account",
        "detail": "Go to Wallet menu → connect X. This completes the 'Blue Check Toll Bridge' quest and shows your handle on the leaderboard instead of a wallet address.",
        "done": x_done
    })

    # 10. Connect Discord
    tasks.append({
        "key": "connect_discord",
        "priority": 2,
        "text": "Connect your Discord account",
        "detail": "Go to Wallet menu → connect Discord. Unlocks in-game username, Discord commands, and leaderboard sync.",
        "done": _is_done("connect_discord")
    })

    # 11. Refer 2 friends
    goblin_done = "Goblin Diplomacy" in quest_names_done or _is_done("refer_friends")
    tasks.append({
        "key": "refer_friends",
        "priority": 2,
        "text": f"Refer 2 friends to unlock parties (your code: {referral_code})",
        "detail": f"Share your referral code <b>{referral_code}</b> on X or Discord. When 2 people use it, you unlock PARTIES which gives +5% bonus carrots per press. You also earn 8% of their carrot earnings.",
        "done": goblin_done
    })

    # 12. Join a party
    ratcatchers_done = "Ratcatchers" in quest_names_done or _is_done("join_party")
    if goblin_done:
        tasks.append({
            "key": "join_party",
            "priority": 2,
            "text": "Join a party (unlocked after Goblin Diplomacy quest)",
            "detail": "Go to Party tab → browse party list or ask in Bunny Button Discord for an invite. Full party = +5% bonus per press for everyone.",
            "done": ratcatchers_done
        })

    # 13. Stake surplus carrots
    if balance > 5000 and len(active_stakes) == 0:
        tasks.append({
            "key": "stake_carrots",
            "priority": 3,
            "text": f"Stake your surplus carrots (balance: {balance:,.0f})",
            "detail": "Go to Farm tab → Stake section. Lock for 30/90/365 days. Longer = higher APY. Only stake what you won't need for upgrades in next few days.",
            "done": _is_done("stake_carrots")
        })

    # 14. Submit creator content
    tasks.append({
        "key": "creator_content",
        "priority": 3,
        "text": "Submit content to the Creator Program for bonus rewards",
        "detail": "Make a post about Bunny Button on X, then go to bunnybutton.xyz/creator-program → submit the URL. You can earn carrot crates, tokens, or unique items.",
        "done": _is_done("creator_content")
    })

    return tasks


# ── EMAIL BUILDERS ─────────────────────────────────────────────────────────

def build_hourly_email(player, inventory, energy, farm, quests_data, streaks, stake):
    tasks = evaluate_tasks(player, inventory, farm, quests_data, streaks, stake)

    breed = player.get("bunnyBreed") or "None"
    bunny_class = player.get("bunnyClass") or "None"
    steal = player.get("stealMultiplier", 0.2)
    total_carrots = player.get("totalCarrotsEarned", 0)
    balance = player.get("carrotBalance", 0)
    rank = player.get("rank", "?")
    regen = player.get("energyRegenPerHour", 0)

    energy_cur = energy.get("energyCurrent", 0)
    energy_max = energy.get("energyMax", 500)
    energy_pct = energy.get("percentFull", 0)
    hours_to_full = energy.get("hoursToFull")

    done_count = sum(1 for t in tasks if t["done"])
    total_count = len(tasks)
    pending_tasks = [t for t in tasks if not t["done"]]
    urgent = [t for t in pending_tasks if t["priority"] == 1]
    important = [t for t in pending_tasks if t["priority"] == 2]
    nice = [t for t in pending_tasks if t["priority"] == 3]

    # Stats bar
    energy_color = "#4caf50" if energy_pct >= 80 else "#ff9900" if energy_pct >= 40 else "#ff5555"
    stats_html = (
        stat_pill("Rank", f"#{rank}") +
        stat_pill("Energy", f"{energy_cur:.0f}/{energy_max:.0f}", energy_color) +
        stat_pill("Steal", f"{steal}x") +
        stat_pill("Total Carrots", f"{total_carrots:,.0f}") +
        stat_pill("Balance", f"{balance:,.0f}") +
        stat_pill("Breed", breed) +
        stat_pill("Class", bunny_class)
    )

    # Progress bar
    pct = int((done_count / total_count) * 100) if total_count else 0
    bar_color = "#4caf50" if pct >= 80 else "#ff9900" if pct >= 40 else "#ff5555"
    progress_html = f"""
    <div style="margin:8px 0;">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
        <span style="color:#aaa;font-size:12px;">Daily progress</span>
        <span style="color:{bar_color};font-size:12px;font-weight:bold;">{done_count}/{total_count} tasks done ({pct}%)</span>
      </div>
      <div style="background:#222;border-radius:10px;height:8px;">
        <div style="background:{bar_color};width:{pct}%;height:8px;border-radius:10px;"></div>
      </div>
    </div>"""

    # Energy status
    if energy_pct >= 100:
        energy_msg = tip_box("⚡ <b>ENERGY IS FULL RIGHT NOW — go press the button immediately!</b>", "#4caf50")
    elif hours_to_full:
        energy_msg = tip_box(f"⏱️ Energy at {energy_pct}% — full in ~{hours_to_full:.1f} hours. Regen: {regen:.1f}/hr. You'll get an email when it's ready.", "#888")
    else:
        energy_msg = ""

    # Task sections
    def render_tasks(task_list, title, color):
        if not task_list:
            return ""
        items = "".join(task_item(t["done"], t["text"], t["detail"]) for t in task_list)
        return card(title, "", items, color)

    urgent_html = render_tasks(urgent, "🚨 DO THESE NOW (Urgent)", "#3a1a00") if urgent else tip_box("✅ All urgent tasks done! Great work.", "#4caf50")
    important_html = render_tasks(important, "📋 Do These Today (Important)", "#1a2a3a")
    nice_html = render_tasks(nice, "💡 Nice To Do (Bonus)", "#1a1a2a") if nice else ""

    # Leaderboard tip
    top_score = 12729  # from last leaderboard check
    gap = max(0, top_score - total_carrots)
    leaderboard_tip = tip_box(f"🏆 You're rank #{rank}. Top player has ~{top_score:,.0f} carrots. Gap: {gap:,.0f} carrots. Keep pressing and upgrading steal to climb!", "#ff9900")

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;background:#0a0f0a;border-radius:14px;overflow:hidden;border:1px solid #1a3a1a;">
      <div style="background:#0f1a0f;padding:18px;text-align:center;border-bottom:1px solid #1a3a1a;">
        <span style="font-size:36px;">🥕</span>
        <h1 style="color:#7fff7f;margin:6px 0 3px;font-size:20px;">Mantis Pro — Daily Advisor</h1>
        <p style="color:#666;margin:0;font-size:12px;">{now_str} · {done_count}/{total_count} tasks done today</p>
      </div>
      <div style="padding:12px 14px;">
        <div style="margin:8px 0;">{stats_html}</div>
        {progress_bar_html if (progress_bar_html := progress_html) else ""}
        {energy_msg}
        {urgent_html}
        {important_html}
        {nice_html}
        {leaderboard_tip}
      </div>
      <div style="padding:12px 14px;text-align:center;">
        <a href="https://bunnybutton.xyz" style="background:#4caf50;color:#fff;padding:12px 36px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:15px;display:inline-block;">Open Bunny Button →</a>
      </div>
      <p style="color:#333;font-size:11px;text-align:center;padding:10px;margin:0;">Mantis Pro · Checks every hour · Only reminds you of unfinished tasks</p>
    </div>"""

    plain = f"Mantis Pro Daily Advisor [{now_str}]\n{done_count}/{total_count} tasks done\nRank #{rank} | {total_carrots:,.0f} carrots | Energy {energy_cur:.0f}/{energy_max:.0f}\n\nGo play: https://bunnybutton.xyz"

    pending_count = total_count - done_count
    return html, plain, pending_count, tasks


def build_energy_email(player, energy, farm, quests_data, streaks):
    steal = player.get("stealMultiplier", 0.2)
    total_carrots = player.get("totalCarrotsEarned", 0)
    balance = player.get("carrotBalance", 0)
    rank = player.get("rank", "?")
    breed = player.get("bunnyBreed") or "None"
    bunny_class = player.get("bunnyClass") or "None"
    energy_cur = energy.get("energyCurrent", 0)
    energy_max = energy.get("energyMax", 500)
    potion_used = player.get("energyPotionUsedToday", False)
    potion_cost = player.get("energyPotionCost", 0)
    referral_code = player.get("referralCode", "?")
    regen = player.get("energyRegenPerHour", 0)

    quest_list = quests_data.get("quests", []) if isinstance(quests_data, dict) else []
    pending_quests = [q for q in quest_list if q.get("status") != "complete"]
    next_quest = pending_quests[0] if pending_quests else None

    QUEST_HOW = {
        "First Hop": "press the button once",
        "Carrot Pickpocket": "earn carrots by pressing the button",
        "Goblin Diplomacy": f"refer 2 friends — share code: <b>{referral_code}</b>",
        "Blue Check Toll Bridge": "Wallet menu → connect X account",
        "Ratcatchers": "Party tab → join any party",
        "Five Bunnies Walk Into A Pub": "get your party to 5 members",
        "Wizards Hate This Bunny": "reach 2.25x total steal via Shop upgrades",
        "The Carrot Cartel": f"earn {max(0,25000-total_carrots):,.0f} more carrots (need 25,000 total)",
        "Burnt Offerings": f"burn {max(0,50000-player.get('carrotsBurned',0)):,.0f} more on upgrades",
        "Recipe For Disaster": "complete all 19 quests",
    }

    quest_section = ""
    if next_quest:
        qname = next_quest.get("name", "?")
        qhow = QUEST_HOW.get(qname, "check in-game")
        quest_section = tip_box(f"🎯 <b>Next Quest: {qname}</b> — {qhow}", "#ff9900")

    potion_section = ""
    if not potion_used and balance >= potion_cost and potion_cost > 0:
        potion_section = tip_box(f"💊 <b>After you drain energy:</b> use your daily Energy Potion! Shop → Energy Potion. Costs {potion_cost:,.0f} carrots, fully refills energy. Free second session!", "#4caf50")

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;background:#0a0f0a;border-radius:14px;overflow:hidden;border:1px solid #1a3a1a;">
      <div style="background:#0f1a0f;padding:20px;text-align:center;border-bottom:1px solid #1a3a1a;">
        <span style="font-size:48px;">⚡</span>
        <h1 style="color:#7fff7f;margin:8px 0 4px;font-size:22px;">Energy Full — Press NOW!</h1>
        <p style="color:#aaa;margin:0;font-size:13px;">Your {energy_cur:.0f}/{energy_max:.0f} energy is ready. Don't waste it.</p>
      </div>
      <div style="padding:14px;">
        <div style="margin:6px 0;">
          {stat_pill("Rank", f"#{rank}")}
          {stat_pill("Steal", f"{steal}x")}
          {stat_pill("Breed", breed)}
          {stat_pill("Class", bunny_class)}
          {stat_pill("Total", f"{total_carrots:,.0f}")}
        </div>
        {tip_box(f"⚡ <b>Go to bunnybutton.xyz and press the button until energy hits 0.</b> Each press earns {steal}x carrots. Your regen is {regen:.1f}/hr so it'll refill in ~{energy_max/regen:.1f} hours.", "#4caf50")}
        {potion_section}
        {quest_section}
      </div>
      <div style="padding:12px 14px;text-align:center;">
        <a href="https://bunnybutton.xyz" style="background:#4caf50;color:#fff;padding:14px 40px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;display:inline-block;">Press the Button →</a>
      </div>
      <p style="color:#333;font-size:11px;text-align:center;padding:10px;margin:0;">Mantis Pro · Abstract Chain Agent</p>
    </div>"""

    plain = f"⚡ Energy full {energy_cur:.0f}/{energy_max:.0f} — go press now! https://bunnybutton.xyz"
    return html, plain


# ── MAIN SESSION (called every hour) ──────────────────────────────────────

def bunny_session():
    from datetime import timezone as tz
    separator = lambda label="": print(f"\n{'─'*10} {label} {'─'*10}")

    separator("BUNNY BUTTON SESSION")
    _reset_if_new_day()

    cookie = os.environ.get("BUNNY_SESSION_COOKIE")
    bb = BunnyButton(session_cookie=cookie)

    # Public data
    try:
        separator("LEADERBOARD (Top 10)")
        print(bb.leaderboard_summary(limit=10))
    except Exception as e:
        print(f"Leaderboard error: {e}")

    try:
        presale = bb.get_presale_status()
        print(f"  Presale: {presale.get('active')} | {presale.get('remainingAllocation',0):,.0f} CARROT left")
    except Exception as e:
        print(f"Presale error: {e}")

    if not cookie:
        print("  No BUNNY_SESSION_COOKIE — skipping player checks")
        separator("SESSION COMPLETE")
        return

    # Player data
    try:
        player = bb.get_player()
        inventory = bb.get_inventory()
        energy = bb.energy_status()
        farm = bb.farm_cap_warning()
        quests_data = bb.get_quests()
        streaks = bb.get_streaks()
        stake = bb.get_stake()
    except Exception as e:
        print(f"Data fetch error: {e}")
        separator("SESSION COMPLETE")
        return

    energy_full = energy.get("isFull", False)
    energy_cur = energy.get("energyCurrent", 0)
    energy_max = energy.get("energyMax", 500)
    total_carrots = player.get("totalCarrotsEarned", 0)
    rank = player.get("rank", "?")

    separator("QUICK STATS")
    print(f"  Breed: {player.get('bunnyBreed')} | Class: {player.get('bunnyClass')}")
    print(f"  Energy: {energy_cur:.0f}/{energy_max:.0f} | Steal: {player.get('stealMultiplier')}x | Rank: #{rank}")
    print(f"  Total: {total_carrots:,.0f} | Balance: {player.get('carrotBalance',0):,.0f}")
    print(f"  Farm: {farm.get('carrotsPerSecond',0)*3600:.1f}/hr | Streak: {streaks.get('streakLength',0)} days")

    # ── ENERGY ALERT (only when full, only once per day) ──────────────────
    if energy_full and not _daily_state["energy_alerted"]:
        print("  ⚡ Energy FULL — sending energy alert email")
        html, plain = build_energy_email(player, energy, farm, quests_data, streaks)
        if send_email("⚡ Energy Full — Press the Button NOW!", html, plain):
            _daily_state["energy_alerted"] = True
    elif energy_full:
        print("  ⚡ Energy full but alert already sent today")

    # ── HOURLY ADVISOR (always sends, shows remaining tasks) ───────────────
    separator("HOURLY ADVISOR")
    html, plain, pending_count, tasks = build_hourly_email(
        player, inventory, energy, farm, quests_data, streaks, stake
    )

    done_count = sum(1 for t in tasks if t["done"])
    total_count = len(tasks)

    if pending_count == 0:
        print(f"  All {total_count} tasks done today! Sending completion email.")
        send_email(f"✅ You've done everything today! Rank #{rank}", html, plain)
    else:
        print(f"  {pending_count} tasks pending — sending advisor email")
        send_email(f"🥕 Mantis Pro — {pending_count} things to do now | Rank #{rank}", html, plain)

    separator("BUNNY SESSION COMPLETE")
