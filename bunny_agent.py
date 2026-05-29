"""
bunny_agent.py — Bunny Button full game advisor for Mantis Pro
--------------------------------------------------------------
Sends a complete noob-friendly email every time energy is full.
Covers every activity in the game with plain-English guidance.
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
        raise Exception("Missing Gmail OAuth env vars")
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
    except urllib.error.HTTPError as e:
        print(f"  [Email] ❌ {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"  [Email] ❌ {e}")


# ── HTML HELPERS ───────────────────────────────────────────────────────────

def section(title, emoji, content, border="#1a3a1a"):
    return f"""
    <div style="margin:12px 0;border-radius:8px;overflow:hidden;border:1px solid {border};">
      <div style="background:#111;padding:10px 14px;border-bottom:1px solid {border};">
        <span style="color:#7fff7f;font-weight:bold;font-size:14px;">{emoji} {title}</span>
      </div>
      <div style="padding:12px 14px;background:#0d150d;">
        {content}
      </div>
    </div>"""

def row(label, value, note=""):
    note_html = f'<span style="color:#888;font-size:12px;"> — {note}</span>' if note else ""
    return f'<div style="margin:4px 0;"><span style="color:#aaa;font-size:13px;">{label}:</span> <span style="color:#fff;font-weight:bold;">{value}</span>{note_html}</div>'

def tip(text, color="#ff9900"):
    return f'<div style="margin:6px 0;padding:8px 10px;background:#1a1200;border-left:3px solid {color};border-radius:4px;color:#fff;font-size:13px;">{text}</div>'

def action(step, text):
    return f'<div style="margin:6px 0;display:flex;gap:8px;align-items:flex-start;"><span style="color:#4caf50;font-weight:bold;min-width:20px;">{step}.</span><span style="color:#fff;font-size:13px;">{text}</span></div>'

def quest_row(name, status, how):
    icon = "✅" if status == "complete" else "🔲"
    color = "#2a5a2a" if status == "complete" else "#1a1a1a"
    return f'<div style="margin:4px 0;padding:6px 8px;background:{color};border-radius:4px;"><span style="font-size:13px;">{icon} <b style="color:#fff;">{name}</b> <span style="color:#888;font-size:12px;">— {how}</span></span></div>'


# ── SMART ADVISOR ──────────────────────────────────────────────────────────

def build_email(player, inventory, energy, farm, quests_data, streaks, stake):
    breed = player.get("bunnyBreed") or "None"
    bunny_class = player.get("bunnyClass") or "None"
    steal = player.get("stealMultiplier", 0.2)
    energy_cur = player.get("energyCurrent", 0)
    energy_max = player.get("energyMax", 500)
    regen = player.get("energyRegenPerHour", 0)
    total_carrots = player.get("totalCarrotsEarned", 0)
    balance = player.get("carrotBalance", 0)
    burned = player.get("carrotsBurned", 0)
    rank = player.get("rank", "?")
    steal_level = player.get("stealLevel", 0)
    regen_level = player.get("regenLevel", 0)
    energy_level = player.get("maxEnergyLevel", 0)
    total_clicks = player.get("totalClicks", 0)
    referral_code = player.get("referralCode", "?")
    potion_used = player.get("energyPotionUsedToday", False)
    potion_cost = player.get("energyPotionCost", 0)

    farm_cps = farm.get("carrotsPerSecond", 0)
    farm_cap_warn = farm.get("capWarning", False)
    farm_hours_left = farm.get("hoursToCapFull")

    streak_len = streaks.get("streakLength") or streaks.get("currentStreak", 0)

    quest_list = quests_data.get("quests", []) if isinstance(quests_data, dict) else []
    quests_done = sum(1 for q in quest_list if q.get("status") == "complete")
    quests_pending = [q for q in quest_list if q.get("status") != "complete"]
    next_quest = quests_pending[0] if quests_pending else None

    equipped = inventory.get("equipped", {})
    cape = equipped.get("cape")
    boots = equipped.get("boots")
    hat = equipped.get("hat")
    gear_bonuses = inventory.get("aggregateBonuses", {})

    stake_data = stake.get("stakes", []) if isinstance(stake, dict) else []
    active_stakes = [s for s in stake_data if s.get("status") == "active"]

    # ── SECTION 1: Your Stats Right Now ──────────────────────────────────
    pct = round((energy_cur / energy_max) * 100)
    stats_content = (
        row("Energy", f"{energy_cur:.0f} / {energy_max:.0f} ({pct}%)", "use it all before it overflows") +
        row("Regen speed", f"{regen:.1f}/hr", f"fills up in ~{(energy_max/regen):.1f}hrs when empty") +
        row("Steal multiplier", f"{steal}x", "higher = more carrots per button press") +
        row("Rank", f"#{rank}", "based on total carrots earned") +
        row("Total carrots earned", f"{total_carrots:,.0f}") +
        row("Current balance", f"{balance:,.0f}", "carrots you can spend") +
        row("Carrots burned", f"{burned:,.0f}", "spent on upgrades, counts for quests") +
        row("Total button presses", f"{total_clicks:,.0f}") +
        row("Daily streak", f"{streak_len} days", "log in every day to keep it")
    )

    # ── SECTION 2: What To Do RIGHT NOW ───────────────────────────────────
    now_actions = []

    # Energy
    now_actions.append(action("1", f"⚡ <b>Press the button now!</b> Your energy is full ({energy_cur:.0f}/{energy_max:.0f}). Go to <a href='https://bunnybutton.xyz' style='color:#4caf50;'>bunnybutton.xyz</a> and keep pressing until energy runs out."))

    # Energy potion
    if not potion_used and balance >= potion_cost and potion_cost > 0:
        now_actions.append(action("2", f"💊 <b>Use your daily Energy Potion</b> — costs {potion_cost:,.0f} carrots and fully refills your energy. Do this after you drain it. Go to Shop → Energy Potion. Free extra session every day!"))
    elif not potion_used:
        now_actions.append(action("2", "💊 <b>Energy Potion</b> is available today but you may not have enough carrots yet. Check Shop when balance grows."))

    # Next quest
    if next_quest:
        qname = next_quest.get("name", "?")
        qhow = {
            "First Hop": "press the button once",
            "Carrot Pickpocket": "earn carrots by pressing the button",
            "Goblin Diplomacy": f"refer 2 friends — share your referral code: <b>{referral_code}</b>",
            "Blue Check Toll Bridge": "go to Wallet menu → connect your X (Twitter) account",
            "Ratcatchers": "join a party — ask in the Bunny Button Discord",
            "Five Bunnies Walk Into A Pub": "get your party filled to 5 members",
            "Wizards Hate This Bunny": "buy steal upgrades until your total steal reaches 2.25x — unlocks Moonburrow breed",
            "The Carrot Cartel": f"earn {max(0, 25000-total_carrots):,.0f} more carrots (need 25,000 total) — unlocks Jackalope breed",
            "Burnt Offerings": f"burn {max(0, 50000-burned):,.0f} more carrots on upgrades (need 50,000 burned) — rewards 5,000 carrots",
            "Recipe For Disaster": "complete all other 19 quests — rewards quest cape + 20,000 carrots",
        }.get(qname, "check in-game for details")
        now_actions.append(action("3", f"🎯 <b>Next quest: {qname}</b> — {qhow}"))

    # Farm claim warning
    if farm_cap_warn:
        now_actions.append(action("!", "🌾 <b>URGENT — Claim your farm carrots!</b> Your farm is about to hit the 8-hour cap. Go to Farm tab and collect now or you lose yield."))

    now_content = "".join(now_actions)

    # ── SECTION 3: Upgrade Shop Guide ─────────────────────────────────────
    upgrade_tips = []
    if steal_level == 0:
        upgrade_tips.append(tip("🗡️ <b>Buy your first Steal upgrade first.</b> This increases how many carrots you get per press. It's the most important upgrade. Go to Shop → Steal."))
    elif steal < 1.0:
        upgrade_tips.append(tip(f"🗡️ Your steal is {steal}x. Keep buying Steal upgrades — aim for at least 1.0x before spending on other things."))
    else:
        upgrade_tips.append(tip(f"🗡️ Steal is at {steal}x (level {steal_level}). Good. Now alternate between Steal and Regen upgrades."))

    if regen_level == 0:
        upgrade_tips.append(tip("⚡ <b>Buy Regen upgrade after Steal.</b> More regen = faster energy refill = more presses per day. Go to Shop → Regen."))
    else:
        upgrade_tips.append(tip(f"⚡ Regen is at level {regen_level} ({regen:.1f}/hr). Keep upgrading this alongside Steal."))

    if energy_level == 0:
        upgrade_tips.append(tip("🔋 Max Energy upgrade makes your pool bigger. Lower priority than Steal and Regen early on. Buy it after the others are mid-level."))

    if farm_cps == 0:
        upgrade_tips.append(tip("🌾 <b>Buy Farm Level 1 as soon as possible!</b> It makes carrots even while you sleep. Go to Farm tab → buy first level. Do this before doing more than 2-3 stat upgrades."))
    else:
        upgrade_tips.append(tip(f"🌾 Farm is active at {farm_cps:.4f} carrots/sec ({farm_cps*3600:.1f}/hr). Upgrade it periodically — alternate with stat upgrades."))

    if balance > 5000 and len(active_stakes) == 0:
        upgrade_tips.append(tip(f"💰 You have {balance:,.0f} carrots sitting idle. Consider staking some! Go to Farm tab → Stake. Lock for 30/90/365 days for extra CARROT rewards. Only stake what you don't need for upgrades in the next few days."))

    upgrade_content = "".join(upgrade_tips)

    # ── SECTION 4: Breed & Class Guide ────────────────────────────────────
    breed_note = {
        "Sprout": "⚠️ You're on Sprout (free starter) — only 0.2x steal. You should upgrade your breed when you can afford it.",
        "Meadow": "✅ Meadow is a solid balanced breed (1x steal, 1000 energy). Good all-rounder.",
        "Bandit": "✅ Bandit has 1.8x steal — great for big burst sessions. Best with Warrior class.",
        "Spark": "✅ Spark has fast regen (58/hr) — great for frequent short sessions.",
        "Burrow": "✅ Burrow has the biggest energy pool (1500). Great if you play in long sessions.",
        "Moonburrow": "✅ Moonburrow (1.25x steal, 880 energy) — unlocked by quest. Strong mid-game breed.",
        "Jackalope": "✅ Jackalope (1.5x steal) — unlocked by earning 25,000 total carrots. Best free breed.",
    }.get(breed, f"You are on {breed}.")

    class_note = {
        "Warrior": "✅ Warrior (+15% steal) — best class for maximizing carrots per press.",
        "Mage": "✅ Mage (+15% regen) — good for frequent players who press a lot.",
        "Knight": "✅ Knight (+15% max energy) — good for long sessions.",
        "Shaman": "✅ Shaman (1.4x lucky carrot chance) — fun but lower steady income.",
        "None": "⚠️ You haven't picked a class yet! Go to the game and choose one. Pick Warrior for most carrots.",
    }.get(bunny_class, f"Class: {bunny_class}.")

    breed_content = (
        f'<div style="margin:6px 0;color:#fff;font-size:13px;"><b>Your breed:</b> {breed}</div>' +
        tip(breed_note, "#4caf50" if breed != "Sprout" else "#ff5555") +
        f'<div style="margin:6px 0;color:#fff;font-size:13px;"><b>Your class:</b> {bunny_class}</div>' +
        tip(class_note, "#4caf50") +
        tip("🐰 <b>FREE breed upgrades through quests:</b> Reach 2.25x steal → get Moonburrow. Earn 25,000 total carrots → get Jackalope (1.5x steal). Don't pay 2,500 carrots for a breed change until you've unlocked these free ones first.", "#888")
    )

    # ── SECTION 5: Gear (Crates) Guide ────────────────────────────────────
    cape_str = f"{cape.get('name','?')} (+{cape.get('stealBonus',0)}x steal)" if cape else "None equipped"
    boots_str = f"{boots.get('name','?')} (+{boots.get('regenBonus',0)}/hr regen)" if boots else "None equipped"
    hat_str = f"{hat.get('name','?')} (+{hat.get('energyBonus',0)} energy)" if hat else "None equipped"

    gear_content = (
        row("Cape (steal bonus)", cape_str) +
        row("Boots (regen bonus)", boots_str) +
        row("Hat (energy bonus)", hat_str) +
        tip("🎁 <b>How to get gear:</b> Go to Gacha page → open a Carrot Crate (~$10 worth of carrots). You get one random item: Cape, Boots, or Hat. Equip it on the Gear page for instant stat boosts on top of your breed and class.", "#4caf50") +
        tip("💡 <b>When to open crates:</b> Wait until your steal and regen upgrades are at least level 3-4 each. A good crate can beat 1-2 upgrade levels. Don't open crates before you have basic upgrades.", "#888") +
        tip("♻️ <b>Salvage bad items:</b> If you get a bad roll, go to Gear page → Salvage to get carrots back. Save 3 same-slot items and you can reroll them into a better one.", "#888")
    )

    # ── SECTION 6: Party Guide ────────────────────────────────────────────
    party_content = (
        tip("👥 <b>What is a party?</b> Up to 5 wallets team up. A full 5-person party gives everyone +5% bonus carrots per press. A 20% tax on your carrots goes to your party mates — but you get theirs too. Net positive for everyone.", "#4caf50") +
        tip(f"🔓 <b>How to unlock parties:</b> Complete the 'Goblin Diplomacy' quest — refer 2 friends using your code: <b>{referral_code}</b>. Share it on X or Discord.", "#ff9900") +
        tip("📋 <b>How to join:</b> Once unlocked, go to Party tab → browse the party list or get an invite link from someone. Ask in the Bunny Button Discord for party invites.", "#888")
    )

    # ── SECTION 7: Referral Guide ─────────────────────────────────────────
    referral_content = (
        row("Your referral code", referral_code) +
        tip(f"💸 <b>Share your code:</b> When someone uses your referral code, you earn 8% of their button-press carrots (up to 500/day per person). Share it on X, Discord, or with friends. Your code: <b>{referral_code}</b>", "#4caf50") +
        tip("📋 <b>How to use someone else's code:</b> Go to Wallet menu → Referral → enter a code. This helps that person and unlocks your referral quests.", "#888")
    )

    # ── SECTION 8: Staking Guide ──────────────────────────────────────────
    stake_content = (
        tip("🔒 <b>What is staking?</b> You lock your carrots for 30, 90, or 365 days and earn extra CARROT tokens as a reward. Longer lock = higher APY (annual percentage yield). It's like putting money in a savings account.", "#4caf50") +
        tip("⚠️ <b>When to stake:</b> Only stake carrots you don't need for upgrades in the next few days. If you exit early, you lose all the yield. Recommended: stake any balance over 5,000 carrots that you won't need soon.", "#ff9900") +
        tip("📍 <b>Where to stake:</b> Go to Farm tab → Stake section. Choose your lock period and amount.", "#888")
    )

    # ── SECTION 9: Farm Guide ─────────────────────────────────────────────
    farm_hrs = f"{farm_hours_left:.1f} hrs" if farm_hours_left else "N/A"
    farm_content = (
        row("Farm yield", f"{farm_cps:.4f} carrots/sec ({farm_cps*3600:.1f}/hr)") +
        row("Time to 8hr cap", farm_hrs) +
        tip("🌾 <b>What is the farm?</b> The Carrot Farm makes carrots automatically even while you're offline. Buy Farm Level 1 from the Farm tab. It's one of the best investments in the game.", "#4caf50") +
        tip("⏰ <b>8-hour cap:</b> The farm stops storing carrots after 8 hours offline. You need to log in at least once every 8 hours to collect and keep earning. Mantis Pro will email you before the cap hits.", "#ff9900") +
        tip("📈 <b>Upgrade the farm:</b> Higher farm levels = more carrots per second. After buying Farm Level 1, alternate between stat upgrades and farm upgrades.", "#888")
    )

    # ── SECTION 10: Creator Program ───────────────────────────────────────
    creator_content = (
        tip("🎬 <b>What is the Creator Program?</b> If you make content about Bunny Button — posts on X, videos, clips, guides — you can submit it and earn carrot crates, carrot tokens, or unique creator-only items.", "#4caf50") +
        tip("📝 <b>How to submit:</b> Go to bunnybutton.xyz/creator-program → connect wallet → link X and Discord → submit your content URL with a short note explaining what it is.", "#888") +
        tip("✅ <b>What counts:</b> X posts, threads, videos, stream clips, memes, guides, or anything useful about Bunny Button. The team reviews manually and decides rewards.", "#888")
    )

    # ── SECTION 11: Quest Progress ────────────────────────────────────────
    QUEST_HOW = {
        "First Hop": "press the button once",
        "Carrot Pickpocket": "earn carrots by pressing the button",
        "Goblin Diplomacy": f"refer 2 friends (your code: {referral_code})",
        "Blue Check Toll Bridge": "connect X account from Wallet menu",
        "Ratcatchers": "join any party",
        "Five Bunnies Walk Into A Pub": "fill your party to 5 members",
        "Wizards Hate This Bunny": "reach 2.25x total steal — unlocks Moonburrow breed",
        "The Carrot Cartel": f"earn {max(0,25000-total_carrots):,.0f} more carrots — unlocks Jackalope breed",
        "Burnt Offerings": f"burn {max(0,50000-burned):,.0f} more carrots on upgrades",
        "Recipe For Disaster": "complete all 19 quests — cape + 20,000 carrots reward",
    }
    quest_rows = "".join(
        quest_row(q.get("name","?"), q.get("status","?"), QUEST_HOW.get(q.get("name","?"), "check in-game"))
        for q in quest_list
    )
    quest_content = (
        f'<div style="margin-bottom:8px;color:#aaa;font-size:13px;">{quests_done}/{len(quest_list)} complete</div>' +
        (quest_rows or '<div style="color:#888;font-size:13px;">No quest data yet — check in-game</div>')
    )

    # ── ASSEMBLE EMAIL ─────────────────────────────────────────────────────
    header = f"""
    <div style="background:#0f1a0f;padding:20px;text-align:center;border-bottom:1px solid #1a3a1a;">
      <span style="font-size:42px;">⚡🥕</span>
      <h1 style="color:#7fff7f;margin:8px 0 4px;font-size:22px;">Energy Full — Time to Play!</h1>
      <p style="color:#aaa;margin:0;font-size:13px;">Mantis Pro is watching your game 24/7 · Rank #{rank} · {total_carrots:,.0f} carrots earned</p>
    </div>"""

    cta = """
    <div style="padding:16px;text-align:center;">
      <a href="https://bunnybutton.xyz" style="background:#4caf50;color:#fff;padding:14px 40px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:16px;display:inline-block;">Open Bunny Button →</a>
    </div>"""

    footer = '<p style="color:#333;font-size:11px;text-align:center;padding:12px;margin:0;">Mantis Pro · Abstract Chain Agent · Checks every 30 minutes</p>'

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;background:#0a0f0a;border-radius:14px;overflow:hidden;border:1px solid #1a3a1a;">
      {header}
      <div style="padding:12px 14px;">
        {section("Your Stats Right Now", "📊", stats_content)}
        {section("What To Do RIGHT NOW", "🚨", now_content, "#2a3a00")}
        {section("Shop — How To Upgrade Yourself", "🛒", upgrade_content)}
        {section("Your Breed & Class", "🐰", breed_content)}
        {section("Gear — Capes, Boots & Hats", "🎽", gear_content)}
        {section("Party — Team Up For Bonus", "👥", party_content)}
        {section("Referrals — Earn From Friends", "💸", referral_content)}
        {section("Staking — Earn While Idle", "🔒", stake_content)}
        {section("Carrot Farm — Passive Income", "🌾", farm_content)}
        {section("Creator Program — Make Content, Earn Rewards", "🎬", creator_content)}
        {section("Quest Progress", "🎯", quest_content)}
      </div>
      {cta}
      {footer}
    </div>"""

    plain = f"Energy full {energy_cur:.0f}/{energy_max:.0f}. Go play: https://bunnybutton.xyz | Rank #{rank} | {total_carrots:,.0f} carrots"
    return html, plain


