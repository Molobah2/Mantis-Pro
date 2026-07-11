#!/usr/bin/env python3
"""
Litany Mesh Auto-Claimer
========================
Automatically claims territory cells in the Litany Mesh using EIP-191 signed
messages posted to the Litany API. No gas required — pure off-chain signing.

Quick start:
  export MESH_PRIVATE_KEY=0x...
  python mesh_claimer.py

Full options (env vars):
  MESH_PRIVATE_KEY   required  Hex private key for the signing wallet
  MESH_ADDRESS       optional  Wallet address (derived from key if omitted)
  MESH_TOKEN_IDS     optional  Comma-separated Litany Card token IDs to use
                               (skips on-chain lookup — useful if RPC is slow)
  MESH_DRY_RUN       optional  Set to "true" to simulate without posting
  MESH_LOOP          optional  Set to "true" to loop daily (default: run once)

How it works:
  1. Looks up your faction and today's remaining claim budget
  2. Enumerates your Litany Card token IDs on-chain (or from MESH_TOKEN_IDS)
  3. Fetches the full mesh map and your current territory
  4. Picks the best adjacent open cells (ranked by faction contiguity score)
  5. Signs a canonical EIP-191 message and POSTs to /api/mesh/claim
  6. Handles cooldowns, rate limits, and token-already-used errors automatically
  7. If MESH_LOOP=true, sleeps until next UTC midnight and repeats

Prerequisites:
  pip install eth-account web3 requests python-dotenv
"""

import os
import sys
import json
import time
import secrets
import datetime
import requests
from typing import Optional

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

API_BASE       = "https://litany.gg/api/mesh"
ABSTRACT_RPC   = "https://api.abs.xyz"
CARDS_CONTRACT = "0xd44abe71c312FCAf73cC20f7DF61C39A89C203eB"

COOLDOWN_S  = 11      # API enforces 10 s; add 1 s buffer
BATCH_MAX   = 10      # API hard cap per signed write
DAILY_MAX   = 10      # API hard cap per UTC day per wallet
BACKOFF_MAX = 30      # Max exponential-backoff sleep in seconds

# Pointy-top axial hex neighbor offsets
HEX_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]

ERC721_ABI = [
    {
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "index", "type": "uint256"},
        ],
        "name": "tokenOfOwnerByIndex",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str, indent: int = 0):
    ts = datetime.datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {'  ' * indent}{msg}")

# ── Hex Grid ──────────────────────────────────────────────────────────────────

def hex_neighbors(q: int, r: int) -> list[tuple[int, int]]:
    return [(q + dq, r + dr) for dq, dr in HEX_DIRS]

# ── On-chain Token Lookup ─────────────────────────────────────────────────────

def get_owned_tokens(address: str) -> list[int]:
    """
    Enumerate Litany Card token IDs owned by `address` via ERC-721 Enumerable.
    Falls back gracefully if the method is unavailable.
    """
    w3 = Web3(Web3.HTTPProvider(ABSTRACT_RPC))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CARDS_CONTRACT),
        abi=ERC721_ABI,
    )
    checksum = Web3.to_checksum_address(address)

    try:
        balance = contract.functions.balanceOf(checksum).call()
    except Exception as e:
        raise RuntimeError(f"balanceOf call failed: {e}")

    log(f"On-chain balance: {balance} Litany Cards", indent=1)
    tokens = []
    for i in range(balance):
        try:
            token_id = contract.functions.tokenOfOwnerByIndex(checksum, i).call()
            tokens.append(int(token_id))
        except Exception as e:
            log(f"[warn] tokenOfOwnerByIndex({i}) failed: {e}", indent=1)
    return tokens

# ── Litany Mesh API ───────────────────────────────────────────────────────────

def api_get(path: str) -> dict:
    r = requests.get(f"{API_BASE}{path}", timeout=30)
    r.raise_for_status()
    body = r.json()
    # All Litany API responses are wrapped: { "ok": true, "data": {...} }
    return body.get("data", body)

def get_wallet_info(address: str) -> dict:
    return api_get(f"/wallet/{address.lower()}")

