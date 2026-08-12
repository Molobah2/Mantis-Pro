"""
Server-side replay of the connected owner's authenticated OpenSea browser
session — the only way to obtain a SeaDrop mintSigned() authorization
(mintParams + salt + signature) for an allowlist/presale stage. OpenSea's
own backend generates that signature per-wallet, on demand, inside an
undocumented internal GraphQL API (gql.opensea.io) that only responds to
an authenticated, eligible session — there is no public API for this (see
RESEARCH_NOTES.md).

The session is a captured `Cookie` header value from a real, already-
logged-in opensea.io browser tab, stored as the OPENSEA_SESSION_COOKIE
Railway env var — mirrors this project's existing UPVOTE_SESSION_B64
pattern (portal_upvote/node_client.py) for the same kind of problem
(replaying a captured third-party auth session server-side). Verified
empirically (2026-08-09): a plain server-side HTTP replay of a real
captured cookie against gql.opensea.io works cleanly with no browser
fingerprint or Cloudflare challenge required.

This is inherently fragile: it replays another site's undocumented,
unversioned internal API using a session that WILL eventually expire or
get invalidated (logout, password change, OpenSea rotating auth/cookies).
There is no SLA here, and no automatic refresh — a stale session just
means signed-mint stages become unfireable until re-captured. Every
function in this module fails soft (returns None) on any unexpected
response shape or failure; nothing here ever raises into the firing
pipeline — a signed-presale stage that can't get its authorization is a
normal, expected outcome (not yet live, session expired, not eligible),
not a bug.
"""
import json
import logging
import os

import requests as _req

logger = logging.getLogger(__name__)

_GRAPHQL_URL = "https://gql.opensea.io/graphql"

# Mirrors a real Chrome request as closely as practical — captured
# 2026-08-09 from a live, working request. x-app-id is OpenSea's own
# static frontend identifier, not a secret or a computed signature.
_BASE_HEADERS = {
    "accept": "application/graphql-response+json, application/graphql+json, "
              "application/json, text/event-stream, multipart/mixed",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://opensea.io",
    "referer": "https://opensea.io/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "x-app-id": "os2-web",
    "x-graphql-operation-type": "query",
}

_REQUEST_TIMEOUT_SECONDS = 10


def _session_cookie() -> str:
    return os.getenv("OPENSEA_SESSION_COOKIE", "").strip()


def is_configured() -> bool:
    """Whether a captured OpenSea session is available at all. Callers
    (firing.py) should check this before attempting a signed-mint stage
    and treat "not configured" as a normal, expected reason that stage
    isn't fireable — not an error."""
    return bool(_session_cookie())


def _persisted_query_get(operation_name: str, variables: dict, sha256_hash: str) -> dict | None:
    """One persisted-query GET against OpenSea's GraphQL API, replaying
    the captured session cookie. Returns the parsed top-level 'data'
    object on success, or None on ANY failure (network error, non-200,
    malformed JSON, GraphQL 'errors' in the body, no session configured,
    session expired/rejected). Every failure mode is treated identically
    by callers: "couldn't get this on this attempt" — never raises."""
    cookie = _session_cookie()
    if not cookie:
        return None

    params = {
        "operationName": operation_name,
        "variables": json.dumps(variables, separators=(",", ":")),
        "extensions": json.dumps(
            {"persistedQuery": {"sha256Hash": sha256_hash, "version": 1}},
            separators=(",", ":"),
        ),
    }
    headers = dict(_BASE_HEADERS)
    headers["cookie"] = cookie

    try:
        r = _req.get(_GRAPHQL_URL, params=params, headers=headers, timeout=_REQUEST_TIMEOUT_SECONDS)
    except _req.exceptions.RequestException as e:
        logger.warning("[opensea_session] %s request failed: %s", operation_name, e)
        return None

    if r.status_code != 200:
        logger.warning(
            "[opensea_session] %s returned HTTP %d — session may be expired/invalid",
            operation_name, r.status_code,
        )
        return None

    try:
        body = r.json()
    except ValueError:
        logger.warning("[opensea_session] %s returned a non-JSON response", operation_name)
        return None

    if not isinstance(body, dict):
        logger.warning("[opensea_session] %s returned an unexpected response shape", operation_name)
        return None

    if body.get("errors"):
        logger.warning("[opensea_session] %s returned GraphQL errors: %s", operation_name, body["errors"])
        return None

    return body.get("data")


# Captured 2026-08-09 from a live request against opensea.io/collection/
# gobbozhq/overview — persisted-query hashes are stable per query TEXT
# (not per session/user), so this is safe to hardcode and reuse for any
# collection/wallet.
_DROP_ELIGIBILITY_SHA256 = "e1b54354df0d26d39c6b81429bd5e5d37749eaa4bdc027f987128f8c1e7d2308"


