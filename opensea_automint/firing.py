"""
The OpenSea Auto-Mint tool's firing pipeline: arms a granted session key
against a specific drop, then fires a real, directly-signed mintPublic()
transaction from that session key the moment the drop's real on-chain
public stage goes live.

A session key here is a plain, fundable EOA — NOT an ERC-4337 smart-account
session (that was this project's original design; replaced for raw firing
speed, at the user's explicit, informed choice: no bundler hop, but also no
on-chain CallPolicy backstop). That means there is no on-chain enforcement
of quantity/price/target-contract limits anymore — the checks in arm_drop
and _check_and_fire_one/node_client.fire_mint (which re-verifies price
against the live chain immediately before signing) ARE the only safety net.
Fund a session key with only what you're willing to have it spend.

THIS IS THE MODULE THAT DECIDES WHEN TO SPEND REAL ETH. It doesn't build
the transaction itself (node_client.fire_mint / wallet-helper's Node
process does that) — this module's job is making sure a given arm request
fires at the right time and fires EXACTLY ONCE.

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
import threading
import time

from wallet_crypto import decrypt_secret

from . import collection_details, node_client, opensea_session, store

logger = logging.getLogger(__name__)

# Safety margin after a drop's real on-chain startTime before this is
# treated as actually live. Kept small — precision no longer depends on
# the scheduler's tick interval (see the countdown-thread mechanism below),
# so this only needs to cover genuine clock drift between this process and
# the chain, not the old tick-interval slop. Firing a hair too early wastes
# a retry (ERC-4337 bundler simulation rejects a would-revert UserOp before
# ever broadcasting it, so nothing is spent) — biasing slightly late avoids
# that wasted round-trip, which would cost far more than this margin does.
GO_LIVE_SAFETY_MARGIN_SECONDS = 1

# How long before a drop's real on-chain startTime this module switches
# from "check on the next regular scheduler tick" to "spawn a dedicated
# thread that busy-waits on the local clock and fires the instant go-live
# passes." This is what actually delivers speed: the regular tick interval
# (agent.py) only needs to be tight enough to catch an arm request BEFORE
# it enters this window — actual firing precision comes from the countdown
# thread's tight sleep loop, decoupled entirely from the tick cadence.
CRITICAL_WINDOW_SECONDS = 20

# How finely the countdown thread polls the clock while waiting — small
# enough that firing latency past the real go-live moment is negligible
# compared to network/bundler latency, large enough not to busy-loop the CPU.
COUNTDOWN_POLL_INTERVAL_SECONDS = 0.05

# Tracks arm requests that already have a dedicated countdown thread
# running, so repeated scheduler ticks (every ~5s, see agent.py) don't spawn
# a second one for the same request while the first is still waiting.
_active_countdowns: set[int] = set()
_countdowns_lock = threading.Lock()

# Bounded retries for a failed/reverted attempt (e.g. a transient RPC error,
# or firing one tick before the chain's clock agrees startTime has passed).
# Not retried indefinitely — a real, persistent failure (sold out, quantity
# already exhausted) would otherwise retry forever.
MAX_FIRE_ATTEMPTS = 3

# How long after a signed-presale stage's (scraped, not on-chain) go-live
# time this module keeps trying to fire before giving up and marking the
# arm request expired. There's no on-chain endTime getter for a presale
# stage the way there is for the public stage (getPublicDrop) — this is a
# generous, fixed bound instead, since most allowlist windows run at least
# this long before yielding to the next stage.
SIGNED_PRESALE_FIRE_WINDOW_SECONDS = 3600

# How stale a signed arm/cancel message is allowed to be before it's
# rejected — bounds the replay window for a captured signature (e.g. from a
# logged request, a browser history entry, or a compromised proxy) rather
# than letting it be replayed indefinitely.
SIGNATURE_MAX_AGE_SECONDS = 300


def is_signature_timestamp_fresh(timestamp: float, now: float | None = None) -> bool:
    """Bounds the replay window for a signed arm/cancel message. Rejects
    both stale timestamps AND ones too far in the future (a clock-skew
    guard against a signature pre-dated to outlive this check)."""
    now = now if now is not None else time.time()
    return abs(now - timestamp) <= SIGNATURE_MAX_AGE_SECONDS


def _resolve_signed_presale_stage(collection_slug: str, owner_address: str, stage_label: str) -> dict:
    """Resolves a human-chosen stage name (e.g. "GTD", matching what the
    dashboard shows from the scraped mint schedule) to its on-chain SeaDrop
    stageIndex and go-live timestamp. Returns {"stage_index": int,
    "go_live_at": float} on success, or {"error": str}.

    Correlates the (cached) scraped mint-schedule list with a fresh
    OpenSea eligibility fetch BY POSITION — both are returned by OpenSea in
    the same chronological/tier display order (empirically confirmed
    against a real drop), which is the only correlation available: the
    eligibility response doesn't carry the stage's display name, and the
    scraped schedule doesn't carry the on-chain stageIndex.

    Resolved ONCE here, at arm time, rather than re-derived at fire time —
    the stage-name-to-index mapping is fixed by the collection's own
    configuration and isn't expected to change; it's contract
    address/grant validity that genuinely need re-checking close to firing
    (see _grant_and_target_still_valid)."""
    if not opensea_session.is_configured():
        return {"error": "OpenSea session not configured — signed-mint stages are unavailable"}

    try:
        details = collection_details.get_collection_details(collection_slug)
    except ValueError:
        return {"error": "Unknown collection"}
    schedule = details.get("mint_schedule") or []

    position = next((i for i, s in enumerate(schedule) if s.get("name") == stage_label), None)
    if position is None:
        return {"error": f"Unknown stage {stage_label!r} for this drop"}

    go_live_at = schedule[position].get("starts_epoch")
    if not go_live_at:
        return {"error": f"Could not determine a start time for stage {stage_label!r}"}

    eligibility_stages = opensea_session.fetch_drop_eligibility(collection_slug, owner_address)
    if not eligibility_stages or position >= len(eligibility_stages):
        return {"error": "Could not fetch stage eligibility from OpenSea — try again shortly"}

    stage = eligibility_stages[position]
    if stage.get("stageType") != "SIGNED_PRESALE":
        return {"error": f"Stage {stage_label!r} is not a signed-presale stage"}
    if not stage.get("isEligible"):
        return {"error": f"This wallet is not eligible for stage {stage_label!r}"}

    stage_index = stage.get("stageIndex")
    if not isinstance(stage_index, int):
        return {"error": f"Could not resolve an on-chain index for stage {stage_label!r}"}

    return {"stage_index": stage_index, "go_live_at": go_live_at}


def arm_drop(
    owner_address: str, collection_slug: str, quantity: int, max_price_wei: str,
    stage_label: str = "",
) -> dict:
    """
    Arms a drop (or, when stage_label is given, one SPECIFIC named stage of
    it — e.g. "GTD" — resolved via _resolve_signed_presale_stage) for
    auto-firing. Validates that the owner has an active session grant that
    actually covers this drop's contract, and that the requested
    quantity/total price stay within what that grant authorizes — arming
    can never request more than the already-granted permission itself
    allows.

    stage_label='' (the default) means the drop's public stage — unchanged
    from before signed-presale support existed. A drop's GTD/FCFS/Public
    stages can each be armed independently and simultaneously (see
    idx_arm_requests_active_owner_drop in store.py) — arming one doesn't
    consume or block the others, though the grant's overall
    quantity/spend caps are still shared and enforced cumulatively across
    all of them (see get_grant_commitment_totals below).

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
    if not isinstance(granted_max_quantity, int):
        return {"error": "Stored permission is malformed"}

    try:
        # max_price_wei is already a TOTAL for this whole arm request (the
        # dashboard labels it "Max ETH (total)", and node_client.fire_mint /
        # ethClient.fireMint compare it directly against mintPrice*quantity
        # with no further multiplication) — must NOT be multiplied by
        # quantity again here.
        requested_total_wei = int(max_price_wei)
        grant_cap_wei = int(grant["value_cap_wei"])
    except (TypeError, ValueError):
        return {"error": "Invalid price"}

    # Enforced CUMULATIVELY across every arm request this grant has ever
    # produced (including already-succeeded ones), not just this single
    # request in isolation — a grant's maxQuantity/valueCapWei is a
    # one-time authorization from the owner's signature, not a per-arm
    # limit that resets on every new arm. Without this, a grant could be
    # re-armed after each success and spend past what was actually
    # authorized, since there's no on-chain CallPolicy to catch it anymore.
    committed_quantity, committed_spend_wei = store.get_grant_commitment_totals(grant["id"])

    if committed_quantity + quantity > granted_max_quantity:
        remaining = max(0, granted_max_quantity - committed_quantity)
        return {
            "error": f"Quantity exceeds your permission's remaining allowance "
                     f"({remaining} left of {granted_max_quantity})"
        }

    if committed_spend_wei + requested_total_wei > grant_cap_wei:
        return {"error": "Requested total price exceeds your permission's remaining spend cap"}

    go_live_at = None
    stage_index = None
    if stage_label:
        resolution = _resolve_signed_presale_stage(collection_slug, owner, stage_label)
        if "error" in resolution:
            return resolution
        stage_index = resolution["stage_index"]
        go_live_at = resolution["go_live_at"]

    # Only one active (non-terminal) arm request per (owner, drop, stage) —
    # arming the SAME stage twice would otherwise risk two independent
    # watcher ticks each firing their own real, duplicate mint. This
    # upfront check is just an early-exit for a clean error message; it is
    # NOT the actual safety guarantee (two concurrent calls could both pass
    # it before either inserts — a real TOCTOU race). The actual guarantee
    # is idx_arm_requests_active_owner_drop, a partial unique index
    # enforced by SQLite itself — see store.create_arm_request / _init_schema.
    existing = store.get_active_arm_request_for_drop(owner, drop["id"], stage_label)
    if existing:
        return {"error": "This stage is already armed", "armId": existing["id"]}

    try:
        arm_id = store.create_arm_request(store.ArmRequestInput(
            owner_address=owner,
            drop_id=drop["id"],
            session_grant_id=grant["id"],
            quantity=quantity,
            max_price_wei=max_price_wei,
            go_live_at=go_live_at,  # None for public — discovered live via the on-chain window instead
            stage_label=stage_label,
            stage_index=stage_index,
        ))
    except sqlite3.IntegrityError:
        # Lost the race against a concurrent arm_drop call for the same
        # (owner, drop, stage) — the other call's row is now the real one.
        existing = store.get_active_arm_request_for_drop(owner, drop["id"], stage_label)
        return {
            "error": "This stage is already armed",
            "armId": existing["id"] if existing else None,
        }

    return {"armId": arm_id}


