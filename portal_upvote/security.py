"""
Security primitives for the Portal upvote system.

Layers:
  1. require_admin  — HMAC-verified X-Admin-Key header on owner-only endpoints
  2. rate_limit     — in-memory sliding-window per (IP, route key)
  3. validate_session_input — strict format checks before touching crypto / DB
  4. security_headers — hardening response headers (CSP, framing, MIME, etc.)
  5. get_client_ip  — real IP behind Railway's reverse proxy
  6. esc            — HTML-escape for safe innerHTML insertion
"""

import hashlib
import hmac
import os
import re
import threading
import time
from collections import defaultdict
from functools import wraps
from typing import Optional

# ── Patterns ──────────────────────────────────────────────────────────────────

ETH_ADDR_RE = re.compile(r'^0x[0-9a-fA-F]{40}$')
ETH_KEY_RE  = re.compile(r'^0x[0-9a-fA-F]{64}$')
TX_HASH_RE  = re.compile(r'^0x[0-9a-fA-F]{64}$')

# ── Rate limiter ──────────────────────────────────────────────────────────────

_buckets: dict = defaultdict(lambda: {"count": 0, "window_end": 0.0})
_rl_lock = threading.Lock()

def rate_limit(ip: str, key: str, limit: int, window: int) -> bool:
    """
    Sliding-window rate limiter. Returns True (allowed) or False (blocked).
    ip     — client IP address
    key    — route identifier, e.g. "register"
    limit  — max requests per window
    window — window size in seconds
    """
    bucket_key = f"{ip}:{key}"
    now = time.time()
    with _rl_lock:
        b = _buckets[bucket_key]
        if now >= b["window_end"]:
            b["count"] = 0
            b["window_end"] = now + window
        b["count"] += 1
        return b["count"] <= limit

def get_client_ip(request) -> str:
    """Extract real client IP, honouring Railway's X-Forwarded-For header."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"

# ── Admin auth ────────────────────────────────────────────────────────────────

def _admin_secret() -> str:
    return os.getenv("ADMIN_SECRET", "").strip()

def check_admin_key(provided: str) -> bool:
    """Constant-time comparison against ADMIN_SECRET. Always True if secret unset."""
    secret = _admin_secret()
    if not secret:
        return True  # backward compat — open if no secret configured
    # Compare SHA-256 digests to prevent timing leakage on different-length strings
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(secret.encode()).digest(),
    )

def require_admin(f):
    """
    Route decorator: require X-Admin-Key header.
    If ADMIN_SECRET env var is not set, the endpoint remains open (backward compat).
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        from flask import request, jsonify
        provided = request.headers.get("X-Admin-Key", "").strip()
        if not check_admin_key(provided):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

# ── Browser admin session (signed cookie) ───────────────────────────────────
# Separate from require_admin's X-Admin-Key header check: a plain page GET or a
# same-origin fetch() from the dashboard can't set custom headers, but browsers
# auto-attach cookies. Visiting a protected page with ?key=<ADMIN_SECRET> once
# issues this cookie; it then authenticates that page load and any same-origin
# fetch() calls (e.g. the "Claim Now" button) for its lifetime.

ADMIN_COOKIE_NAME = "mp_admin"
ADMIN_COOKIE_MAX_AGE = 7 * 86400  # 7 days

def _sign_admin_cookie(secret: str, ts: str) -> str:
    return hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()

def issue_admin_cookie(response, secret: str):
    """Attach a fresh signed admin-session cookie to a Flask response."""
    ts = str(int(time.time()))
    mac = _sign_admin_cookie(secret, ts)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        f"{ts}.{mac}",
        max_age=ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="Lax",
    )
    return response

def verify_admin_cookie(cookie_value: str) -> bool:
    """Check a signed admin-session cookie. Open if ADMIN_SECRET is unset (matches check_admin_key)."""
    secret = _admin_secret()
    if not secret:
        return True
    if not cookie_value or "." not in cookie_value:
        return False
    ts_str, mac = cookie_value.split(".", 1)
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    if time.time() - ts > ADMIN_COOKIE_MAX_AGE:
        return False
    expected = _sign_admin_cookie(secret, ts_str)
    return hmac.compare_digest(mac, expected)

# ── URL safety ───────────────────────────────────────────────────────────────

def require_loopback_url(url: str) -> Optional[str]:
    """Returns an error string if url isn't loopback, else None. Guards
    private-key POSTs from being redirected off-box by a misconfigured env var."""
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        return f"Refusing to send private key to non-loopback host: {host!r}"
    return None

def check_admin_login(request) -> bool:
    """
    True if the request already carries a valid admin cookie OR the correct
    ?key= query param (the login path — caller should then call issue_admin_cookie).
    """
    if verify_admin_cookie(request.cookies.get(ADMIN_COOKIE_NAME, "")):
        return True
    return check_admin_key(request.args.get("key", ""))

# ── Input validation ──────────────────────────────────────────────────────────

def validate_session_input(body: dict) -> Optional[str]:
    """
    Validate a store-session / register-session payload.
    Returns an error string on failure, None on success.
    """
    priv_key    = body.get("sessionPrivKey", "")
    agw_addr    = body.get("agwAddress", "")
    session_cfg = body.get("sessionConfig", "")
    expires_at  = body.get("expiresAt", 0)

    if not priv_key:
        return "Missing sessionPrivKey"
    if not agw_addr:
        return "Missing agwAddress"
    if not session_cfg:
        return "Missing sessionConfig"

    if not ETH_ADDR_RE.match(agw_addr):
        return "Invalid wallet address format"
    if not ETH_KEY_RE.match(priv_key):
        return "Invalid session key format"

    try:
        exp = float(expires_at)
    except (TypeError, ValueError):
        return "Invalid expiresAt value"

    now = time.time()
    if exp <= now:
        return "Session is already expired"
    if exp > now + 32 * 86400:  # 32 days max
        return "Session expiry exceeds maximum allowed (32 days)"

    cfg_str = str(session_cfg)
    if len(cfg_str) > 65_536:  # 64 KB hard limit
        return "sessionConfig payload too large"

    # Sanity-check that sessionConfig is valid JSON
    import json
    try:
        json.loads(cfg_str)
    except (ValueError, TypeError):
        return "sessionConfig must be valid JSON"

    return None

# ── Security headers ──────────────────────────────────────────────────────────

def security_headers(response):
    """
    Attach hardening headers to every response.
    Register with: app.after_request(security.security_headers)
    """
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"]        = "DENY"
    h["X-XSS-Protection"]       = "1; mode=block"
    h["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    h["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
    # CSP: permits inline scripts (needed for dashboard/register pages),
    # esm.sh CDN for wallet libs, WalletConnect relays, Abstract RPC.
    h["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://esm.sh https://fonts.googleapis.com "
            "https://*.privy.io https://privy.io; "
        "style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com https://fonts.gstatic.com "
            "https://*.privy.io; "
        "font-src 'self' https://fonts.gstatic.com data: https://*.privy.io; "
        "connect-src 'self' "
            "https://esm.sh "
            "https://api.mainnet.abs.xyz "
            "https://*.privy.io wss://*.privy.io "
            "https://privy.io wss://privy.io; "
        "frame-src 'self' https://*.privy.io https://privy.io; "
        "img-src 'self' data: https:; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    return response

# ── HTML escaper ─────────────────────────────────────────────────────────────

_ESC_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}

def esc(value) -> str:
    """Escape a value for safe insertion into HTML contexts (innerHTML)."""
    return "".join(_ESC_MAP.get(c, c) for c in str(value))