def get_territory(address: str) -> dict:
    return api_get(f"/wallet/{address.lower()}/territory")

def get_map() -> dict:
    return api_get("/map")

# ── EIP-191 Signing ───────────────────────────────────────────────────────────

def build_and_sign(
    account,
    address: str,
    pairs: list[tuple[int, int]],  # [(token_id, cell_id), ...]
    is_first_claim: bool,
) -> dict:
    """
    Build the canonical Litany Mesh claim message, sign it with EIP-191
    personal_sign, and return the full POST body.

    Canonical format (from docs):
      Litany Protocol — The Mesh
      Action: claim
      Wallet: <lowercase>
      Nonce: <32 hex chars>
      Date: <YYYY-MM-DDTHH:MM:SSZ>
      TokenIds: <sorted ascending, comma-separated>
      CellIds: <in same order as sorted TokenIds>
      FirstClaim: true|false

    The message TokenIds are always sorted ascending. CellIds follow the same
    positional order so the server can re-derive each token→cell pairing.
    """
    # Sort pairs by token_id ascending so message is deterministic
    sorted_pairs = sorted(pairs, key=lambda p: p[0])
    sorted_token_ids = [p[0] for p in sorted_pairs]
    cell_ids_ordered = [p[1] for p in sorted_pairs]

    nonce = secrets.token_hex(16)   # 16 random bytes = 32 hex chars
    date  = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    message = (
        "Litany Protocol — The Mesh\n"
        "Action: claim\n"
        f"Wallet: {address.lower()}\n"
        f"Nonce: {nonce}\n"
        f"Date: {date}\n"
        f"TokenIds: {','.join(str(t) for t in sorted_token_ids)}\n"
        f"CellIds: {','.join(str(c) for c in cell_ids_ordered)}\n"
        f"FirstClaim: {'true' if is_first_claim else 'false'}"
    )

    signed = account.sign_message(encode_defunct(text=message))
    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    return {
        "wallet":         address.lower(),
        "nonce":          nonce,
        "date":           date,
        "signature":      sig_hex,
        "message":        message,
        "token_ids":      sorted_token_ids,
        "cell_ids":       cell_ids_ordered,
        "is_first_claim": is_first_claim,
    }

# ── Cell Selection Strategy ───────────────────────────────────────────────────

def find_best_cells(
    map_cells: list[dict],
    my_territory_ids: set[int],
    my_faction: str,
    is_first: bool,
    exclude_cell_ids: set[int],
) -> list[dict]:
    """
    Return open cells ranked best-first.

    For first claims: targets the home-region (capital starter zone) for the
    faction. These cells are pre-designated as claimable at game start.

    For subsequent claims: ranks open cells adjacent to any faction territory
    by the number of neighboring faction cells (higher = more contiguous,
    harder to break).

    Null-waste biome cells are excluded from targets (impassable).
    Home-region cells (the fortress ring itself) are also excluded — they're
    uncapturable per game rules.
    """
    cells_by_qr = {(c["q"], c["r"]): c for c in map_cells}

    faction_qr = {
        (c["q"], c["r"])
        for c in map_cells
        if c.get("faction") == my_faction and c.get("state") in ("held", "fortress")
    }

    def is_capturable(cell: dict) -> bool:
        if cell.get("state") != "open":
            return False
        if cell.get("biome") == "null_waste":
            return False
        if cell["id"] in exclude_cell_ids:
            return False
        # Home-region cells are fortress-protected and uncapturable
        if cell.get("special_type") == "fortress" or cell.get("state") == "fortress":
            return False
        return True

    if is_first or not faction_qr:
        # Look for cells explicitly tagged to our faction's starter zone
        starters = [
            c for c in map_cells
            if is_capturable(c) and c.get("home_region") == my_faction
        ]
        if starters:
            return starters
        # Fallback: any open non-null cell (shouldn't normally be needed)
        return [c for c in map_cells if is_capturable(c)]

    # Score candidates by faction contiguity
    scores: dict[int, dict] = {}
    for (q, r) in faction_qr:
        for nq, nr in hex_neighbors(q, r):
            cell = cells_by_qr.get((nq, nr))
            if cell and is_capturable(cell):
                cid = cell["id"]
                if cid not in scores:
                    scores[cid] = {"cell": cell, "score": 0}
                scores[cid]["score"] += 1

    return [v["cell"] for v in sorted(scores.values(), key=lambda x: -x["score"])]

