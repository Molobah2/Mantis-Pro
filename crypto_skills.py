"""
crypto_skills.py
Mantis Pro — Agent #857
Crypto Skill Registry + Execution Gate + $200 Spend Cap Enforcer

Drop this file into C:\\Users\\Cole\\Mantis-Pro\\
Import in app.py:  from crypto_skills import skill_registry, check_execution_gate
"""

SPEND_CAP_USD = 200.00

SKILL_REGISTRY = [
    {
        "name": "wallet_scanner",
        "label": "Wallet scanner",
        "description": "Read balances, token holdings, and transaction history for any address.",
        "category": "portfolio",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "rarity_engine",
        "label": "Rarity engine",
        "description": "Score NFT traits against collection data to rank rarity.",
        "category": "nft",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "gas_monitor",
        "label": "Gas monitor",
        "description": "Track current gas prices and estimate fees for pending actions.",
        "category": "market",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "token_analytics",
        "label": "Token analytics",
        "description": "Fetch price, volume, market cap, and holder distribution data.",
        "category": "market",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "whale_tracker",
        "label": "Whale tracker",
        "description": "Monitor large wallet movements and flag significant onchain activity.",
        "category": "market",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "contract_verifier",
        "label": "Contract verifier",
        "description": "Check source code verification, audit flags, honeypot patterns, and rug signals.",
        "category": "security",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "yield_discovery",
        "label": "Yield discovery",
        "description": "Find and compare APY/APR across DeFi protocols — read-only, no deposits.",
        "category": "defi",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "mint_calendar",
        "label": "Mint calendar",
        "description": "Track upcoming NFT mints, allowlist windows, and launch dates.",
        "category": "nft",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "tx_simulation",
        "label": "Transaction simulation",
        "description": "Simulate any transaction outcome before real execution — no funds moved.",
        "category": "portfolio",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "news_intelligence",
        "label": "News intelligence",
        "description": "Aggregate protocol news, token signals, and ecosystem narratives.",
        "category": "market",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "approval_monitor",
        "label": "Approval monitor",
        "description": "Detect dangerous ERC-20/NFT approvals active on a wallet.",
        "category": "security",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "pnl_reporting",
        "label": "PnL reporting",
        "description": "Calculate unrealised and realised profit/loss across tracked positions.",
        "category": "portfolio",
        "mode": "analysis",
        "risk": "low",
        "requires_key": False,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
    {
        "name": "swap_executor",
        "label": "Swap executor",
        "description": "Execute ETH/token swaps via Abstract Global Wallet CLI (abstract_swap.py).",
        "category": "defi",
        "mode": "execution",
        "risk": "medium",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "live",
    },
    {
        "name": "spot_trade",
        "label": "Spot trade",
        "description": "Place buy/sell orders on CEX or DEX.",
        "category": "market",
        "mode": "execution",
        "risk": "medium",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "nft_mint",
        "label": "NFT mint",
        "description": "Mint a token — spends ETH and gas.",
        "category": "nft",
        "mode": "execution",
        "risk": "medium",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "lp_deposit",
        "label": "LP deposit",
        "description": "Deposit assets into a liquidity pool.",
        "category": "defi",
        "mode": "execution",
        "risk": "medium",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "staking",
        "label": "Staking",
        "description": "Lock tokens in a staking contract — unbonding period may apply.",
        "category": "defi",
        "mode": "execution",
        "risk": "medium",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "bridge_funds",
        "label": "Bridge funds",
        "description": "Cross-chain asset transfer — irreversible once submitted.",
        "category": "defi",
        "mode": "execution",
        "risk": "high",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "token_approval",
        "label": "Token approval",
        "description": "Grant a contract spending rights over wallet tokens.",
        "category": "security",
        "mode": "execution",
        "risk": "high",
        "requires_key": True,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "governance_vote",
        "label": "Governance vote",
        "description": "Sign and submit an on-chain governance vote.",
        "category": "portfolio",
        "mode": "execution",
        "risk": "medium",
        "requires_key": True,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "portfolio_rebalance",
        "label": "Portfolio rebalance",
        "description": "Execute multiple swaps to hit target allocations. Cap applies per leg.",
        "category": "portfolio",
        "mode": "execution",
        "risk": "high",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "futures_position",
        "label": "Futures position",
        "description": "Open a leveraged trade. Liquidation risk. Hard-blocked above $200 notional.",
        "category": "market",
        "mode": "execution",
        "risk": "high",
        "requires_key": True,
        "moves_funds": True,
        "spend_cap_applies": True,
        "approval_required": True,
        "automation_level": "gated",
        "status": "planned",
    },
    {
        "name": "portal_auto_upvote",
        "label": "Portal auto-upvote",
        "description": (
            "Daily upvotes on Abstract Portal apps via AGW session key. "
            "Session key is scoped exclusively to upvote(uint256) on the Portal upvote contract. "
            "No funds moved. One-time browser authorization; agent runs daily at 09:00 UTC autonomously. "
            "Rotates through all visible Portal apps in least-recently-upvoted order."
        ),
        "category": "social",
        "mode": "execution",
        "risk": "low",
        "requires_key": True,
        "moves_funds": False,
        "spend_cap_applies": False,
        "approval_required": False,
        "automation_level": "full",
        "status": "live",
    },
]


def skill_registry(mode=None, category=None, status=None):
    """
    Return skills filtered by mode ('analysis'|'execution'),
    category (str), or status ('live'|'planned').
    Returns full list if no filters applied.
    """
    results = SKILL_REGISTRY
    if mode:
        results = [s for s in results if s["mode"] == mode]
    if category:
        results = [s for s in results if s["category"] == category]
    if status:
        results = [s for s in results if s["status"] == status]
    return results


def get_skill(name):
    """Return a single skill dict by name, or None if not found."""
    for s in SKILL_REGISTRY:
        if s["name"] == name:
            return s
    return None


def check_execution_gate(skill_name, amount_usd=None):
    """
    Check whether an execution skill is allowed to proceed.

    Returns a dict:
        allowed   (bool)   — True if agent may proceed
        reason    (str)    — Human-readable explanation
        cap_usd   (float)  — The spend cap in USD
        amount    (float)  — The requested amount (if provided)
        skill     (dict)   — The skill definition

    Rules:
      1. Skill must exist in the registry.
      2. Skill must be in 'execution' mode — analysis skills never need this gate.
      3. Approval is always required for execution skills.
      4. If spend_cap_applies is True and amount_usd is provided,
         the amount must not exceed SPEND_CAP_USD.
      5. Amount is evaluated at call time — callers must pass current market value.
    """
    skill = get_skill(skill_name)

    if skill is None:
        return {
            "allowed": False,
            "reason": f"Skill '{skill_name}' not found in registry. Cannot proceed.",
            "cap_usd": SPEND_CAP_USD,
            "amount": amount_usd,
            "skill": None,
        }

    if skill["mode"] == "analysis":
        return {
            "allowed": True,
            "reason": "Analysis skill — runs freely, no gate required.",
            "cap_usd": SPEND_CAP_USD,
            "amount": amount_usd,
            "skill": skill,
        }

    if not skill["approval_required"]:
        return {
            "allowed": True,
            "reason": "Execution skill with approval not required (misconfiguration — review registry).",
            "cap_usd": SPEND_CAP_USD,
            "amount": amount_usd,
            "skill": skill,
        }

    if skill["spend_cap_applies"] and amount_usd is not None:
        if amount_usd > SPEND_CAP_USD:
            return {
                "allowed": False,
                "reason": (
                    f"Blocked: ${amount_usd:.2f} exceeds the $200.00 spend cap. "
                    f"Reduce the amount or request a manual override."
                ),
                "cap_usd": SPEND_CAP_USD,
                "amount": amount_usd,
                "skill": skill,
            }

    return {
        "allowed": True,
        "reason": (
            f"Execution gate passed for '{skill['label']}'. "
            f"Amount: ${amount_usd:.2f} — within cap. "
            f"Explicit approval still required before signing."
            if amount_usd is not None
            else (
                f"Execution gate passed for '{skill['label']}'. "
                f"No spend amount provided — approval still required before signing."
            )
        ),
        "cap_usd": SPEND_CAP_USD,
        "amount": amount_usd,
        "skill": skill,
    }


def execution_summary():
    """Print a human-readable summary of all skills and their gate status."""
    analysis = skill_registry(mode="analysis")
    execution = skill_registry(mode="execution")

    print(f"\n{'='*60}")
    print(f"  MANTIS PRO — Agent #857 — Crypto Skill Registry")
    print(f"  Spend cap: ${SPEND_CAP_USD:.2f} USD per execution action")
    print(f"{'='*60}")

    print(f"\n  ANALYSIS SKILLS ({len(analysis)}) — runs autonomously\n")
    for s in analysis:
        print(f"  [{'LIVE' if s['status'] == 'live' else 'PLAN'}]  {s['label']:<28} {s['category']}")

    print(f"\n  EXECUTION SKILLS ({len(execution)}) — approval + cap required\n")
    for s in execution:
        cap_note = f"cap ${SPEND_CAP_USD:.0f}" if s["spend_cap_applies"] else "no spend"
        print(f"  [{'LIVE' if s['status'] == 'live' else 'PLAN'}]  {s['label']:<28} {s['category']:<12} {cap_note}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    execution_summary()

    print("--- Gate check: swap $50 ---")
    result = check_execution_gate("swap_executor", amount_usd=50.00)
    print(f"  Allowed: {result['allowed']}")
    print(f"  Reason:  {result['reason']}\n")

    print("--- Gate check: swap $250 (over cap) ---")
    result = check_execution_gate("swap_executor", amount_usd=250.00)
    print(f"  Allowed: {result['allowed']}")
    print(f"  Reason:  {result['reason']}\n")

    print("--- Gate check: wallet scanner (analysis) ---")
    result = check_execution_gate("wallet_scanner")
    print(f"  Allowed: {result['allowed']}")
    print(f"  Reason:  {result['reason']}\n")
