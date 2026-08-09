"""
Security primitives for the OpenSea Auto-Mint session-grant flow.

validate_session_grant_input mirrors portal_upvote/security.py's
validate_session_input style exactly: strict format/bounds checks BEFORE
any crypto/DB touch, returning an error string on failure or None on
success, failing fast on the first violation found.
"""

import re
import time
from typing import Optional

# ── Patterns ──────────────────────────────────────────────────────────────────
# Self-contained (not imported from portal_upvote) — a one-line regex doesn't
# warrant a cross-module dependency between otherwise-independent features.
ETH_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# ── Bounds ────────────────────────────────────────────────────────────────────
# The frontend's serialized ZeroDev approval blob (embedded session private
# key + permission config) has been observed at ~3300 chars in practice.
# 20,000 is generously above that so a legitimate approval never gets
# rejected, while still catching an absurdly oversized payload (buggy
# client, abuse attempt) before it reaches encryption/DB.
_MAX_SERIALIZED_APPROVAL_LEN = 20_000

# Real grants use exactly 2 targets (the SeaDrop contract + the specific NFT
# contract). 5 is a sane upper bound above that, not a hard product rule.
_MAX_TARGETS = 5

_MAX_FUNCTION_NAME_LEN = 100

_MIN_MAX_QUANTITY = 1
_MAX_MAX_QUANTITY = 1000

# Sanity ceiling against typos/overflow in valueCapWei, not a real product
# limit — 10 ETH in wei.
_MAX_VALUE_CAP_WEI = 10 * 10**18

# Mirrors portal_upvote/security.py's validate_session_input 32-day-max
# pattern; 30 days chosen here since session-key grants are meant to be
# short-lived, narrowly-scoped permissions, not long-standing approvals.
_MAX_EXPIRES_AT_SECONDS_FROM_NOW = 30 * 86400


def _validate_targets(targets: object) -> Optional[str]:
    if not isinstance(targets, list) or len(targets) == 0:
        return "targets must be a non-empty list"
    if len(targets) > _MAX_TARGETS:
        return f"targets exceeds maximum allowed ({_MAX_TARGETS})"
    for target in targets:
        if not isinstance(target, str) or not ETH_ADDR_RE.match(target):
            return "Invalid target address format"
    return None


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


def validate_session_grant_input(body: dict) -> Optional[str]:
    """Validate a session-grant POST body BEFORE any crypto/DB touch. Returns
    an error message string on failure, None on success. Mirrors
    portal_upvote/security.py's validate_session_input style exactly."""
    owner_address = body.get("ownerAddress", "")
    if not isinstance(owner_address, str) or not ETH_ADDR_RE.match(owner_address):
        return "Invalid ownerAddress format"

    smart_account_address = body.get("smartAccountAddress", "")
    if not isinstance(smart_account_address, str) or not ETH_ADDR_RE.match(smart_account_address):
        return "Invalid smartAccountAddress format"

    serialized_approval = body.get("serializedApproval", "")
    if not isinstance(serialized_approval, str) or not serialized_approval:
        return "Missing serializedApproval"
    if len(serialized_approval) > _MAX_SERIALIZED_APPROVAL_LEN:
        return "serializedApproval payload too large"

    targets_error = _validate_targets(body.get("targets"))
    if targets_error:
        return targets_error

    function_name = body.get("functionName", "")
    if not isinstance(function_name, str) or not function_name:
        return "Missing functionName"
    if len(function_name) > _MAX_FUNCTION_NAME_LEN:
        return "functionName too long"

    max_quantity_error = _validate_max_quantity(body.get("maxQuantity"))
    if max_quantity_error:
        return max_quantity_error

    value_cap_error = _validate_value_cap_wei(body.get("valueCapWei"))
    if value_cap_error:
        return value_cap_error

    expires_at_error = _validate_expires_at(body.get("expiresAt"))
    if expires_at_error:
        return expires_at_error

    return None


# Self-contained (matches this module's existing style of not importing a
# one-line regex from collection_details for a single cross-module use) —
# same pattern collection_details.SLUG_RE uses.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")


def _validate_collection_slug(collection_slug: object) -> Optional[str]:
    if not isinstance(collection_slug, str) or not SLUG_RE.match(collection_slug):
        return "Invalid collectionSlug format"
    return None


# ECDSA signature format: 0x + r(32 bytes) + s(32 bytes) + v(1 byte) = 65
# bytes = 130 hex chars — the standard personal_sign/eth_sign output shape.
SIGNATURE_RE = re.compile(r"^0x[0-9a-fA-F]{130}$")

_MAX_MESSAGE_LEN = 500  # generous over the actual built message length; catches abuse, not legitimate use


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
    exact fields (see firing.build_arm_message) — format-checked here,
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
    the URL path, not the body — see firing.build_cancel_message."""
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
