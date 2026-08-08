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