# ── Claim Cycle ───────────────────────────────────────────────────────────────

def run_claim_cycle(account, address: str, dry_run: bool = False):
    log(f"=== Claim cycle start | wallet: {address} ===")

    # ── Step 1: wallet info ────────────────────────────────────────────────────
    try:
        info = get_wallet_info(address)
    except Exception as e:
        log(f"[error] Could not reach wallet info endpoint: {e}")
        return

    my_faction   = info.get("faction")
    total_claims = info.get("total_claims", 0)
    log(f"Faction: {my_faction or 'none'} | Total claims all-time: {total_claims}", indent=1)

    if not my_faction:
        log("[error] Wallet has no faction assigned.", indent=1)
        log("Complete faction classification at https://litany.gg/docs/agents/skills/mesh", indent=1)
        return

    # Daily budget enforced server-side; BUDGET_EXCEEDED error terminates the loop early
    remaining = DAILY_MAX

    # ── Step 2: token IDs ──────────────────────────────────────────────────────
    env_token_str = os.environ.get("MESH_TOKEN_IDS", "").strip()
    if env_token_str:
        token_ids = [int(t.strip()) for t in env_token_str.split(",") if t.strip()]
        log(f"Token IDs from MESH_TOKEN_IDS: {token_ids}", indent=1)
    else:
        log("Fetching owned token IDs on-chain...", indent=1)
        try:
            token_ids = get_owned_tokens(address)
        except Exception as e:
            log(f"[error] On-chain token lookup failed: {e}", indent=1)
            log("Set MESH_TOKEN_IDS=<id1>,<id2>,... to bypass on-chain lookup.", indent=1)
            return

    if not token_ids:
        log("[error] Wallet owns no Litany Cards. Cannot claim cells.", indent=1)
        return

    log(f"Available tokens: {token_ids}", indent=1)

    # ── Step 3: map + territory ────────────────────────────────────────────────
    log("Fetching mesh map...", indent=1)
    try:
        map_data  = get_map()
        map_cells = map_data.get("cells", [])
    except Exception as e:
        log(f"[error] Map fetch failed: {e}", indent=1)
        return

    log(f"Map cells: {len(map_cells)}", indent=1)

    log("Fetching my territory...", indent=1)
    try:
        territory    = get_territory(address)
        my_cell_ids  = {c["id"] for c in territory.get("cells", [])}
    except Exception as e:
        log(f"[warn] Territory fetch failed ({e}), assuming zero cells.", indent=1)
        my_cell_ids = set()

    is_first = len(my_cell_ids) == 0
    log(f"Cells held: {len(my_cell_ids)} | First-claim: {is_first}", indent=1)

    # ── Step 4: claim loop ─────────────────────────────────────────────────────
    used_tokens:   set[int] = set()
    claimed_total: int      = 0
    # Cells we've already targeted this cycle (avoid re-submitting rejected cells)
    excluded_cells: set[int] = set()

    while remaining > 0:
        avail_tokens = [t for t in token_ids if t not in used_tokens]
        if not avail_tokens:
            log("All tokens consumed for today.", indent=1)
            break

        target_cells = find_best_cells(
            map_cells, my_cell_ids, my_faction, is_first, excluded_cells
        )
        if not target_cells:
            log("[warn] No adjacent open cells found.", indent=1)
            break

        batch_size   = min(len(avail_tokens), len(target_cells), BATCH_MAX, remaining)
        batch_tokens = avail_tokens[:batch_size]
        batch_cells  = [c["id"] for c in target_cells[:batch_size]]
        pairs        = list(zip(batch_tokens, batch_cells))

        log(f"Batch: {len(pairs)} pairs", indent=1)
        for tok, cell in pairs:
            log(f"token {tok} → cell {cell}", indent=2)

        if dry_run:
            log("[DRY RUN] Skipping POST.", indent=2)
            used_tokens.update(batch_tokens)
            claimed_total += len(pairs)
            remaining     -= len(pairs)
            is_first       = False
            continue

        payload = build_and_sign(account, address, pairs, is_first)

        # Submit with retry
        backoff = 1
        while True:
            try:
                resp = requests.post(f"{API_BASE}/claim", json=payload, timeout=30)
            except Exception as e:
                log(f"Network error: {e}. Retrying in {backoff}s...", indent=2)
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue

            if resp.status_code == 200:
                try:
                    result = resp.json()
                except Exception:
                    result = resp.text
                log(f"[ok] Accepted: {json.dumps(result)}", indent=2)
                used_tokens.update(batch_tokens)
                claimed_total += len(pairs)
                remaining     -= len(pairs)
                is_first       = False
                if remaining > 0:
                    log(f"Cooldown {COOLDOWN_S}s...", indent=2)
                    time.sleep(COOLDOWN_S)
                break

            # Parse error body
            error_code = ""
            try:
                body = resp.json()
                error_code = body.get("error", body.get("code", ""))
            except Exception:
                body = resp.text

            if resp.status_code == 429 or error_code in ("COOLDOWN_ACTIVE", "RATE_LIMITED"):
                wait = COOLDOWN_S if error_code == "COOLDOWN_ACTIVE" else backoff
                log(f"Rate-limited ({error_code}). Waiting {wait}s...", indent=2)
                time.sleep(wait)
                backoff = min(backoff * 2, BACKOFF_MAX)
                # Re-sign so the timestamp stays within the ±120 s window
                payload = build_and_sign(account, address, pairs, is_first)
                continue

            if resp.status_code == 409:
                if error_code == "TOKEN_ALREADY_USED_TODAY":
                    log("Some tokens already used today. Marking as consumed.", indent=2)
                    used_tokens.update(batch_tokens)
                    break
                if error_code == "BUDGET_EXCEEDED":
                    log("Daily budget fully exhausted.", indent=2)
                    remaining = 0
                    break
                if error_code == "NOT_ADJACENT":
                    log("Cells not adjacent to faction territory. Excluding this batch.", indent=2)
                    excluded_cells.update(batch_cells)
                    break

            if resp.status_code == 401:
                log(f"[error] Auth/signature failure ({error_code}): {body}", indent=2)
                log("Check that wallet address matches the private key.", indent=2)
                return

            log(f"[error] {resp.status_code} {error_code}: {body}", indent=2)
            break

    log(f"Cycle done. Claimed {claimed_total} cell(s) this cycle.")

