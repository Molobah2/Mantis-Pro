"""
The OpenSea Auto-Mint tool's firing pipeline: arms a granted session-key
permission against a specific drop, then a periodic watcher tick fires a
real mintPublic() UserOperation the moment the drop's real on-chain public
stage goes live.

THIS IS THE MODULE THAT DECIDES WHEN TO SPEND REAL ETH. It doesn't build
the transaction itself (node_client.fire_mint / wallet-helper's
zerodevClient.ts does that, re-verifying price/fee-recipient against the
live chain immediately before submitting) — this module's job is making
sure a given arm request fires at the right time and fires EXACTLY ONCE.

Status lifecycle for an arm_requests row (see store.py):
    pending_schedule -> scheduled -> armed -> fired -> succeeded
                                                     -> armed (retry) or failed
    (any non-terminal state) -> cancelled (user-initiated)
    (any non-terminal state) -> expired (drop's public window closed first)
    (any non-terminal state) -> failed (grant revoked/expired before firing)
"""
import json
import logging
import sqlite3
import time

from wallet_crypto import decrypt_secret

from . import node_client, store

logger = logging.getLogger(__name__)

# Safety margin after a drop's real on-chain startTime before this is
# treated as actually live. Firing exactly at startTime risks a boundary
# race against slight clock drift between this process and the chain;
# ERC-4337 bundler simulation should also catch/reject a would-revert
# UserOp before ever broadcasting it, but this margin avoids relying on
# that as the only protection.
GO_LIVE_SAFETY_MARGIN_SECONDS = 3

# Bounded retries for a failed/reverted attempt (e.g. a transient RPC error,
# or firing one tick before the chain's clock agrees startTime has passed).
# Not retried indefinitely — a real, persistent failure (sold out, quantity
# already exhausted) would otherwise retry forever.
MAX_FIRE_ATTEMPTS = 3

# How stale a signed arm/cancel message is allowed to be before it's
# rejected — bounds the replay window for a captured signature (e.g. from a
# logged request, a browser history entry, or a compromised proxy) rather
# than letting it be replayed indefinitely.
SIGNATURE_MAX_AGE_SECONDS = 300


def build_arm_message(collection_slug: str, quantity: int, max_price_wei: str, timestamp: int) -> str:
    """The exact message an owner's wallet must sign to authorize arming a
    drop — both the frontend (to produce the signature) and this function
    (to verify it) must build this identically, since verification recovers
    the signer address from the message text itself. Changing this format
    is a breaking change for any in-flight signed request."""
    return (
        "Mantis Pro OpenSea Auto-Mint\n"
        "action: arm\n"
        f"collectionSlug: {collection_slug}\n"
        f"quantity: {quantity}\n"
        f"maxPriceWei: {max_price_wei}\n"
        f"timestamp: {timestamp}"
    )


def build_cancel_message(arm_id: int, timestamp: int) -> str:
    """The exact message an owner's wallet must sign to authorize
    cancelling an arm request — see build_arm_message's docstring for why
    the frontend and backend must construct this identically."""
    return (
        "Mantis Pro OpenSea Auto-Mint\n"
        "action: cancel\n"
        f"armId: {arm_id}\n"
        f"timestamp: {timestamp}"
    )


def is_signature_timestamp_fresh(timestamp: float, now: float | None = None) -> bool:
    """Bounds the replay window for a signed arm/cancel message. Rejects
    both stale timestamps AND ones too far in the future (a clock-skew
    guard against a signature pre-dated to outlive this check)."""
    now = now if now is not None else time.time()
    return abs(now - timestamp) <= SIGNATURE_MAX_AGE_SECONDS


