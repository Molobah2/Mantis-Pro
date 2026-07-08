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


def ensure_session(renew_days_before_expiry=3):
    """
    Create or renew the AGW session using AGW_OWNER_PRIVATE_KEY.
    Runs on startup and before each daily upvote so the session is always fresh.
    No-ops if the session is valid and not expiring soon.
    Returns True if session is ready, raises RuntimeError on failure.
    """
    import time
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    agw_owner_key = os.getenv("AGW_OWNER_PRIVATE_KEY", "").strip()
    enc_key_hex   = os.getenv("SESSION_KEY_ENCRYPTION_KEY", "")
    if not agw_owner_key or len(enc_key_hex) != 64:
        return True  # credentials missing — fall through to manual session

    # Skip if session file is valid and not expiring soon
    if os.path.exists(_cfg.SESSION_FILE):
        try:
            with open(_cfg.SESSION_FILE) as f:
                sess = json.load(f)
            if sess.get("expires_at", 0) > time.time() + renew_days_before_expiry * 86400:
                return True
        except Exception:
            pass  # corrupt or unreadable — recreate below

    print("[upvote] creating/renewing AGW session via AGW_OWNER_PRIVATE_KEY...")
    try:
        r = _req.post(
            f"{_cfg.NODE_HELPER_URL}/create-session",
            json={"ownerPrivKey": agw_owner_key, "network": _cfg.NETWORK},
            timeout=120,
        )
    except _req.exceptions.ConnectionError:
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    data = r.json()
    if r.status_code != 200 or data.get("error"):
        raise RuntimeError(data.get("error", f"HTTP {r.status_code}"))

    key    = bytes.fromhex(enc_key_hex)
    nonce  = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_EAX, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(data["sessionPrivKey"].encode())
    encrypted = base64.b64encode(nonce + tag + ct).decode()

    payload = {
        "encrypted_key":  encrypted,
        "session_config": data["sessionConfig"],
        "agw_address":    data["agwAddress"],
        "expires_at":     data["expiresAt"],
        "created_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(_cfg.SESSION_FILE, "w") as f:
        json.dump(payload, f)
    print(f"[upvote] session ready: agw={data['agwAddress']} expires={data['expiresAt']}")
    return True
