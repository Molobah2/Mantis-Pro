import logging

import requests as _req

from . import config as _cfg

logger = logging.getLogger(__name__)


def get_smart_account_address(owner_address: str) -> str:
    """POST to the Node wallet-helper's /eth/smart-account-address route.

    Given an owner EOA address, returns the derived (counterfactual) ZeroDev
    smart account address for it. Read-only, non-firing computation — no
    signing, no transaction. Raises RuntimeError on any failure — connection
    refused, timeout, a non-JSON response body, or an {"error": ...} response
    body — so callers only ever need to catch one exception type.
    """
    payload = {"ownerAddress": owner_address}

    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/eth/smart-account-address", json=payload, timeout=30
        )
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")
    except _req.exceptions.Timeout:
        raise RuntimeError("Node wallet-helper timed out")

    try:
        result = r.json()
    except ValueError:
        logger.warning(
            "[node_client] non-JSON response from Node helper, status=%d", r.status_code
        )
        raise RuntimeError(f"Node helper returned a non-JSON response (HTTP {r.status_code})")

    if not isinstance(result, dict):
        raise RuntimeError("Node helper returned an unexpected response shape")

    if r.status_code != 200 or result.get("error"):
        error_msg = result.get("error", f"HTTP {r.status_code}")
        logger.warning("[node_client] smart-account-address failed: %s", error_msg)
        raise RuntimeError(error_msg)

    return result["smartAccountAddress"]


def verify_session_grant(
    serialized_approval: str, owner_address: str, smart_account_address: str
) -> tuple[bool, str | None]:
    """POST to the Node wallet-helper's /eth/verify-session-grant route.

    Confirms a browser-produced serialized session-key approval genuinely
    resolves to the claimed owner/smart-account addresses BEFORE it's ever
    persisted — closes the gap where a client could POST a real, validly-
    signed approval for their OWN wallet alongside false ownerAddress/
    smartAccountAddress metadata claiming it belongs to someone else.

    Returns (True, None) if verification passed. Returns (False, reason) if
    verification legitimately failed (e.g. a spoofed/mismatched claim) —
    this is an expected, non-exceptional outcome, not a system failure.
    Raises RuntimeError only for actual Node-helper-level failures
    (connection refused, timeout, malformed response) — mirrors
    get_smart_account_address's resilience pattern for those cases.
    """
    payload = {
        "serializedApproval": serialized_approval,
        "ownerAddress": owner_address,
        "smartAccountAddress": smart_account_address,
    }

    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/eth/verify-session-grant", json=payload, timeout=30
        )
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")
    except _req.exceptions.Timeout:
        raise RuntimeError("Node wallet-helper timed out")

    try:
        result = r.json()
    except ValueError:
        raise RuntimeError(f"Node helper returned a non-JSON response (HTTP {r.status_code})")

    if not isinstance(result, dict):
        raise RuntimeError("Node helper returned an unexpected response shape")

    if r.status_code == 200 and result.get("valid") is True:
        return True, None

    if r.status_code == 400:
        # A legitimate "no" — the approval didn't resolve to the claimed
        # addresses. Not a Node-helper malfunction.
        return False, result.get("error", "Verification failed")

    error_msg = result.get("error", f"HTTP {r.status_code}")
    logger.warning("[node_client] verify-session-grant failed: %s", error_msg)
    raise RuntimeError(error_msg)


def verify_owner_signature(owner_address: str, message: str, signature: str) -> bool:
    """POST to the Node wallet-helper's /eth/verify-owner-signature route.

    Confirms a plain EOA signature over `message` actually recovers to
    `owner_address` — used to authorize arm/cancel actions (which otherwise
    would trust a bare, self-reported owner address with no proof of
    control). Returns True/False — a signature that doesn't recover to the
    claimed address is a legitimate "no", not a Node-helper malfunction.
    Raises RuntimeError only for actual Node-helper-level failures
    (connection refused, timeout, malformed response).
    """
    payload = {"ownerAddress": owner_address, "message": message, "signature": signature}

    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/eth/verify-owner-signature", json=payload, timeout=30
        )
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")
    except _req.exceptions.Timeout:
        raise RuntimeError("Node wallet-helper timed out")

    try:
        result = r.json()
    except ValueError:
        raise RuntimeError(f"Node helper returned a non-JSON response (HTTP {r.status_code})")

    if not isinstance(result, dict):
        raise RuntimeError("Node helper returned an unexpected response shape")

    if r.status_code != 200:
        error_msg = result.get("error", f"HTTP {r.status_code}")
        logger.warning("[node_client] verify-owner-signature failed: %s", error_msg)
        raise RuntimeError(error_msg)

    return bool(result.get("valid"))


