import base64
import json
import os
import requests as _req
from . import config as _cfg


def _load_session():
    """Read and decrypt the session key from .upvote_session file."""
    from Crypto.Cipher import AES

    enc_key_hex = os.getenv("SESSION_KEY_ENCRYPTION_KEY", "")
    if len(enc_key_hex) != 64:
        raise RuntimeError(
            "SESSION_KEY_ENCRYPTION_KEY not set or invalid (need 64-char hex). "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    if not os.path.exists(_cfg.SESSION_FILE):
        raise RuntimeError(
            f"No session file found. Authorize first at /portal-upvote"
        )

    with open(_cfg.SESSION_FILE) as f:
        data = json.load(f)

    raw            = base64.b64decode(data["encrypted_key"])
    nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
    cipher         = AES.new(bytes.fromhex(enc_key_hex), AES.MODE_EAX, nonce=nonce)
    session_priv   = cipher.decrypt_and_verify(ct, tag).decode()

    return session_priv, data.get("session_config", "{}"), data.get("agw_address", _cfg.AGW_ADDRESS)


def call_upvote(app_id):
    """
    Decrypt session key, forward upvote request to Node helper.
    Returns {"txHash": "0x..."} or raises RuntimeError.
    """
    session_priv, session_config, agw_address = _load_session()

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


def node_health():
    try:
        r = _req.get(f"{_cfg.NODE_HELPER_URL}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
