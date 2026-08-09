"""
Security primitives for the OpenSea Auto-Mint tool's grant/arm/cancel flows.

Each validate_*_input function mirrors portal_upvote/security.py's
validate_session_input style exactly: strict format/bounds checks BEFORE
any crypto/DB touch, returning an error string on failure or None on
success, failing fast on the first violation found. Signature/timestamp
freshness (which needs a real RPC call and the current clock) is
deliberately NOT checked here — see firing.is_signature_timestamp_fresh and
node_client.verify_owner_signature, called by the route layer after these
format checks pass.
"""

import re
import time
from typing import Optional

# ── Patterns ──────────────────────────────────────────────────────────────────
# Self-contained (not imported from portal_upvote) — a one-line regex doesn't
# warrant a cross-module dependency between otherwise-independent features.
ETH_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# A raw secp256k1 private key: 0x + 32 bytes = 64 hex chars.
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# ECDSA signature format: 0x + r(32 bytes) + s(32 bytes) + v(1 byte) = 65
# bytes = 130 hex chars — the standard personal_sign/eth_sign output shape.
SIGNATURE_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")

# ── Bounds ────────────────────────────────────────────────────────────────────
_MIN_MAX_QUANTITY = 1
_MAX_MAX_QUANTITY = 1000

# Sanity ceiling against typos/overflow in valueCapWei, not a real product
# limit — 10 ETH in wei.
_MAX_VALUE_CAP_WEI = 10 * 10**18

# Mirrors portal_upvote/security.py's validate_session_input 32-day-max
# pattern; 30 days chosen here since session grants are meant to be
# short-lived, narrowly-scoped authorizations, not long-standing approvals.
_MAX_EXPIRES_AT_SECONDS_FROM_NOW = 30 * 86400


def _validate_max_quantity(max_quantity: object) -> Optional[str]:
    # bool is a subclass of int in Python (isinstance(True, int) is True),
    # so it must be excluded explicitly before the isinstance(int) check.
    if isinstance(max_quantity, bool) or not isinstance(max_quantity, int):
        return "maxQuantity must be an integer"
    if not (_MIN_MAX_QUANTITY <= max_quantity <= _MAX_MAX_QUANTITY):
        return f"maxQuantity must be between {_MIN_MAX_QUANTITY} and {_MAX_MAX_QUANTITY}"
    return None


def _validate_value_cap_wei(value_cap_wei: object) -> Optional[str]:
    try:
        parsed = int(value_cap_wei)
    except (TypeError, ValueError):
        return "valueCapWei must be a numeric string"
    if parsed < 0:
        return "valueCapWei must be non-negative"
    if parsed > _MAX_VALUE_CAP_WEI:
        return "valueCapWei exceeds maximum allowed"
    return None


def _validate_expires_at(expires_at: object) -> Optional[str]:
    # bool is a subclass of int/float-compatible here too — same trap as maxQuantity.
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        return "expiresAt must be a number"
    now = time.time()
    if expires_at <= now:
        return "Session grant is already expired"
    if expires_at > now + _MAX_EXPIRES_AT_SECONDS_FROM_NOW:
        return "Session grant expiry exceeds maximum allowed (30 days)"
    return None


def _validate_collection_slug(collection_slug: object) -> Optional[str]:
    if not isinstance(collection_slug, str) or not SLUG_RE.match(collection_slug):
        return "Invalid collectionSlug format"
    return None


def _validate_signature(signature: object) -> Optional[str]:
    if not isinstance(signature, str) or not SIGNATURE_RE.match(signature):
        return "Invalid signature format"
    return None


def _validate_timestamp(timestamp: object) -> Optional[str]:
    # bool is a subclass of int here too — same trap as maxQuantity/expiresAt.
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return "timestamp must be a number"
    if timestamp <= 0:
        return "timestamp must be positive"
    return None