def revoke_grant(grant_id: int, owner_address: str) -> dict:
    """Revokes a session grant on demand — only for the owner who created
    it, and only while it isn't already revoked. Returns {"revoked": True}
    or {"error": str}.

    This ONLY stops THIS APP from using the grant's session key for future
    arm/fire actions (get_active_session_grant, _check_and_fire_one, and
    _countdown_and_fire all reject a revoked grant). It does NOT touch the
    underlying EOA — a session key is a real Ethereum private key, not a
    revocable on-chain approval — and does NOT sweep any ETH still sitting
    at the session address. Revoking narrows the window where the app
    itself might use the key; it does nothing for funds already exposed by
    a leaked key, and nothing to protect a balance left behind."""
    grant = store.get_session_grant(grant_id)
    if not grant or grant["owner_address"] != owner_address.lower():
        return {"error": "Session grant not found"}

    if not store.try_revoke_session_grant(grant_id):
        return {"error": "Already revoked"}
    return {"revoked": True}


def sweep_grant(grant_id: int, owner_address: str) -> dict:
    """Sends whatever ETH is left in a session grant's key back to the
    owner's own connected wallet — the actual fix for revoke_grant's "does
    not sweep any ETH" gap. Works whether or not the grant has already been
    revoked (a superseded/revoked grant's key can still hold real, real-
    world-recoverable ETH; there's no reason to block sweeping it back).

    chain is resolved from the grant's own target contract (the first entry
    in allowed_targets — a grant is always scoped to exactly one contract,
    see api_session_grant) via store.get_tracked_drop_by_contract_address,
    defaulting to "ethereum" when that contract isn't (or is no longer)
    tracked, matching every other chain-resolution call site in this file.

    Returns node_client.sweep_session_key's result shape, or {"error": str}
    for an ownership/lookup/decryption failure before ever touching the
    key. Never raises for a real, expected outcome — a balance too small to
    be worth sweeping comes back as a normal {"success": False, "error":
    "..."} result, not an exception."""
    grant = store.get_session_grant(grant_id)
    if not grant or grant["owner_address"] != owner_address.lower():
        return {"error": "Session grant not found"}

    try:
        decrypted_key = decrypt_secret(grant["encrypted_session_key"])
    except ValueError as e:
        logger.error("[firing] sweep grant %d: could not decrypt session key: %s", grant_id, e)
        return {"error": "Could not decrypt session key"}

    chain = "ethereum"
    allowed_targets = json.loads(grant["allowed_targets"])
    if allowed_targets:
        tracked = store.get_tracked_drop_by_contract_address(allowed_targets[0])
        if tracked:
            chain = tracked.get("chain") or "ethereum"

    try:
        return node_client.sweep_session_key(decrypted_key, owner_address, chain)
    except RuntimeError as e:
        return {"error": str(e)}


