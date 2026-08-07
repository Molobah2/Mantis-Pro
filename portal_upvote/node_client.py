import base64
import binascii
import json
import logging
import os
import time

import requests as _req

from . import config as _cfg
from wallet_crypto import decrypt_secret

logger = logging.getLogger(__name__)


def _load_session() -> tuple[str, str, str]:
    """Read and decrypt the session key from .upvote_session file (owner / single-user path)."""
    enc_key_hex = os.getenv("SESSION_KEY_ENCRYPTION_KEY", "")
    if len(enc_key_hex) != 64:
        raise RuntimeError(
            "SESSION_KEY_ENCRYPTION_KEY not set or invalid (need 64-char hex). "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    if not os.path.exists(_cfg.SESSION_FILE):
        raise RuntimeError("No session file found. Authorize first at /portal-upvote")

    with open(_cfg.SESSION_FILE) as f:
        data = json.load(f)

    session_priv = decrypt_secret(data["encrypted_key"], key_hex=enc_key_hex)

    return session_priv, data.get("session_config", "{}"), data.get("agw_address", _cfg.AGW_ADDRESS)


def _decrypt_user_session(user: dict) -> tuple[str, str, str]:
    """Decrypt a user row's encrypted_key from the users DB table."""
    enc_key_hex = os.getenv("SESSION_KEY_ENCRYPTION_KEY", "")
    if len(enc_key_hex) != 64:
        raise RuntimeError("SESSION_KEY_ENCRYPTION_KEY not set or invalid")

    session_priv = decrypt_secret(user["encrypted_key"], key_hex=enc_key_hex)

    return session_priv, user.get("session_config", "{}"), user["address"]


def _post_upvote(session_priv: str, session_config: str, agw_address: str, app_id: int | str) -> dict:
    """Shared POST to the Node wallet-helper /upvote route. Raises RuntimeError on failure."""
    payload = {
        "sessionPrivKey": session_priv,
        "agwAddress":     agw_address,
        "sessionConfig":  session_config,
        "appId":          int(app_id),
        "network":        _cfg.NETWORK,
    }

    try:
        r = _req.post(f"{_cfg.NODE_HELPER_URL}/upvote", json=payload, timeout=30)
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    result = r.json()
    if r.status_code != 200 or result.get("error"):
        raise RuntimeError(result.get("error", f"HTTP {r.status_code}"))
    return result


def call_upvote(app_id: int | str) -> dict:
    """
    Decrypt session key (owner/file path), forward upvote request to Node helper.
    Returns {"txHash": "0x..."} or raises RuntimeError.
    """
    session_priv, session_config, agw_address = _load_session()
    return _post_upvote(session_priv, session_config, agw_address, app_id)


def call_upvote_for_user(user: dict, app_id: int | str) -> dict:
    """
    Decrypt session key from DB user row and forward upvote request to Node helper.
    user: dict with keys address, encrypted_key, session_config.
    Returns {"txHash": "0x..."} or raises RuntimeError.
    """
    session_priv, session_config, agw_address = _decrypt_user_session(user)
    return _post_upvote(session_priv, session_config, agw_address, app_id)


def node_health() -> bool:
    try:
        r = _req.get(f"{_cfg.NODE_HELPER_URL}/health", timeout=5)
        return r.status_code == 200
    except _req.exceptions.RequestException:
        return False


def restore_from_env() -> bool:
    """
    Restore session file from UPVOTE_SESSION_B64 env var on startup.
    The env var holds the raw session JSON as base64 — set it in Railway after
    authorizing via the browser so it survives container restarts.
    Returns True if session file is now present and not expired, False otherwise.
    """
    # Already have a valid session file — nothing to do.
    if os.path.exists(_cfg.SESSION_FILE):
        try:
            with open(_cfg.SESSION_FILE) as f:
                sess = json.load(f)
            if sess.get("expires_at", 0) > time.time():
                return True
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[upvote] existing session file unreadable: %s", e)

    b64 = os.getenv("UPVOTE_SESSION_B64", "").strip()
    if not b64:
        return False

    try:
        raw = base64.b64decode(b64)
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError, binascii.Error) as e:
        logger.warning("[upvote] UPVOTE_SESSION_B64 could not be decoded: %s", e)
        return False

    if data.get("expires_at", 0) <= time.time():
        logger.warning("[upvote] UPVOTE_SESSION_B64 is expired — re-authorize at /portal-upvote")
        return False

    try:
        with open(_cfg.SESSION_FILE, "w") as f:
            json.dump(data, f)
    except OSError as e:
        logger.warning("[upvote] could not write session file: %s", e)
        return False

    logger.info("[upvote] session restored from env var, expires=%s", data.get("expires_at"))
    return True