# ── Daily Loop ────────────────────────────────────────────────────────────────

def seconds_until_next_utc_day() -> float:
    now      = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=1, second=0, microsecond=0   # 1-minute buffer after midnight
    )
    return (tomorrow - now).total_seconds()

# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    private_key = os.environ.get("MESH_PRIVATE_KEY", "").strip()
    if not private_key:
        print("Error: MESH_PRIVATE_KEY environment variable is required.")
        print("  export MESH_PRIVATE_KEY=0x<your_private_key>")
        sys.exit(1)

    account = Account.from_key(private_key)
    address = os.environ.get("MESH_ADDRESS", account.address).strip()
    dry_run = os.environ.get("MESH_DRY_RUN", "false").lower() == "true"
    loop    = os.environ.get("MESH_LOOP", "false").lower() == "true"

    print("=" * 60)
    print("  Litany Mesh Auto-Claimer")
    print("=" * 60)
    print(f"  Wallet : {address}")
    print(f"  Dry run: {dry_run}")
    print(f"  Loop   : {loop}")
    print("=" * 60)
    print()

    run_claim_cycle(account, address, dry_run)

    if loop:
        while True:
            sleep_s = seconds_until_next_utc_day()
            log(f"Next run in {sleep_s / 3600:.1f} h (next UTC day).")
            time.sleep(sleep_s)
            run_claim_cycle(account, address, dry_run)


if __name__ == "__main__":
    main()