def transfer_minted_nft(mint_attempt_id: int, owner_address: str) -> dict:
    """Sends whatever NFT(s) a session key minted in one specific mint
    attempt back to the owner's own connected wallet — a session key only
    ever exists to fire the mint at speed, it was never meant to be where
    the NFT actually lives.

    Walks mint_attempt -> arm_request -> (session_grant, tracked_drop) to
    recover the encrypted session key and which contract/chain actually
    minted it. Ownership is checked against the arm_request's owner (mint
    attempts don't store an owner directly) — only the wallet that armed
    and fired this specific mint can move it.

    Returns node_client.transfer_minted_nft's result shape, or {"error":
    str} for an ownership/lookup/decryption failure before ever touching
    the key. Never raises for a real, expected outcome."""
    mint_attempt = store.get_mint_attempt(mint_attempt_id)
    if not mint_attempt or mint_attempt["status"] != "success" or not mint_attempt["tx_hash"]:
        return {"error": "Successful mint attempt not found"}

    arm = store.get_arm_request(mint_attempt["arm_request_id"])
    if not arm or arm["owner_address"] != owner_address.lower():
        return {"error": "Mint attempt not found"}

    grant = store.get_session_grant(arm["session_grant_id"])
    if not grant:
        return {"error": "Session grant for this mint no longer exists"}

    drop = store.get_tracked_drop(arm["drop_id"])
    if not drop or not drop.get("contract_address"):
        return {"error": "Collection for this mint no longer exists"}

    try:
        decrypted_key = decrypt_secret(grant["encrypted_session_key"])
    except ValueError as e:
        logger.error(
            "[firing] transfer nft for mint attempt %d: could not decrypt session key: %s",
            mint_attempt_id, e,
        )
        return {"error": "Could not decrypt session key"}

    chain = drop.get("chain") or "ethereum"

    try:
        return node_client.transfer_minted_nft(
            decrypted_key, drop["contract_address"], mint_attempt["tx_hash"], owner_address, chain,
        )
    except RuntimeError as e:
        return {"error": str(e)}


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