# ── MAIN SESSION ───────────────────────────────────────────────────────────

def bunny_session():
    separator("BUNNY BUTTON SESSION")
    cookie = os.environ.get("BUNNY_SESSION_COOKIE")
    bb = BunnyButton(session_cookie=cookie)

    try:
        separator("LEADERBOARD (Top 10)")
        print(bb.leaderboard_summary(limit=10))
    except Exception as e:
        print(f"Leaderboard error: {e}")

    try:
        separator("PRESALE STATUS")
        presale = bb.get_presale_status()
        print(f"  Active: {presale.get('active')} | Remaining: {presale.get('remainingAllocation',0):,.0f} CARROT")
    except Exception as e:
        print(f"Presale error: {e}")

    if not cookie:
        print("  No BUNNY_SESSION_COOKIE — skipping player checks")
        separator("SESSION COMPLETE")
        return

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

    separator("ENERGY")
    cur = energy.get("energyCurrent", 0)
    mx = energy.get("energyMax", 500)
    pct = energy.get("percentFull", 0)
    print(f"  {cur:.0f} / {mx:.0f}  ({pct}% full)")

    energy_full = energy.get("isFull", False)
    farm_warn = farm.get("capWarning", False)

    if energy_full:
        print("  ⚡ FULL — building full advisor email...")
        html, plain = build_email(player, inventory, energy, farm, quests_data, streaks, stake)
        send_email("⚡ Bunny Button — Energy Full! Your full game guide inside", html, plain)
    else:
        h = energy.get("hoursToFull")
        if h:
            print(f"  Full in ~{h:.1f} hours")

    separator("FARM")
    cps = farm.get("carrotsPerSecond", 0)
    print(f"  {cps:.4f}/sec ({cps*3600:.1f}/hr)")
    if farm_warn and not energy_full:
        print("  ⚠️ Cap warning — sending farm alert")
        html, plain = build_email(player, inventory, energy, farm, quests_data, streaks, stake)
        send_email("⚠️ Bunny Button — Claim Your Farm NOW Before Cap Hits!", html, plain)

    separator("QUICK STATS")
    print(f"  Breed: {player.get('bunnyBreed')} | Class: {player.get('bunnyClass')}")
    print(f"  Steal: {player.get('stealMultiplier')}x | Rank: #{player.get('rank')}")
    print(f"  Total: {player.get('totalCarrotsEarned',0):,.0f} | Balance: {player.get('carrotBalance',0):,.0f}")

    quest_list = quests_data.get("quests", []) if isinstance(quests_data, dict) else []
    done = sum(1 for q in quest_list if q.get("status") == "complete")
    print(f"  Quests: {done}/{len(quest_list)} | Streak: {streaks.get('streakLength',0)} days")

    separator("BUNNY SESSION COMPLETE")