def arm_drop(owner_address: str, collection_slug: str, quantity: int, max_price_wei: str) -> dict:
    """
    Arms a drop for auto-firing. Validates that the owner has an active
    session grant that actually covers this drop's contract, and that the
    requested quantity/total price stay within what that grant authorizes
    — arming can never request more than the already-granted on-chain
    permission itself allows (the permission's own CallPolicy enforces this
    independently on-chain regardless, but rejecting it here up front gives
    a clear error instead of a guaranteed-to-revert arm).

    Takes collection_slug rather than the drop's internal DB id — that's
    what the frontend actually has (drops.py's to_display_dict deliberately
    never exposes the internal id).

    Returns {"armId": int} on success, or {"error": str} on a validation
    failure — mirrors this feature's routes.py convention of keeping
    exception handling in the route layer thin; expected validation
    failures are returned as data, not raised.
    """
    owner = owner_address.lower()

    drop = store.get_tracked_drop_by_slug(collection_slug)
    if not drop:
        return {"error": "Unknown drop"}
    if not drop.get("contract_address"):
        return {"error": "This drop's mint contract has not been discovered yet"}

    grant = store.get_active_session_grant(owner)
    if not grant:
        return {"error": "No active minting permission for this wallet — grant one first"}

    try:
        permission_config = json.loads(grant["permission_config"])
        allowed_targets = json.loads(grant["allowed_targets"])
    except (TypeError, ValueError):
        return {"error": "Stored permission is malformed"}

    if drop["contract_address"] not in allowed_targets:
        return {"error": "Your active permission does not cover this drop's contract"}

    granted_max_quantity = permission_config.get("maxQuantity")
    if not isinstance(granted_max_quantity, int) or quantity > granted_max_quantity:
        return {"error": f"Quantity exceeds what your permission allows (max {granted_max_quantity})"}

    try:
        requested_total_wei = int(max_price_wei) * quantity
        grant_cap_wei = int(grant["value_cap_wei"])
    except (TypeError, ValueError):
        return {"error": "Invalid price"}
    if requested_total_wei > grant_cap_wei:
        return {"error": "Requested total price exceeds your permission's spend cap"}

    # Only one active (non-terminal) arm request per (owner, drop) — arming
    # twice for the same drop would otherwise risk two independent watcher
    # ticks each firing their own real, duplicate mint. This upfront check
    # is just an early-exit for a clean error message; it is NOT the actual
    # safety guarantee (two concurrent calls could both pass it before
    # either inserts — a real TOCTOU race). The actual guarantee is
    # idx_arm_requests_active_owner_drop, a partial unique index enforced
    # by SQLite itself — see store.create_arm_request / _init_schema.
    existing = store.get_active_arm_request_for_drop(owner, drop["id"])
    if existing:
        return {"error": "This drop is already armed", "armId": existing["id"]}

    try:
        arm_id = store.create_arm_request(store.ArmRequestInput(
            owner_address=owner,
            drop_id=drop["id"],
            session_grant_id=grant["id"],
            quantity=quantity,
            max_price_wei=max_price_wei,
            go_live_at=None,  # discovered by the watcher via the real on-chain window, not guessed here
        ))
    except sqlite3.IntegrityError:
        # Lost the race against a concurrent arm_drop call for the same
        # (owner, drop) — the other call's row is now the real one.
        existing = store.get_active_arm_request_for_drop(owner, drop["id"])
        return {
            "error": "This drop is already armed",
            "armId": existing["id"] if existing else None,
        }

    return {"armId": arm_id}


def cancel_arm(arm_id: int, owner_address: str) -> dict:
    """Cancels an arm request — only while it hasn't fired yet, and only
    for the owner who created it. Returns {"cancelled": bool} or
    {"error": str}."""
    arm = store.get_arm_request(arm_id)
    if not arm or arm["owner_address"] != owner_address.lower():
        return {"error": "Arm request not found"}

    cancelled = store.try_cancel_arm_request(arm_id)
    if not cancelled:
        return {"error": f"Cannot cancel — already {arm['status']}"}
    return {"cancelled": True}


def get_arm_status_for_drop(owner_address: str, collection_slug: str) -> dict | None:
    """The owner's active (non-terminal) arm request for a drop plus its
    mint attempts, looked up by collection_slug — or None if this owner
    has no active arm request for it."""
    drop = store.get_tracked_drop_by_slug(collection_slug)
    if not drop:
        return None
    arm = store.get_active_arm_request_for_drop(owner_address.lower(), drop["id"])
    if not arm:
        return None
    return {"arm": arm, "attempts": store.get_mint_attempts(arm["id"])}


def check_and_fire_armed_requests() -> None:
    """
    The watcher tick — called frequently (every ~10s) by an APScheduler
    job. For every non-terminal arm request, checks the drop's real
    on-chain public mint window and fires the moment it's live.

    Never raises: one arm request's failure must not stop the rest of the
    batch from being checked on this tick.
    """
    now = time.time()
    for arm in store.get_pending_arm_requests():
        try:
            _check_and_fire_one(arm, now)
        except Exception as e:
            logger.warning("[firing] arm request %d: unexpected error: %s", arm["id"], e)