def get_arm_status_for_drop(owner_address: str, collection_slug: str, stage_label: str = "") -> dict | None:
    """The owner's active (non-terminal) arm request for one stage of a
    drop (default '' = the public stage), plus its mint attempts, looked
    up by collection_slug — or None if this owner has no active arm
    request for that stage."""
    drop = store.get_tracked_drop_by_slug(collection_slug)
    if not drop:
        return None
    arm = store.get_active_arm_request_for_drop(owner_address.lower(), drop["id"], stage_label)
    if not arm:
        return None
    return {"arm": arm, "attempts": store.get_mint_attempts(arm["id"])}


def get_all_arm_statuses_for_drop(owner_address: str, collection_slug: str) -> list[dict]:
    """Every stage (public and/or any signed-presale stages) the owner
    currently has actively armed for this drop, each with its mint
    attempts — lets the dashboard show status for GTD/FCFS/Public
    simultaneously rather than assuming only one stage is ever armed."""
    drop = store.get_tracked_drop_by_slug(collection_slug)
    if not drop:
        return []
    arms = store.get_active_arm_requests_for_drop_all_stages(owner_address.lower(), drop["id"])
    return [{"arm": arm, "attempts": store.get_mint_attempts(arm["id"])} for arm in arms]


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


def _grant_and_target_still_valid(drop: dict | None, grant: dict | None, now: float) -> bool:
    """True iff drop/grant are both present, the grant isn't revoked/expired,
    and the drop's CURRENT contract address is still covered by the grant's
    allowed_targets. Shared by _check_and_fire_one (the regular tick) and
    _countdown_and_fire (the precision-firing thread) — a countdown thread
    can sleep for up to CRITICAL_WINDOW_SECONDS before actually firing, and
    the drop/grant it was spawned with are a snapshot from BEFORE that wait,
    not guaranteed still true at the moment it's about to sign+broadcast (a
    re-scrape can overwrite tracked_drops.contract_address, or the owner can
    revoke the grant, at any point during that wait)."""
    if not drop or not drop.get("contract_address"):
        return False
    if not grant or grant["revoked"] or grant["expires_at"] <= now:
        return False
    try:
        allowed_targets = json.loads(grant["allowed_targets"])
    except (TypeError, ValueError):
        allowed_targets = []
    return drop["contract_address"] in allowed_targets


