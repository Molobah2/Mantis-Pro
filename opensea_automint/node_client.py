import logging

import requests as _req

from . import config as _cfg

logger = logging.getLogger(__name__)


def verify_owner_signature(owner_address: str, message: str, signature: str) -> bool:
    """POST to the Node wallet-helper's /eth/verify-owner-signature route.

    Confirms a plain EOA signature over `message` actually recovers to
    `owner_address` — used to authorize grant/arm/cancel actions (which
    otherwise would trust a bare, self-reported owner address with no proof
    of control). Returns True/False — a signature that doesn't recover to
    the claimed address is a legitimate "no", not a Node-helper malfunction.
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


def verify_session_key(session_private_key: str, session_address: str) -> bool:
    """POST to the Node wallet-helper's /eth/verify-session-key route.

    Confirms a session private key actually corresponds to the
    session_address a grant request claims for it — closes the gap where a
    client could send a validly-signed grant authorization for a session
    address it claims, alongside a DIFFERENT private key entirely, which
    would silently store a key that can never actually mint as the address
    the owner thinks they authorized. Returns True/False; raises
    RuntimeError only for actual Node-helper-level failures.
    """
    payload = {"sessionPrivateKey": session_private_key, "sessionAddress": session_address}

    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/eth/verify-session-key", json=payload, timeout=30
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
        logger.warning("[node_client] verify-session-key failed: %s", error_msg)
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
    session_private_key: str,
    nft_contract_address: str,
    quantity: int,
    value_cap_wei: str,
) -> dict:
    """POST to the Node wallet-helper's /eth/fire-mint route.

    THE ONE FUNCTION IN THIS MODULE THAT SPENDS REAL ETH. session_private_key
    must already be DECRYPTED plaintext — callers decrypt just before this
    call and must never persist the plaintext or log it. Fires a direct
    signed transaction from that key's own address — not sponsored through
    any smart account, so that key needs its own ETH balance.

    Returns a dict shaped like:
        {"success": bool, "txHash": str|None, "blockNumber": str|None,
         "gasUsed": str|None, "error": str|None, "ambiguous": bool|None}
    A failed/reverted mint (e.g. sold out, price changed, quantity limit
    hit) comes back as {"success": False, "error": "..."} — a real,
    non-exceptional outcome callers must log, not a Node-helper failure.
    Raises RuntimeError only for actual Node-helper-level failures
    (connection refused, timeout, malformed response) — mirrors this
    module's other functions' resilience style.
    """
    payload = {
        "sessionPrivateKey": session_private_key,
        "nftContract": nft_contract_address,
        "quantity": quantity,
        "valueCapWei": value_cap_wei,
    }

    try:
        # A transaction submission + receipt wait can legitimately take a
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


def fire_signed_mint(
    session_private_key: str,
    nft_contract_address: str,
    quantity: int,
    value_cap_wei: str,
    mint_params: dict,
    salt: str,
    signature: str,
) -> dict:
    """POST to the Node wallet-helper's /eth/fire-signed-mint route — the
    allowlist/presale counterpart to fire_mint, for SeaDrop's mintSigned().

    mint_params/salt/signature must come from opensea_session.
    fetch_signed_mint_authorization, fetched fresh immediately before this
    call — never fabricated, guessed, or reused from a prior attempt. Per
    SeaDrop's own docs a signature is single-use; mint_params must be the
    EXACT values the signer signed over, or the on-chain signature
    recovery fails and the transaction reverts (a safe, definite failure —
    see ethClient.ts's fireSignedMint docstring).

    mint_params must have exactly these keys (all numeric-string except
    restrictFeeRecipients): mintPrice, maxTotalMintableByWallet, startTime,
    endTime, dropStageIndex, maxTokenSupplyForStage, feeBps,
    restrictFeeRecipients (bool).

    Same result shape / RuntimeError resilience as fire_mint.
    """
    payload = {
        "sessionPrivateKey": session_private_key,
        "nftContract": nft_contract_address,
        "quantity": quantity,
        "valueCapWei": value_cap_wei,
        "mintParams": mint_params,
        "salt": salt,
        "signature": signature,
    }

    try:
        r = _req.post(f"{_cfg.NODE_HELPER_URL}/eth/fire-signed-mint", json=payload, timeout=90)
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
        logger.warning("[node_client] fire-signed-mint request-level failure: %s", error_msg)
        raise RuntimeError(error_msg)

    return result