def get_public_drop_window(nft_contract_address: str) -> dict | None:
    """POST to the Node wallet-helper's /eth/public-drop-window route.

    Read-only, no-wallet lookup of a collection's real on-chain public mint
    window. Returns {"startTime": int, "endTime": int, "mintPriceWei": str}
    or None when the collection doesn't expose a public drop stage (not an
    error — e.g. allowlist-only collections, or one that hasn't configured
    a public stage yet). Raises RuntimeError only for actual Node-helper-
    level failures (connection refused, timeout, malformed response).
    """
    payload = {"nftContract": nft_contract_address}

    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/eth/public-drop-window", json=payload, timeout=30
        )
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")
    except _req.exceptions.Timeout:
        raise RuntimeError("Node wallet-helper timed out")

    try:
        result = r.json()
    except ValueError:
        raise RuntimeError(f"Node helper returned a non-JSON response (HTTP {r.status_code})")

    if not isinstance(result, dict):
        raise RuntimeError("Node helper returned an unexpected response shape")

    if r.status_code != 200:
        error_msg = result.get("error", f"HTTP {r.status_code}")
        logger.warning("[node_client] public-drop-window failed: %s", error_msg)
        raise RuntimeError(error_msg)

    if not result.get("available"):
        return None

    return {
        "startTime": result["startTime"],
        "endTime": result["endTime"],
        "mintPriceWei": result["mintPriceWei"],
    }


def fire_mint(
    serialized_approval: str,
    nft_contract_address: str,
    smart_account_address: str,
    quantity: int,
    value_cap_wei: str,
) -> dict:
    """POST to the Node wallet-helper's /eth/fire-mint route.

    THE ONE FUNCTION IN THIS MODULE THAT SPENDS REAL ETH. serialized_approval
    must already be DECRYPTED plaintext — callers decrypt just before this
    call and must never persist the plaintext or log it.

    Returns a dict shaped like:
        {"success": bool, "userOpHash": str, "txHash": str|None,
         "blockNumber": str|None, "gasUsed": str|None, "error": str|None}
    A failed/reverted mint (e.g. sold out, price changed, quantity limit
    hit) comes back as {"success": False, "error": "..."} — a real,
    non-exceptional outcome callers must log, not a Node-helper failure.
    Raises RuntimeError only for actual Node-helper-level failures
    (connection refused, timeout, malformed response, ZeroDev not
    configured) — mirrors this module's other functions' resilience style.
    """
    payload = {
        "serializedApproval": serialized_approval,
        "nftContract": nft_contract_address,
        "smartAccountAddress": smart_account_address,
        "quantity": quantity,
        "valueCapWei": value_cap_wei,
    }

    try:
        # A UserOperation submission + receipt wait can legitimately take a
        # while (up to the Node side's own 60s receipt timeout) — this HTTP
        # timeout must stay comfortably above that, not match the other
        # (much cheaper) calls in this module.
        r = _req.post(f"{_cfg.NODE_HELPER_URL}/eth/fire-mint", json=payload, timeout=90)
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")
    except _req.exceptions.Timeout:
        raise RuntimeError("Node wallet-helper timed out")

    try:
        result = r.json()
    except ValueError:
        raise RuntimeError(f"Node helper returned a non-JSON response (HTTP {r.status_code})")

    if not isinstance(result, dict):
        raise RuntimeError("Node helper returned an unexpected response shape")

    if r.status_code != 200:
        error_msg = result.get("error", f"HTTP {r.status_code}")
        logger.warning("[node_client] fire-mint request-level failure: %s", error_msg)
        raise RuntimeError(error_msg)

    return result