def _schedule_or_fire(
    arm: dict, drop: dict, grant: dict, now: float, start_time: float, end_time: float | None,
) -> None:
    """Shared state-transition logic once a go-live (and optional end)
    time is known, regardless of where that timing came from — the public
    stage's real on-chain window (getPublicDrop) or a signed-presale
    stage's scraped go-live time (resolved once at arm time, see
    _resolve_signed_presale_stage). Decides whether to mark 'scheduled',
    hand off to a countdown thread, or expire."""
    if end_time is not None and now >= end_time:
        logger.info("[firing] arm request %d: mint window has ended, expiring", arm["id"])
        store.update_arm_request_status(arm["id"], "expired")
        return

    if now >= start_time + GO_LIVE_SAFETY_MARGIN_SECONDS:
        # Already live by the time a regular tick caught this (e.g. the
        # process just restarted, or this arm request only just got past
        # validation) — fire via the same background-thread path as the
        # countdown case below (deadline in the past means it fires on the
        # very first clock check), rather than blocking this tick: a slow
        # fire_mint call (up to ~90s worst case) must never delay every
        # OTHER pending arm request this same tick is about to check.
        _start_countdown_if_not_already_running(arm, drop, grant, start_time)
        return

    if now >= start_time - CRITICAL_WINDOW_SECONDS:
        # Close enough to go-live that waiting for the next regular tick
        # (every ~5s, see agent.py) would cost real, avoidable latency —
        # hand off to a dedicated thread that busy-waits on the local
        # clock and fires the instant go-live actually passes.
        if arm["status"] != "scheduled":
            store.update_arm_request_status(arm["id"], "scheduled")
        _start_countdown_if_not_already_running(arm, drop, grant, start_time)
        return

    if arm["status"] != "scheduled":
        store.update_arm_request_status(arm["id"], "scheduled")


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
    # instead of a clean, immediate, off-chain failure. This same check runs
    # AGAIN, against freshly-fetched rows, immediately before actually
    # firing (see _countdown_and_fire) — this earlier check is just a fast
    # path to fail obviously-stale requests without ever starting a thread.
    if not _grant_and_target_still_valid(drop, grant, now):
        logger.warning(
            "[firing] arm request %d: grant/target no longer valid at tick time, failing", arm["id"],
        )
        store.update_arm_request_status(arm["id"], "failed")
        return

    if arm.get("stage_label"):
        # Signed-presale stage: go-live was resolved once, at arm time
        # (see _resolve_signed_presale_stage) — there's no on-chain getter
        # to poll live the way there is for the public stage.
        go_live_at = arm.get("go_live_at")
        if not go_live_at:
            logger.warning(
                "[firing] arm request %d: no go-live time resolved for stage %r, failing",
                arm["id"], arm.get("stage_label"),
            )
            store.update_arm_request_status(arm["id"], "failed")
            return
        _schedule_or_fire(
            arm, drop, grant, now, go_live_at, go_live_at + SIGNED_PRESALE_FIRE_WINDOW_SECONDS,
        )
        return

    try:
        window = node_client.get_public_drop_window(
            drop["contract_address"], drop.get("chain") or "ethereum"
        )
    except RuntimeError as e:
        logger.warning("[firing] arm request %d: could not read drop window: %s", arm["id"], e)
        return  # transient — try again next tick

    if not window:
        return  # no public stage configured on-chain yet — wait

    _schedule_or_fire(arm, drop, grant, now, window["startTime"], window["endTime"])


