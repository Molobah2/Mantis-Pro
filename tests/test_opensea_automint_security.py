import time

import pytest

from opensea_automint import security

OWNER_ADDRESS = "0x" + "a1" * 20
SMART_ACCOUNT_ADDRESS = "0x" + "b2" * 20
TARGET_1 = "0x" + "d4" * 20
TARGET_2 = "0x" + "c3" * 20


def _valid_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "smartAccountAddress": SMART_ACCOUNT_ADDRESS,
        "serializedApproval": "a" * 3300,
        "targets": [TARGET_1, TARGET_2],
        "functionName": "mintPublic",
        "maxQuantity": 3,
        "valueCapWei": "50000000000000000",
        "expiresAt": time.time() + 3600,
    }


# ── Happy path ────────────────────────────────────────────────────────────

def test_valid_full_payload_returns_none() -> None:
    assert security.validate_session_grant_input(_valid_payload()) is None


# ── ownerAddress ─────────────────────────────────────────────────────────

def test_bad_owner_address_format_returns_error() -> None:
    body = _valid_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_session_grant_input(body) is not None


# ── smartAccountAddress ──────────────────────────────────────────────────

def test_bad_smart_account_address_format_returns_error() -> None:
    body = _valid_payload()
    body["smartAccountAddress"] = "not-an-address"

    assert security.validate_session_grant_input(body) is not None


# ── serializedApproval ───────────────────────────────────────────────────

def test_missing_serialized_approval_returns_error() -> None:
    body = _valid_payload()
    del body["serializedApproval"]

    assert security.validate_session_grant_input(body) is not None


def test_empty_serialized_approval_returns_error() -> None:
    body = _valid_payload()
    body["serializedApproval"] = ""

    assert security.validate_session_grant_input(body) is not None


def test_oversized_serialized_approval_returns_error() -> None:
    body = _valid_payload()
    body["serializedApproval"] = "a" * 20_001

    assert security.validate_session_grant_input(body) is not None


# ── targets ───────────────────────────────────────────────────────────────

def test_empty_targets_list_returns_error() -> None:
    body = _valid_payload()
    body["targets"] = []

    assert security.validate_session_grant_input(body) is not None


def test_too_many_targets_returns_error() -> None:
    body = _valid_payload()
    body["targets"] = ["0x" + f"{i:02x}" * 20 for i in range(6)]

    assert security.validate_session_grant_input(body) is not None


def test_target_not_valid_address_returns_error() -> None:
    body = _valid_payload()
    body["targets"] = [TARGET_1, "not-an-address"]

    assert security.validate_session_grant_input(body) is not None


# ── functionName ─────────────────────────────────────────────────────────

def test_missing_function_name_returns_error() -> None:
    body = _valid_payload()
    del body["functionName"]

    assert security.validate_session_grant_input(body) is not None


def test_empty_function_name_returns_error() -> None:
    body = _valid_payload()
    body["functionName"] = ""

    assert security.validate_session_grant_input(body) is not None


def test_oversized_function_name_returns_error() -> None:
    body = _valid_payload()
    body["functionName"] = "a" * 101

    assert security.validate_session_grant_input(body) is not None


# ── maxQuantity ───────────────────────────────────────────────────────────

def test_max_quantity_true_bool_returns_error() -> None:
    body = _valid_payload()
    body["maxQuantity"] = True

    assert security.validate_session_grant_input(body) is not None


def test_max_quantity_false_bool_returns_error() -> None:
    body = _valid_payload()
    body["maxQuantity"] = False

    assert security.validate_session_grant_input(body) is not None


def test_max_quantity_zero_returns_error() -> None:
    body = _valid_payload()
    body["maxQuantity"] = 0

    assert security.validate_session_grant_input(body) is not None


def test_max_quantity_over_1000_returns_error() -> None:
    body = _valid_payload()
    body["maxQuantity"] = 1001

    assert security.validate_session_grant_input(body) is not None


# ── valueCapWei ───────────────────────────────────────────────────────────

def test_value_cap_wei_non_numeric_string_returns_error() -> None:
    body = _valid_payload()
    body["valueCapWei"] = "not-a-number"

    assert security.validate_session_grant_input(body) is not None


def test_value_cap_wei_exceeding_sanity_ceiling_returns_error() -> None:
    body = _valid_payload()
    body["valueCapWei"] = str(11 * 10**18)

    assert security.validate_session_grant_input(body) is not None


def test_value_cap_wei_negative_returns_error() -> None:
    body = _valid_payload()
    body["valueCapWei"] = "-1"

    assert security.validate_session_grant_input(body) is not None


# ── expiresAt ─────────────────────────────────────────────────────────────

def test_expires_at_true_bool_returns_error() -> None:
    body = _valid_payload()
    body["expiresAt"] = True

    assert security.validate_session_grant_input(body) is not None


def test_expires_at_in_past_returns_error() -> None:
    body = _valid_payload()
    body["expiresAt"] = time.time() - 3600

    assert security.validate_session_grant_input(body) is not None


def test_expires_at_more_than_30_days_out_returns_error() -> None:
    body = _valid_payload()
    body["expiresAt"] = time.time() + 31 * 86400

    assert security.validate_session_grant_input(body) is not None
