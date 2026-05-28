"""
bunny_agent.py — Bunny Button session logic for Mantis Pro
----------------------------------------------------------
This runs inside the existing Mantis Pro agent loop.

Add to agent.py in the run_agent() function:
    from bunny_agent import bunny_session
    bunny_session()

It will:
  - Check energy % and log when to play
  - Warn when farm cap is approaching (< 1hr left)
  - Report your current rank and carrot balance
  - Log effective stats (breed + class + gear)
  - Summarise quest progress
  - Log top 10 leaderboard

All read-only. No actions taken — as per the docs.
"""

import os
import time
from bunny_button import BunnyButton, RateLimitError, AuthError


def separator(label=""):
    print(f"\n{'─' * 10} {label} {'─' * 10}")


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

    # ── Energy status ───────────────────────────────────────────────────────
    try:
        separator("ENERGY")
        energy = bb.energy_status()
        print(f"  {energy['energyCurrent']:.0f} / {energy['energyMax']:.0f}  ({energy['percentFull']}% full)")
        if energy["isFull"]:
            print("  ⚡ Energy is FULL — time to go play!")
        else:
            h = energy["hoursToFull"]
            if h is not None:
                print(f"  Full in ~{h:.1f} hours")
    except (AuthError, Exception) as e:
        print(f"Energy check error: {e}")

    # ── Farm cap warning ───────────────────────────────────────────────────
    try:
        separator("CARROT FARM")
        farm = bb.farm_cap_warning()
        cps = farm.get("carrotsPerSecond", 0)
        print(f"  Yield:       {cps:.4f} carrots/sec  ({cps * 3600:.1f}/hr)")
        if farm.get("capWarning"):
            print("  ⚠️  FARM CAP WARNING — less than 1hr until 8h offline cap hits!")
            print("  Log in to the game and claim your farm carrots now.")
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