def _start_countdown_if_not_already_running(
    arm: dict, drop: dict, grant: dict, start_time: float
) -> None:
    """Spawns (at most once per arm request) a dedicated thread that fires
    the moment start_time passes. Also used for the "already live" case —
    a start_time already in the past just means the thread's very first
    clock check is already past its deadline, so it fires immediately."""
    with _countdowns_lock:
        if arm["id"] in _active_countdowns:
            return  # a thread from an earlier tick is already waiting/firing
        _active_countdowns.add(arm["id"])

    thread = threading.Thread(
        target=_countdown_and_fire, args=(arm, drop, grant, start_time), daemon=True,
        name=f"firing-countdown-{arm['id']}",
    )
    thread.start()


def _countdown_and_fire(arm: dict, drop: dict, grant: dict, start_time: float) -> None:
    """Runs in its own thread, decoupled from the scheduler's tick cadence
    (and from every OTHER pending arm request's tick processing) entirely:
    busy-waits (in small increments) on the local wall clock until
    start_time passes, then fires immediately — this is what actually
    delivers sub-second firing precision, not the tick interval.

    drop/grant are the snapshot from whichever tick spawned this thread —
    up to CRITICAL_WINDOW_SECONDS stale by the time the wait ends. Re-fetches
    both fresh and re-validates immediately before firing (same check as
    _check_and_fire_one) rather than trusting that snapshot: a re-scrape can
    overwrite the drop's contract address, or the owner can revoke the
    grant, at any point during the wait — without this, firing would use
    stale data for the one decision (which key, which contract) that this
    entire off-chain safety net exists to get right."""
    try:
        deadline = start_time + GO_LIVE_SAFETY_MARGIN_SECONDS
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(remaining, COUNTDOWN_POLL_INTERVAL_SECONDS))

        fresh_drop = store.get_tracked_drop(arm["drop_id"])
        fresh_grant = store.get_session_grant(arm["session_grant_id"])
        if not _grant_and_target_still_valid(fresh_drop, fresh_grant, time.time()):
            logger.warning(
                "[firing] arm request %d: grant/target no longer valid at fire time, failing", arm["id"],
            )
            store.update_arm_request_status(arm["id"], "failed")
            return

        store.update_arm_request_status(arm["id"], "armed")
        _fire_one(arm, fresh_drop, fresh_grant, go_live_at=start_time)
    except Exception as e:
        logger.warning("[firing] arm request %d: countdown thread error: %s", arm["id"], e)
    finally:
        with _countdowns_lock:
            _active_countdowns.discard(arm["id"])


