#!/usr/bin/env python3
"""
Litany Mesh Auto-Claimer
========================
Automatically claims territory cells in the Litany Mesh using EIP-191 signed
messages posted to the Litany API. No gas required — pure off-chain signing.

Quick start:
  export AGW_OWNER_PRIVATE_KEY=0x...
  python mesh_claimer.py

Full options (env vars):
  AGW_OWNER_PRIVATE_KEY  required  Hex private key for the EOA signer
  MESH_TOKEN_IDS         optional  Comma-separated Litany Card token IDs
                                   (overrides session-based lookup)
  MESH_DRY_RUN           optional  Set to "true" to simulate without posting

How it works:
  1. Issues a 15-minute Litany session (EIP-191 signed message)
  2. Calls /wallet/available-claims to get today's usable token IDs
  3. Fetches the full mesh map and current territory
  4. Picks the best adjacent open cells (ranked by faction contiguity)
  5. Signs a canonical EIP-191 claim message and POSTs to /api/mesh/claim
  6. Handles cooldowns, rate limits, and token-already-used errors

Prerequisites:
  pip install eth-account requests python-dotenv
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

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────

API_BASE    = "https://litany.gg/api/mesh"

COOLDOWN_S  = 11      # API enforces 10 s; add 1 s buffer
BATCH_MAX   = 10      # API hard cap per signed write
DAILY_MAX   = 10      # API hard cap per UTC day per wallet
BACKOFF_MAX = 30      # Max exponential-backoff sleep in seconds

# Pointy-top axial hex neighbor offsets
HEX_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str, indent: int = 0):
    ts = datetime.datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {'  ' * indent}{msg}")

# ── Hex Grid ──────────────────────────────────────────────────────────────────

def hex_neighbors(q: int, r: int) -> list[tuple[int, int]]:
    return [(q + dq, r + dr) for dq, dr in HEX_DIRS]

# ── Litany Session + Token Lookup ─────────────────────────────────────────────

def issue_session(account, address: str):
    """
    Trade an EIP-191 personal_sign for a 15-minute Litany session JWT.
    Returns the response cookies on success, None on failure.

    Canonical message format (from Litany docs):
      Litany Protocol — The Mesh
      Action: session_issue
      Wallet: <lowercase>
      Nonce: <32 hex chars>
      Date: <YYYY-MM-DDTHH:MM:SSZ>
    """
    nonce   = secrets.token_hex(16)
    date    = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    message = (
        "Litany Protocol — The Mesh\n"
        "Action: session_issue\n"
        f"Wallet: {address.lower()}\n"
        f"Nonce: {nonce}\n"
        f"Date: {date}"
    )
    signed  = account.sign_message(encode_defunct(text=message))
    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    try:
        r = requests.post(
            f"{API_BASE}/session/issue",
            json={
                "wallet":    address.lower(),
                "nonce":     nonce,
                "date":      date,
                "signature": sig_hex,
                "message":   message,
            },
            timeout=15,
        )
        if r.ok:
            log("Session issued OK.", indent=1)
            return r.cookies
        log(f"[warn] Session issue returned {r.status_code}: {r.text[:300]}", indent=1)
        return None
    except Exception as e:
        log(f"[warn] Session issue error: {e}", indent=1)
        return None


def get_available_claims(cookies) -> list[int]:
    """
    Fetch token IDs available for claiming today (requires active session).
    Handles various possible response shapes from the API.
    """
    r = requests.get(
        f"{API_BASE}/wallet/available-claims",
        cookies=cookies,
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    data = body.get("data", body)

    if isinstance(data, list):
        return [int(x) for x in data]
    if isinstance(data, dict):
        for key in ("token_ids", "available", "tokens", "available_token_ids"):
            val = data.get(key)
            if isinstance(val, list):
                return [int(x) for x in val]
    return []

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
    Build the canonical Litany Mesh claim message, sign with EIP-191
    personal_sign, and return the full POST body.

    TokenIds are sorted ascending in the message; CellIds follow the same
    positional order so the server can re-derive each token→cell pairing.
    """
    sorted_pairs       = sorted(pairs, key=lambda p: p[0])
    sorted_token_ids   = [p[0] for p in sorted_pairs]
    cell_ids_ordered   = [p[1] for p in sorted_pairs]

    nonce = secrets.token_hex(16)
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

    signed  = account.sign_message(encode_defunct(text=message))
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

    First claim: targets home_region cells for the faction (starter zone).
    Subsequent: ranks open cells adjacent to faction territory by the number
    of neighboring faction cells (higher = more contiguous).
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
        if cell.get("special_type") == "fortress" or cell.get("state") == "fortress":
            return False
        return True

    if is_first or not faction_qr:
        starters = [
            c for c in map_cells
            if is_capturable(c) and c.get("home_region") == my_faction
        ]
        if starters:
            return starters
        return [c for c in map_cells if is_capturable(c)]

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
        log("Complete faction classification at https://litany.gg", indent=1)
        return

    remaining = DAILY_MAX

    # ── Step 2: resolve token IDs ──────────────────────────────────────────────
    env_token_str = os.environ.get("MESH_TOKEN_IDS", "").strip()
    if env_token_str:
        token_ids = [int(t.strip()) for t in env_token_str.split(",") if t.strip()]
        log(f"Token IDs from MESH_TOKEN_IDS env var: {token_ids}", indent=1)
    else:
        log("Requesting available-claims via Litany session...", indent=1)
        cookies   = issue_session(account, address)
        token_ids = []
        if cookies is not None:
            try:
                token_ids = get_available_claims(cookies)
                log(f"Available token IDs from API: {token_ids}", indent=1)
            except Exception as e:
                log(f"[warn] available-claims fetch failed: {e}", indent=1)

        if not token_ids:
            log("[error] Could not determine token IDs.", indent=1)
            log("Set MESH_TOKEN_IDS=<id1>,<id2>,... in Railway env vars to fix this.", indent=1)
            log("Find your token IDs at litany.gg (open your card inventory).", indent=1)
            return

    if not token_ids:
        log("[error] No token IDs available for claiming today.", indent=1)
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
        territory   = get_territory(address)
        my_cell_ids = {c["id"] for c in territory.get("cells", [])}
    except Exception as e:
        log(f"[warn] Territory fetch failed ({e}), assuming zero cells.", indent=1)
        my_cell_ids = set()

    is_first = len(my_cell_ids) == 0
    log(f"Cells held: {len(my_cell_ids)} | First-claim: {is_first}", indent=1)

    # ── Step 4: claim loop ─────────────────────────────────────────────────────
    used_tokens:    set[int] = set()
    claimed_total:  int      = 0
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

        log(f"Batch: {len(pairs)} pair(s)", indent=1)
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
                payload = build_and_sign(account, address, pairs, is_first)
                continue

            if resp.status_code == 409:
                if error_code == "TOKEN_ALREADY_USED_TODAY":
                    log("Tokens already used today. Marking as consumed.", indent=2)
                    used_tokens.update(batch_tokens)
                    break
                if error_code == "BUDGET_EXCEEDED":
                    log("Daily budget fully exhausted.", indent=2)
                    remaining = 0
                    break
                if error_code == "NOT_ADJACENT":
                    log("Cells not adjacent to faction territory. Excluding batch.", indent=2)
                    excluded_cells.update(batch_cells)
                    break

            if resp.status_code == 401:
                log(f"[error] Auth/signature failure ({error_code}): {body}", indent=2)
                return

            log(f"[error] {resp.status_code} {error_code}: {body}", indent=2)
            break

    log(f"Cycle done. Claimed {claimed_total} cell(s) this cycle.")

# ── Daily Loop ────────────────────────────────────────────────────────────────

def seconds_until_next_utc_day() -> float:
    now      = datetime.datetime.utcnow()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=1, second=0, microsecond=0
    )
    return (tomorrow - now).total_seconds()

# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    private_key = os.environ.get("AGW_OWNER_PRIVATE_KEY", "").strip()
    if not private_key:
        print("Error: AGW_OWNER_PRIVATE_KEY environment variable is required.")
        sys.exit(1)

    account = Account.from_key(private_key)
    address = os.environ.get(
        "AGW_ADDRESS",
        "0x9d60f5906d43aa12b0496765ec202bf498e9cd1f",
    ).strip()
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