def _check_and_fire_one(arm: dict, now: float) -> None:
    drop = store.get_tracked_drop(arm["drop_id"])
    if not drop or not drop.get("contract_address"):
        return  # nothing to fire against yet — try again next tick

    grant = store.get_session_grant(arm["session_grant_id"])
    if not grant or grant["revoked"] or grant["expires_at"] <= now:
        logger.info("[firing] arm request %d: grant no longer valid, failing", arm["id"])
        store.update_arm_request_status(arm["id"], "failed")
        return

    # Re-check the drop's contract against the grant's allowed targets at
    # FIRE time, not just at arm time — a periodic re-scrape (every 15 min,
    # see agent.py) overwrites tracked_drops.contract_address, and arming
    # only validated this once, when the arm request was first created. The
    # on-chain CallPolicy would also reject a mismatched contract, but that
    # surfaces as a wasted on-chain revert (consuming a retry + real gas)
    # instead of a clean, immediate, off-chain failure.
    try:
        allowed_targets = json.loads(grant["allowed_targets"])
    except (TypeError, ValueError):
        allowed_targets = []
    if drop["contract_address"] not in allowed_targets:
        logger.warning(
            "[firing] arm request %d: drop contract %s no longer covered by grant %d, failing",
            arm["id"], drop["contract_address"], grant["id"],
        )
        store.update_arm_request_status(arm["id"], "failed")
        return

    try:
        window = node_client.get_public_drop_window(drop["contract_address"])
    except RuntimeError as e:
        logger.warning("[firing] arm request %d: could not read drop window: %s", arm["id"], e)
        return  # transient — try again next tick

    if not window:
        return  # no public stage configured on-chain yet — wait

    if now >= window["endTime"]:
        logger.info("[firing] arm request %d: drop's public window has ended, expiring", arm["id"])
        store.update_arm_request_status(arm["id"], "expired")
        return

    if now < window["startTime"] + GO_LIVE_SAFETY_MARGIN_SECONDS:
        if arm["status"] != "scheduled":
            store.update_arm_request_status(arm["id"], "scheduled")
        return

    store.update_arm_request_status(arm["id"], "armed")
    _fire_one(arm, drop, grant)


def _fire_one(arm: dict, drop: dict, grant: dict) -> None:
    """Attempts to fire exactly once for one arm request. The atomic
    'armed' -> 'fired' claim (store.try_claim_arm_request) is the sole
    guard against double-firing — if the claim fails, another tick (or a
    concurrent invocation) already took it, and this is a safe no-op."""
    if not store.try_claim_arm_request(arm["id"]):
        return

    try:
        decrypted_approval = decrypt_secret(grant["encrypted_session_key"])
    except ValueError as e:
        logger.error("[firing] arm request %d: could not decrypt session key: %s", arm["id"], e)
        store.record_mint_attempt(store.MintAttemptInput(
            arm_request_id=arm["id"], tx_hash=None, user_op_hash=None,
            status="error", error_message="Could not decrypt session key",
            gas_used=None, block_number=None,
        ))
        store.update_arm_request_status(arm["id"], "failed")
        return

    try:
        result = node_client.fire_mint(
            decrypted_approval, drop["contract_address"], grant["smart_account_address"],
            arm["quantity"], arm["max_price_wei"],
        )
    except RuntimeError as e:
        # A "timed out" RuntimeError at this layer means the Node helper's
        # own internal receipt-wait (up to 60s) plus the HTTP round-trip may
        # have still been mid-flight when this call's own 90s timeout fired
        # — a real UserOperation could genuinely have been submitted, same
        # as the "ambiguous" case node_client.fire_mint itself can return.
        # Any OTHER RuntimeError (connection refused, an immediate error
        # response) happens before Node ever calls sendUserOperation, so
        # it's a safe, definite failure — nothing was ever broadcast.
        is_ambiguous = "timed out" in str(e).lower()
        result = {
            "success": False, "ambiguous": is_ambiguous, "userOpHash": "", "txHash": None,
            "blockNumber": None, "gasUsed": None, "error": str(e),
        }

    block_number = result.get("blockNumber")
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm["id"],
        tx_hash=result.get("txHash"),
        user_op_hash=result.get("userOpHash") or None,
        status="success" if result.get("success") else "failed",
        error_message=None if result.get("success") else result.get("error"),
        gas_used=result.get("gasUsed"),
        block_number=int(block_number) if block_number else None,
    ))

    if result.get("success"):
        logger.info("[firing] arm request %d: mint succeeded, tx=%s", arm["id"], result.get("txHash"))
        store.update_arm_request_status(arm["id"], "succeeded")
        return

    if result.get("ambiguous"):
        # NEVER retry an ambiguous outcome — a prior attempt may already
        # have landed on-chain, and resubmitting risks a genuine duplicate
        # spend/mint. Fail immediately regardless of MAX_FIRE_ATTEMPTS; the
        # recorded userOpHash (see mint_attempts above) is what a human
        # would use to check the real outcome manually.
        logger.error(
            "[firing] arm request %d: AMBIGUOUS outcome (userOpHash=%s) — a mint may already "
            "have gone through. Failing without retry to avoid a duplicate submission.",
            arm["id"], result.get("userOpHash"),
        )
        store.update_arm_request_status(arm["id"], "failed")
        return

    logger.warning("[firing] arm request %d: attempt failed: %s", arm["id"], result.get("error"))
    attempts = store.get_mint_attempts(arm["id"])
    if len(attempts) >= MAX_FIRE_ATTEMPTS:
        store.update_arm_request_status(arm["id"], "failed")
    else:
        # Back to 'armed' so the next tick (still within the drop's public
        # window, checked by _check_and_fire_one before ever reaching here)
        # gets another attempt.
        store.update_arm_request_status(arm["id"], "armed")
