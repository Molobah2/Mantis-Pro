"""
bunny_button.py — Bunny Button companion module for Mantis Pro
--------------------------------------------------------------
Covers every public API endpoint documented at bunnybutton.xyz/docs.
Per-player endpoints require the user's session cookie; public ones need nothing.

Usage (drop into your Mantis Pro repo):
    from bunny_button import BunnyButton
    bb = BunnyButton()                             # public-only
    bb = BunnyButton(session_cookie="<cookie>")    # full access
"""

import time
import requests
from datetime import datetime, timezone

BASE_URL = "https://www.bunnybutton.xyz/api"

# Rate limits per docs:
#   Public reads     → 30 req/min per IP
#   ETH price        → 120 req/min per IP
#   Session reads    → 120 req/min per wallet


class BunnyButton:
    def __init__(self, session_cookie: str = None):
        """
        session_cookie: value of the __Host-bunny-button-auth cookie
                        from a signed-in bunnybutton.xyz browser session.
                        Leave None to use public endpoints only.
        """
        self.session_cookie = session_cookie
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if session_cookie:
            self.session.cookies.set(
                "__Host-bunny-button-auth",
                session_cookie,
                domain="www.bunnybutton.xyz",
            )

    # ── helpers ────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> dict:
        url = f"{BASE_URL}{path}"
        resp = self.session.get(url, params=params, timeout=10)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            raise RateLimitError(f"Rate limited. Retry after {retry_after}s.", retry_after)
        resp.raise_for_status()
        return resp.json()

    def _require_session(self):
        if not self.session_cookie:
            raise AuthError("This endpoint requires a session cookie. Pass session_cookie= to BunnyButton().")

    # ── PUBLIC ENDPOINTS (no auth) ─────────────────────────────────────────

    def get_leaderboard(self, limit: int = 25) -> list[dict]:
        """Top wallets ranked by total carrots earned. limit 1–100."""
        limit = max(1, min(100, limit))
        data = self._get("/leaderboard", params={"limit": limit})
        return data.get("leaderboard", [])

    def get_eth_price(self) -> dict:
        """Cached ETH/USD spot price. Returns {ethUsd, fetchedAt}."""
        return self._get("/eth-price")

    def get_presale_status(self, wallet: str = None) -> dict:
        """Presale totals, allocation, claim flag. Optional wallet for per-wallet contribution."""
        params = {}
        if wallet:
            params["wallet"] = wallet
        return self._get("/presale/status", params=params)

    def get_party_list(self) -> dict:
        """Public party directory. If session cookie present, also returns own party state."""
        return self._get("/party/list")

    # ── PER-PLAYER ENDPOINTS (session cookie required) ─────────────────────

    def get_player(self) -> dict:
        """Full player profile: breed, class, balances, energy, levels, multipliers, rank."""
        self._require_session()
        return self._get("/player").get("player", {})

    def get_inventory(self) -> dict:
        """Equipped gear + all owned items + aggregate stat bonuses."""
        self._require_session()
        return self._get("/inventory")

    def get_quests(self) -> dict:
        """Quest summary with status and reward state (20 quests)."""
        self._require_session()
        return self._get("/quests")

    def get_streaks(self) -> dict:
        """Daily login streak length and milestone progress."""
        self._require_session()
        return self._get("/streaks")

    def get_farm(self) -> dict:
        """Farm level, carrots-per-second, accrued offline yield, 8h cap info."""
        self._require_session()
        return self._get("/farm")

    def get_stake(self) -> dict:
        """Active stakes plus global stake stats (total locked, avg APY)."""
        self._require_session()
        return self._get("/stake")

    def get_referral(self) -> dict:
        """Referral code, referred-by wallet, daily earnings, referee count."""
        self._require_session()
        return self._get("/referral")

    def get_crate_history(self) -> dict:
        """Recent Carrot Crate openings: rarity, slot, archetype, value, timestamp."""
        self._require_session()
        return self._get("/crate/history")

    # ── COMPUTED HELPERS ────────────────────────────────────────────────────

    def energy_status(self) -> dict:
        """
        Returns a summary of current energy state and time until full.
        Requires session cookie.
        """
        player = self.get_player()
        current = player.get("energyCurrent", 0)
        max_e = player.get("energyMax", 1)
        regen = player.get("energyRegenPerHour", 0)

        missing = max_e - current
        hours_to_full = (missing / regen) if regen > 0 else None
        pct = round((current / max_e) * 100, 1)

        return {
            "energyCurrent": current,
            "energyMax": max_e,
            "energyRegenPerHour": regen,
            "percentFull": pct,
            "missingEnergy": missing,
            "hoursToFull": round(hours_to_full, 2) if hours_to_full is not None else None,
            "isFull": current >= max_e,
        }

    def farm_cap_warning(self) -> dict:
        """
        Returns farm state and whether you're approaching the 8-hour offline cap.
        Requires session cookie.
        """
        farm = self.get_farm()
        accrued = farm.get("accruedOfflineCarrots", 0)
        cap = farm.get("offlineCapCarrots", None)
        cps = farm.get("carrotsPerSecond", 0)

        hours_to_cap = None
        if cap and cps and cps > 0:
            remaining_cap = cap - accrued
            hours_to_cap = (remaining_cap / cps) / 3600 if remaining_cap > 0 else 0

        return {
            **farm,
            "hoursToCapFull": round(hours_to_cap, 2) if hours_to_cap is not None else None,
            "capWarning": hours_to_cap is not None and hours_to_cap < 1,
        }

    def effective_stats(self) -> dict:
        """
        Combines player breed/class stats with equipped gear bonuses for a full picture.
        Requires session cookie.
        """
        player = self.get_player()
        inventory = self.get_inventory()
        gear_bonuses = inventory.get("aggregateBonuses", {})

        return {
            "breed": player.get("bunnyBreed"),
            "bunnyClass": player.get("bunnyClass"),
            "baseSteal": player.get("stealMultiplier", 0),
            "gearStealBonus": gear_bonuses.get("stealBonus", 0),
            "effectiveSteal": round(
                player.get("stealMultiplier", 0) + gear_bonuses.get("stealBonus", 0), 4
            ),
            "baseEnergyMax": player.get("energyMax", 0),
            "gearEnergyBonus": gear_bonuses.get("maxEnergyBonus", 0),
            "effectiveEnergyMax": player.get("energyMax", 0) + gear_bonuses.get("maxEnergyBonus", 0),
            "baseRegen": player.get("energyRegenPerHour", 0),
            "gearRegenBonus": gear_bonuses.get("regenBonus", 0),
            "effectiveRegen": round(
                player.get("energyRegenPerHour", 0) + gear_bonuses.get("regenBonus", 0), 2
            ),
            "rank": player.get("rank"),
            "totalCarrotsEarned": player.get("totalCarrotsEarned", 0),
            "carrotBalance": player.get("carrotBalance", 0),
        }

    def full_report(self) -> dict:
        """
        Pulls all available data for the signed-in player and returns a single dict.
        Requires session cookie.
        """
        self._require_session()
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "player": self.get_player(),
            "inventory": self.get_inventory(),
            "farm": self.get_farm(),
            "stake": self.get_stake(),
            "quests": self.get_quests(),
            "streaks": self.get_streaks(),
            "referral": self.get_referral(),
            "energy": self.energy_status(),
            "farmCapWarning": self.farm_cap_warning(),
            "effectiveStats": self.effective_stats(),
        }
        return report

    def leaderboard_summary(self, limit: int = 10) -> str:
        """
        Returns a formatted leaderboard string, good for logging or agent reports.
        Public endpoint — no auth needed.
        """
        board = self.get_leaderboard(limit=limit)
        eth = self.get_eth_price()
        eth_usd = eth.get("ethUsd", 0)

        lines = [f"🥕 Bunny Button Top {len(board)} — ETH/USD: ${eth_usd:,.2f}\n"]
        for entry in board:
            name = entry.get("xHandle") or entry.get("walletAddress", "???")[:10] + "..."
            carrots = entry.get("totalCarrotsEarned", 0)
            rank = entry.get("rank", "?")
            breed = entry.get("bunnyBreed") or "?"
            lines.append(f"#{rank:>3}  {name:<20}  {carrots:>12,.0f} carrots  [{breed}]")

        return "\n".join(lines)


# ── EXCEPTIONS ─────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after

class AuthError(Exception):
    pass