def validate_session_grant_input(body: dict) -> Optional[str]:
    """Validate a session-grant POST body ({ownerAddress, sessionAddress,
    sessionPrivateKey, nftContract, maxQuantity, valueCapWei, expiresAt,
    signature, timestamp}) BEFORE any crypto/DB touch. Returns an error
    message string on failure, None on success.

    There is no on-chain enforcement of maxQuantity/valueCapWei/nftContract
    in this project's firing model (a direct signed transaction from the
    session key, not an ERC-4337 CallPolicy) — this validation, plus
    firing.py's pre-fire checks, are the only safety net. signature/
    timestamp authorize the grant itself: the caller must prove control of
    ownerAddress by signing a message built from these exact fields (see
    messages.build_grant_message) — format-checked here, actually verified
    against the chain (and checked for staleness) by the route layer."""
    owner_address = body.get("ownerAddress", "")
    if not isinstance(owner_address, str) or not ETH_ADDR_RE.match(owner_address):
        return "Invalid ownerAddress format"

    session_address = body.get("sessionAddress", "")
    if not isinstance(session_address, str) or not ETH_ADDR_RE.match(session_address):
        return "Invalid sessionAddress format"

    session_private_key = body.get("sessionPrivateKey", "")
    if not isinstance(session_private_key, str) or not PRIVATE_KEY_RE.match(session_private_key):
        return "Invalid sessionPrivateKey format"

    nft_contract = body.get("nftContract", "")
    if not isinstance(nft_contract, str) or not ETH_ADDR_RE.match(nft_contract):
        return "Invalid nftContract format"

    max_quantity_error = _validate_max_quantity(body.get("maxQuantity"))
    if max_quantity_error:
        return max_quantity_error

    value_cap_error = _validate_value_cap_wei(body.get("valueCapWei"))
    if value_cap_error:
        return value_cap_error

    expires_at_error = _validate_expires_at(body.get("expiresAt"))
    if expires_at_error:
        return expires_at_error

    signature_error = _validate_signature(body.get("signature"))
    if signature_error:
        return signature_error

    timestamp_error = _validate_timestamp(body.get("timestamp"))
    if timestamp_error:
        return timestamp_error

    return None


def validate_arm_input(body: dict) -> Optional[str]:
    """Validate an arm-request POST body ({ownerAddress, collectionSlug,
    quantity, maxPriceWei, signature, timestamp}) BEFORE any DB touch.
    Returns an error message string on failure, None on success. Reuses the
    same quantity/value-wei bounds as validate_session_grant_input —
    arming can never request more than a grant itself allows, but that
    cross-check (against the actual stored grant) happens in
    firing.arm_drop, not here; this only rejects malformed/out-of-sane-range
    input up front. collectionSlug (not an internal drop id) is what the
    frontend actually has — drops.py's to_display_dict deliberately never
    exposes the internal integer id.

    signature/timestamp together authorize the action: the caller must
    prove control of ownerAddress by signing a message built from these
    exact fields (see messages.build_arm_message) — format-checked here,
    actually verified against the chain (and checked for staleness) by the
    route layer, since that requires a real RPC call this module
    deliberately never makes (see this module's docstring)."""
    owner_address = body.get("ownerAddress", "")
    if not isinstance(owner_address, str) or not ETH_ADDR_RE.match(owner_address):
        return "Invalid ownerAddress format"

    slug_error = _validate_collection_slug(body.get("collectionSlug"))
    if slug_error:
        return slug_error

    quantity_error = _validate_max_quantity(body.get("quantity"))
    if quantity_error:
        return quantity_error.replace("maxQuantity", "quantity")

    max_price_error = _validate_value_cap_wei(body.get("maxPriceWei"))
    if max_price_error:
        return max_price_error.replace("valueCapWei", "maxPriceWei")

    signature_error = _validate_signature(body.get("signature"))
    if signature_error:
        return signature_error

    timestamp_error = _validate_timestamp(body.get("timestamp"))
    if timestamp_error:
        return timestamp_error

    return None


def validate_cancel_input(body: dict) -> Optional[str]:
    """Validate a cancel-request POST body ({ownerAddress, signature,
    timestamp}) BEFORE any DB touch. The arm request id itself comes from
    the URL path, not the body — see messages.build_cancel_message."""
    owner_address = body.get("ownerAddress", "")
    if not isinstance(owner_address, str) or not ETH_ADDR_RE.match(owner_address):
        return "Invalid ownerAddress format"

    signature_error = _validate_signature(body.get("signature"))
    if signature_error:
        return signature_error

    timestamp_error = _validate_timestamp(body.get("timestamp"))
    if timestamp_error:
        return timestamp_error

    return None
