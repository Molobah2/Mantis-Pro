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


def get_public_drop_window(nft_contract_address: str, chain: str = "ethereum") -> dict | None:
    """POST to the Node wallet-helper's /eth/public-drop-window route.

    Read-only, no-wallet lookup of a collection's real on-chain public mint
    window. Returns {"startTime": int, "endTime": int, "mintPriceWei": str}
    or None when the collection doesn't expose a public drop stage (not an
    error — e.g. allowlist-only collections, or one that hasn't configured
    a public stage yet). Raises RuntimeError only for actual Node-helper-
    level failures (connection refused, timeout, malformed response).

    chain selects which EVM chain to read from ("ethereum" or "robinhood")
    — see ethClient.ts's ChainName. Defaults to "ethereum" for callers that
    predate multi-chain support.
    """
    payload = {"nftContract": nft_contract_address, "chain": chain}

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


def get_recent_public_drop_updates(from_block: str | None) -> dict:
    """POST to the Node wallet-helper's /eth/recent-public-drop-updates
    route — discovers collections that have (re)configured their SeaDrop
    public mint stage on-chain since from_block (None means "start from a
    recent lookback", see ethClient.ts). This is the real source of truth
    for which collections are using SeaDrop, independent of whatever
    OpenSea's own /drops listing page happens to feature — used by
    drops.discover_new_seadrop_collections (a periodic background job),
    not anything time-critical.

    Returns {"updates": [{"nftContract": str, "startTime": int,
    "endTime": int, "mintPriceWei": str}, ...], "scannedToBlock": str}.
    Raises RuntimeError only for actual Node-helper-level failures.
    """
    payload = {"fromBlock": from_block}

    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/eth/recent-public-drop-updates", json=payload, timeout=30
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
        logger.warning("[node_client] recent-public-drop-updates failed: %s", error_msg)
        raise RuntimeError(error_msg)

    return result


def fire_mint(
    session_private_key: str,
    nft_contract_address: str,
    quantity: int,
    value_cap_wei: str,
    chain: str = "ethereum",
) -> dict:
    """POST to the Node wallet-helper's /eth/fire-mint route.

    THE ONE FUNCTION IN THIS MODULE THAT SPENDS REAL ETH. session_private_key
    must already be DECRYPTED plaintext — callers decrypt just before this
    call and must never persist the plaintext or log it. Fires a direct
    signed transaction from that key's own address — not sponsored through
    any smart account, so that key needs its own ETH balance.

    chain selects which EVM chain to fire on ("ethereum" or "robinhood") —
    the session key's address is the same on both (same secp256k1 curve),
    but it needs its own balance on whichever chain it's firing on.
    Defaults to "ethereum" for callers that predate multi-chain support.

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
        "chain": chain,
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
    chain: str = "ethereum",
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
        "chain": chain,
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


def sweep_session_key(
    session_private_key: str,
    destination_address: str,
    chain: str = "ethereum",
) -> dict:
    """POST to the Node wallet-helper's /eth/sweep route.

    Sends whatever ETH is left in a session key's own address back to
    destination_address (the owner's connected wallet) — the only way to
    recover funds left behind after a grant is revoked or superseded, since
    revoking has zero on-chain effect. session_private_key must already be
    DECRYPTED plaintext — callers decrypt just before this call and must
    never persist the plaintext or log it.

    Returns a dict shaped like:
        {"success": bool, "txHash": str|None, "amountSweptWei": str|None,
         "error": str|None}
    A balance too small to be worth sweeping (doesn't exceed the estimated
    gas cost) comes back as {"success": False, "error": "..."} — a real,
    non-exceptional outcome, not a Node-helper failure. Raises RuntimeError
    only for actual Node-helper-level failures (connection refused,
    timeout, malformed response).
    """
    payload = {
        "sessionPrivateKey": session_private_key,
        "destinationAddress": destination_address,
        "chain": chain,
    }

    try:
        r = _req.post(f"{_cfg.NODE_HELPER_URL}/eth/sweep", json=payload, timeout=90)
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
        logger.warning("[node_client] sweep request-level failure: %s", error_msg)
        raise RuntimeError(error_msg)

    return result


def transfer_minted_nft(
    session_private_key: str,
    nft_contract_address: str,
    mint_tx_hash: str,
    destination_address: str,
    chain: str = "ethereum",
) -> dict:
    """POST to the Node wallet-helper's /eth/transfer-minted-nft route.

    Sends every NFT the session key minted in mint_tx_hash to
    destination_address (the owner's connected wallet) — a session key only
    ever exists to fire the mint at speed, it was never meant to be where
    the NFT actually lives. tokenId is never trusted from the caller; the
    real source of truth is the mint transaction's own Transfer event
    log(s), read fresh from the chain by wallet-helper every time this
    runs. session_private_key must already be DECRYPTED plaintext —
    callers decrypt just before this call and must never persist the
    plaintext or log it.

    Returns a dict shaped like:
        {"success": bool, "transfers": [{"tokenId": str, "success": bool,
         "txHash": str|None, "error": str|None}, ...], "error": str|None}
    "error" at the top level is set only when NO transfer was ever
    attempted (e.g. the mint tx has no matching Transfer log) — a real,
    non-exceptional outcome, not a Node-helper failure. A quantity>1 mint
    can partially succeed; check each entry in "transfers" rather than
    just the top-level "success". Raises RuntimeError only for actual
    Node-helper-level failures (connection refused, timeout, malformed
    response).
    """
    payload = {
        "sessionPrivateKey": session_private_key,
        "nftContract": nft_contract_address,
        "mintTxHash": mint_tx_hash,
        "destinationAddress": destination_address,
        "chain": chain,
    }

    try:
        r = _req.post(f"{_cfg.NODE_HELPER_URL}/eth/transfer-minted-nft", json=payload, timeout=90)
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
        logger.warning("[node_client] transfer-minted-nft request-level failure: %s", error_msg)
        raise RuntimeError(error_msg)

    return result


def fire_raw_transaction(
    session_private_key: str,
    to: str,
    data: str,
    value_wei: str,
    chain: str = "ethereum",
) -> dict:
    """POST to the Node wallet-helper's /eth/fire-raw-transaction route.

    Signs and sends a transaction EXACTLY as built by OpenSea's own backend
    (opensea_session.fetch_mint_transaction_data) — no ABI encoding happens
    on our side at all. session_private_key must already be DECRYPTED
    plaintext — callers decrypt just before this call and must never
    persist the plaintext or log it.

    Returns a dict shaped like:
        {"success": bool, "txHash": str|None, "blockNumber": str|None,
         "gasUsed": str|None, "error": str|None, "ambiguous": bool|None}
    A failed/reverted/would-revert call (e.g. the signature was already
    consumed, the stage closed) comes back as {"success": False, "error":
    "..."} — a real, non-exceptional outcome, not a Node-helper failure.
    Raises RuntimeError only for actual Node-helper-level failures
    (connection refused, timeout, malformed response).
    """
    payload = {
        "sessionPrivateKey": session_private_key,
        "to": to,
        "data": data,
        "valueWei": value_wei,
        "chain": chain,
    }

    try:
        # Mirrors fire_mint's own extended timeout — a transaction
        # submission + receipt wait can legitimately take a while.
        r = _req.post(f"{_cfg.NODE_HELPER_URL}/eth/fire-raw-transaction", json=payload, timeout=90)
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
        logger.warning("[node_client] fire-raw-transaction request-level failure: %s", error_msg)
        raise RuntimeError(error_msg)

    return result