def _fire_one(arm: dict, drop: dict, grant: dict, go_live_at: float | None = None) -> None:
    """Attempts to fire exactly once for one arm request. The atomic
    'armed' -> 'fired' claim (store.try_claim_arm_request) is the sole
    guard against double-firing — if the claim fails, another tick (or a
    concurrent invocation) already took it, and this is a safe no-op.

    go_live_at is the drop's real on-chain public-mint start time (from
    node_client.get_public_drop_window), when known — used only to compute
    latency_ms (how many milliseconds after go-live this attempt actually
    fired), the number that answers "how fast is this tool" for a
    speed-critical mint. None when there's no go-live reference (e.g. a
    direct/test call), in which case latency_ms is simply not recorded."""
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

    # Captured immediately before the actual fire call — this, not
    # mint_attempts.attempted_at (recorded further below, AFTER fire_mint's
    # full round trip including up to a 60s receipt wait), is the real
    # "we fired at this instant" timestamp.
    fire_started_at = time.time()
    latency_ms = (
        round((fire_started_at - go_live_at) * 1000) if go_live_at is not None else None
    )

    stage_label = arm.get("stage_label") or ""
    if stage_label:
        result = _fire_signed_presale(arm, drop, decrypted_approval, stage_label)
    else:
        try:
            result = node_client.fire_mint(
                decrypted_approval, drop["contract_address"],
                arm["quantity"], arm["max_price_wei"],
                chain=drop.get("chain") or "ethereum",
            )
        except RuntimeError as e:
            # A "timed out" RuntimeError at this layer means the Node
            # helper's own internal receipt-wait (up to 60s) plus the HTTP
            # round-trip may have still been mid-flight when this call's
            # own 90s timeout fired — a real transaction could genuinely
            # have been broadcast, same as the "ambiguous" case
            # node_client.fire_mint itself can return. Any OTHER
            # RuntimeError (connection refused, an immediate error
            # response) happens before Node ever calls sendTransaction, so
            # it's a safe, definite failure — nothing was ever broadcast.
            is_ambiguous = "timed out" in str(e).lower()
            result = {
                "success": False, "ambiguous": is_ambiguous, "txHash": None,
                "blockNumber": None, "gasUsed": None, "error": str(e),
            }

    block_number = result.get("blockNumber")
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm["id"],
        tx_hash=result.get("txHash"),
        user_op_hash=None,  # not applicable — this model fires direct signed transactions, no ERC-4337 UserOperation
        status="success" if result.get("success") else "failed",
        error_message=None if result.get("success") else result.get("error"),
        gas_used=result.get("gasUsed"),
        block_number=int(block_number) if block_number else None,
        fired_at=fire_started_at,
        latency_ms=latency_ms,
    ))

    if result.get("success"):
        logger.info("[firing] arm request %d: mint succeeded, tx=%s", arm["id"], result.get("txHash"))
        store.update_arm_request_status(arm["id"], "succeeded")
        return

    if result.get("ambiguous"):
        # NEVER retry an ambiguous outcome — a prior attempt may already
        # have landed on-chain, and resubmitting risks a genuine duplicate
        # spend/mint. Fail immediately regardless of MAX_FIRE_ATTEMPTS; the
        # recorded tx_hash (see mint_attempts above) is what a human would
        # use to check the real outcome manually.
        logger.error(
            "[firing] arm request %d: AMBIGUOUS outcome (txHash=%s) — a mint may already "
            "have gone through. Failing without retry to avoid a duplicate submission.",
            arm["id"], result.get("txHash"),
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


def _fire_signed_presale(arm: dict, drop: dict, decrypted_approval: str, stage_label: str) -> dict:
    """Fetches a FRESH, ready-to-sign transaction from OpenSea's own
    backend for this stage (never cached or reused across attempts — the
    underlying signature is single-use per SeaDrop's own docs) and, if
    available, fires it via node_client.fire_raw_transaction. Returns the
    same result shape node_client.fire_mint/fire_raw_transaction do.

    Unlike the public-mint path, this never builds or ABI-encodes the mint
    call itself — opensea_session.fetch_mint_transaction_data returns
    OpenSea's own already-encoded {to, data, valueWei}, verified live
    2026-08-12 against a real allowlist mint. The owner's own wallet
    address is what OpenSea checks eligibility against (not the session
    key's) — see that function's docstring for why that's still safe to
    fire from the session key.

    If a transaction can't be fetched for ANY reason (OpenSea session not
    configured, request failed, not eligible for this stage), returns a
    normal, non-exceptional, retryable failure result — this never fires
    blind with fabricated or stale data. The returned cost is checked
    against arm's own max_price_wei cap BEFORE ever firing — OpenSea's
    backend is trusted to build a VALID transaction, but not to enforce
    what the owner actually authorized THIS APP to spend; that check is
    this function's job, same as every other spend path in this codebase."""
    chain = drop.get("chain") or "ethereum"

    tx_data = opensea_session.fetch_mint_transaction_data(
        arm["owner_address"], drop["contract_address"], arm["quantity"], chain,
    )
    if not tx_data:
        return {
            "success": False, "txHash": None, "blockNumber": None, "gasUsed": None,
            "error": f"Mint transaction unavailable for stage {stage_label!r} on this attempt",
        }

    value_wei = int(tx_data["valueWei"])
    max_price_wei = int(arm["max_price_wei"])
    if value_wei > max_price_wei:
        return {
            "success": False, "txHash": None, "blockNumber": None, "gasUsed": None,
            "error": f"Real cost ({value_wei} wei) exceeds the granted spend cap "
                     f"({max_price_wei} wei) — aborted before firing, nothing spent",
        }

    try:
        return node_client.fire_raw_transaction(
            decrypted_approval, tx_data["to"], tx_data["data"], tx_data["valueWei"], chain,
        )
    except RuntimeError as e:
        # Same ambiguous-vs-definite distinction as the public-mint path —
        # see _fire_one's comment on this.
        is_ambiguous = "timed out" in str(e).lower()
        return {
            "success": False, "ambiguous": is_ambiguous, "txHash": None,
            "blockNumber": None, "gasUsed": None, "error": str(e),
        }