def fetch_drop_eligibility(collection_slug: str, owner_address: str) -> list[dict] | None:
    """Replays OpenSea's DropEligibilityQuery for one wallet + collection.
    Returns the raw 'stages' list — each entry has stageType ('PUBLIC_SALE'
    or 'SIGNED_PRESALE' seen so far), stageIndex, isEligible,
    eligibleMaxTotalMintableByWallet, eligiblePrice — or None on failure.

    This does NOT include a signature/salt — see
    fetch_signed_mint_authorization for the actual mint authorization,
    which is a separate, not-yet-captured request."""
    data = _persisted_query_get(
        "DropEligibilityQuery",
        {"address": owner_address.lower(), "collectionSlug": collection_slug},
        _DROP_ELIGIBILITY_SHA256,
    )
    if not data:
        return None
    drop = data.get("dropBySlug")
    if not isinstance(drop, dict):
        return None
    stages = drop.get("stages")
    return stages if isinstance(stages, list) else None


def fetch_signed_mint_authorization(
    collection_slug: str, owner_address: str, stage_index: int
) -> dict | None:
    """NOT YET WIRED. Superseded by fetch_mint_transaction_data below —
    kept only because firing.py's _fire_signed_presale still calls it (that
    caller is being migrated to the new function in the same change that
    adds it). Always returns None."""
    logger.warning(
        "[opensea_session] fetch_signed_mint_authorization not yet implemented "
        "(collection=%s owner=%s stage=%d) — signed-mint stages cannot fire yet",
        collection_slug, owner_address, stage_index,
    )
    return None


# Captured 2026-08-12 from a LIVE, real GTD allowlist mint (NUMBERS on
# Robinhood Chain) — confirmed working end-to-end: decoding the returned
# transactionSubmissionData.data against SeaDrop's own mintSigned() ABI
# byte-for-byte matched the exact fields expected (nftContract,
# feeRecipient, mintParams, salt, signature). Persisted-query hashes are
# stable per query TEXT (not per session/user), so this is safe to hardcode
# and reuse for any collection/wallet — same reasoning as
# _DROP_ELIGIBILITY_SHA256 above.
_MINT_ACTION_TIMELINE_SHA256 = "55e2f535d4b2f2ef95cfc4f349632e1f225647d1a6b1c7e652b768d595af16f3"

_NATIVE_TOKEN_PLACEHOLDER_ADDRESS = "0x0000000000000000000000000000000000000000"


def fetch_mint_transaction_data(
    owner_address: str, nft_contract_address: str, quantity: int, chain: str = "ethereum",
) -> dict | None:
    """Replays OpenSea's MintActionTimelineQuery for one wallet + collection
    + quantity — the real request OpenSea's own frontend fires when a
    connected, ELIGIBLE wallet starts a mint. owner_address here is the
    wallet whose eligibility gets checked (verified live: it does NOT need
    to be the wallet that ends up submitting the transaction — the returned
    calldata's minterIfNotPayer was the zero address, meaning "whoever
    pays/submits this is the minter"). This is what lets the OWNER's real,
    allowlisted wallet authorize a mint that the SESSION KEY later fires.

    Unlike fetch_signed_mint_authorization's original plan (return
    mintParams/salt/signature for OUR OWN ABI encoding), this returns
    OpenSea's own already-ABI-encoded transaction — {"to": str, "data":
    "0x...", "valueWei": str} — ready to sign and send as-is. Verified live
    2026-08-12: decoding that "data" against SeaDrop's mintSigned() ABI
    matched perfectly, but nothing here or in the caller assumes that
    specifically — this only ever relays whatever real, OpenSea-authorized
    transaction the response contains, whatever contract/function it
    targets.

    Returns None on ANY failure (no session configured, network error,
    non-200, GraphQL errors, not eligible, or an unexpected response
    shape) — never raises. A None here is a normal, expected "can't fire
    this attempt" outcome for firing.py, not a bug."""
    variables = {
        "address": owner_address.lower(),
        "capabilities": {"eip7702": False},
        "fromAssets": [{"asset": {"chain": chain, "contractAddress": _NATIVE_TOKEN_PLACEHOLDER_ADDRESS}}],
        "toAssets": [{
            "asset": {"chain": chain, "contractAddress": nft_contract_address.lower(), "tokenId": "0"},
            "quantity": str(quantity),
        }],
    }
    data = _persisted_query_get("MintActionTimelineQuery", variables, _MINT_ACTION_TIMELINE_SHA256)
    if not data:
        return None

    swap = data.get("swap")
    if not isinstance(swap, dict):
        return None
    if swap.get("errors"):
        logger.warning("[opensea_session] MintActionTimelineQuery returned swap errors: %s", swap["errors"])
        return None

    actions = swap.get("actions")
    if not isinstance(actions, list) or not actions:
        return None
    action = actions[0]
    if not isinstance(action, dict) or action.get("__typename") != "MintAction":
        return None

    tx_data = action.get("transactionSubmissionData")
    if not isinstance(tx_data, dict):
        return None
    to = tx_data.get("to")
    call_data = tx_data.get("data")
    value = tx_data.get("value")
    if not isinstance(to, str) or not isinstance(call_data, str) or value is None:
        return None

    return {"to": to, "data": call_data, "valueWei": str(value)}
