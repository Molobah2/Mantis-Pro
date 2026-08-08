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
