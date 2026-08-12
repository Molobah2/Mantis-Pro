import time

import pytest

from opensea_automint import security

OWNER_ADDRESS = "0x" + "a1" * 20
SESSION_ADDRESS = "0x" + "b2" * 20
NFT_CONTRACT = "0x" + "d4" * 20
VALID_SIGNATURE = "0x" + "ab" * 65
SESSION_PRIVATE_KEY = "0x" + "cd" * 32


def _valid_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "sessionAddress": SESSION_ADDRESS,
        "sessionPrivateKey": SESSION_PRIVATE_KEY,
        "nftContract": NFT_CONTRACT,
        "maxQuantity": 3,
        "valueCapWei": "50000000000000000",
        "expiresAt": time.time() + 3600,
        "signature": VALID_SIGNATURE,
        "timestamp": time.time(),
    }


# ── Happy path ────────────────────────────────────────────────────────────

def test_valid_full_payload_returns_none() -> None:
    assert security.validate_session_grant_input(_valid_payload()) is None


# ── ownerAddress ─────────────────────────────────────────────────────────

def test_bad_owner_address_format_returns_error() -> None:
    body = _valid_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_session_grant_input(body) is not None


# ── sessionAddress ────────────────────────────────────────────────────────

def test_bad_session_address_format_returns_error() -> None:
    body = _valid_payload()
    body["sessionAddress"] = "not-an-address"

    assert security.validate_session_grant_input(body) is not None


# ── sessionPrivateKey ─────────────────────────────────────────────────────

def test_missing_session_private_key_returns_error() -> None:
    body = _valid_payload()
    del body["sessionPrivateKey"]

    assert security.validate_session_grant_input(body) is not None


def test_empty_session_private_key_returns_error() -> None:
    body = _valid_payload()
    body["sessionPrivateKey"] = ""

    assert security.validate_session_grant_input(body) is not None


def test_session_private_key_wrong_length_returns_error() -> None:
    body = _valid_payload()
    body["sessionPrivateKey"] = "0x" + "cd" * 31

    assert security.validate_session_grant_input(body) is not None


def test_session_private_key_missing_0x_prefix_returns_error() -> None:
    body = _valid_payload()
    body["sessionPrivateKey"] = "cd" * 32

    assert security.validate_session_grant_input(body) is not None


# ── nftContract ───────────────────────────────────────────────────────────

def test_missing_nft_contract_returns_error() -> None:
    body = _valid_payload()
    del body["nftContract"]

    assert security.validate_session_grant_input(body) is not None


def test_bad_nft_contract_format_returns_error() -> None:
    body = _valid_payload()
    body["nftContract"] = "not-an-address"

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


# ── grant signature/timestamp ───────────────────────────────────────────────

@pytest.mark.parametrize("bad_sig", ["", "not-hex", "0x" + "ab" * 64, "0x" + "zz" * 65, None, 12345])
def test_grant_invalid_signature_returns_error(bad_sig: object) -> None:
    body = _valid_payload()
    body["signature"] = bad_sig

    assert security.validate_session_grant_input(body) is not None


@pytest.mark.parametrize("bad_ts", [0, -1, "not-a-number", None, True])
def test_grant_invalid_timestamp_returns_error(bad_ts: object) -> None:
    body = _valid_payload()
    body["timestamp"] = bad_ts

    assert security.validate_session_grant_input(body) is not None


# ── validate_arm_input ────────────────────────────────────────────────────

def _valid_arm_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "collectionSlug": "cool-drop",
        "quantity": 2,
        "maxPriceWei": "50000000000000000",
        "signature": VALID_SIGNATURE,
        "timestamp": time.time(),
    }


def test_valid_arm_payload_returns_none() -> None:
    assert security.validate_arm_input(_valid_arm_payload()) is None


def test_valid_arm_payload_with_stage_label_returns_none() -> None:
    body = _valid_arm_payload()
    body["stageLabel"] = "GTD"

    assert security.validate_arm_input(body) is None


def test_arm_missing_stage_label_is_valid_defaults_to_public() -> None:
    body = _valid_arm_payload()
    assert "stageLabel" not in body

    assert security.validate_arm_input(body) is None


def test_arm_empty_string_stage_label_is_valid() -> None:
    body = _valid_arm_payload()
    body["stageLabel"] = ""

    assert security.validate_arm_input(body) is None


@pytest.mark.parametrize("bad_stage_label", [123, True, ["GTD"], {"name": "GTD"}])
def test_arm_non_string_stage_label_returns_error(bad_stage_label: object) -> None:
    body = _valid_arm_payload()
    body["stageLabel"] = bad_stage_label

    assert security.validate_arm_input(body) is not None


def test_arm_overlong_stage_label_returns_error() -> None:
    body = _valid_arm_payload()
    body["stageLabel"] = "x" * 201

    assert security.validate_arm_input(body) is not None


def test_arm_bad_owner_address_format_returns_error() -> None:
    body = _valid_arm_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_arm_input(body) is not None


@pytest.mark.parametrize("bad_slug", ["", "UPPERCASE", "has spaces", "../etc/passwd", None, 7])
def test_arm_invalid_collection_slug_returns_error(bad_slug: object) -> None:
    body = _valid_arm_payload()
    body["collectionSlug"] = bad_slug

    assert security.validate_arm_input(body) is not None


@pytest.mark.parametrize("bad_sig", ["", "not-hex", "0x" + "ab" * 64, "0x" + "zz" * 65, None, 12345])
def test_arm_invalid_signature_returns_error(bad_sig: object) -> None:
    body = _valid_arm_payload()
    body["signature"] = bad_sig

    assert security.validate_arm_input(body) is not None


@pytest.mark.parametrize("bad_ts", [0, -1, "not-a-number", None, True])
def test_arm_invalid_timestamp_returns_error(bad_ts: object) -> None:
    body = _valid_arm_payload()
    body["timestamp"] = bad_ts

    assert security.validate_arm_input(body) is not None


# ── validate_cancel_input ─────────────────────────────────────────────────

def _valid_cancel_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "signature": VALID_SIGNATURE,
        "timestamp": time.time(),
    }


def test_valid_cancel_payload_returns_none() -> None:
    assert security.validate_cancel_input(_valid_cancel_payload()) is None


def test_cancel_bad_owner_address_format_returns_error() -> None:
    body = _valid_cancel_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_cancel_input(body) is not None


@pytest.mark.parametrize("bad_sig", ["", "not-hex", None])
def test_cancel_invalid_signature_returns_error(bad_sig: object) -> None:
    body = _valid_cancel_payload()
    body["signature"] = bad_sig

    assert security.validate_cancel_input(body) is not None


@pytest.mark.parametrize("bad_ts", [0, -1, None])
def test_cancel_invalid_timestamp_returns_error(bad_ts: object) -> None:
    body = _valid_cancel_payload()
    body["timestamp"] = bad_ts

    assert security.validate_cancel_input(body) is not None


# ── validate_revoke_grant_input ──────────────────────────────────────────

def _valid_revoke_grant_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "signature": VALID_SIGNATURE,
        "timestamp": time.time(),
    }


def test_valid_revoke_grant_payload_returns_none() -> None:
    assert security.validate_revoke_grant_input(_valid_revoke_grant_payload()) is None


def test_revoke_grant_bad_owner_address_format_returns_error() -> None:
    body = _valid_revoke_grant_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_revoke_grant_input(body) is not None


@pytest.mark.parametrize("bad_sig", ["", "not-hex", None])
def test_revoke_grant_invalid_signature_returns_error(bad_sig: object) -> None:
    body = _valid_revoke_grant_payload()
    body["signature"] = bad_sig

    assert security.validate_revoke_grant_input(body) is not None


@pytest.mark.parametrize("bad_ts", [0, -1, None])
def test_revoke_grant_invalid_timestamp_returns_error(bad_ts: object) -> None:
    body = _valid_revoke_grant_payload()
    body["timestamp"] = bad_ts

    assert security.validate_revoke_grant_input(body) is not None


# ── validate_sweep_grant_input ────────────────────────────────────────────

def _valid_sweep_grant_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "signature": VALID_SIGNATURE,
        "timestamp": time.time(),
    }


def test_valid_sweep_grant_payload_returns_none() -> None:
    assert security.validate_sweep_grant_input(_valid_sweep_grant_payload()) is None


def test_sweep_grant_bad_owner_address_format_returns_error() -> None:
    body = _valid_sweep_grant_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_sweep_grant_input(body) is not None


@pytest.mark.parametrize("bad_sig", ["", "not-hex", None])
def test_sweep_grant_invalid_signature_returns_error(bad_sig: object) -> None:
    body = _valid_sweep_grant_payload()
    body["signature"] = bad_sig

    assert security.validate_sweep_grant_input(body) is not None


@pytest.mark.parametrize("bad_ts", [0, -1, None])
def test_sweep_grant_invalid_timestamp_returns_error(bad_ts: object) -> None:
    body = _valid_sweep_grant_payload()
    body["timestamp"] = bad_ts

    assert security.validate_sweep_grant_input(body) is not None


# ── validate_transfer_nft_input ───────────────────────────────────────────

def _valid_transfer_nft_payload() -> dict:
    return {
        "ownerAddress": OWNER_ADDRESS,
        "signature": VALID_SIGNATURE,
        "timestamp": time.time(),
    }


def test_valid_transfer_nft_payload_returns_none() -> None:
    assert security.validate_transfer_nft_input(_valid_transfer_nft_payload()) is None


def test_transfer_nft_bad_owner_address_format_returns_error() -> None:
    body = _valid_transfer_nft_payload()
    body["ownerAddress"] = "not-an-address"

    assert security.validate_transfer_nft_input(body) is not None


@pytest.mark.parametrize("bad_sig", ["", "not-hex", None])
def test_transfer_nft_invalid_signature_returns_error(bad_sig: object) -> None:
    body = _valid_transfer_nft_payload()
    body["signature"] = bad_sig

    assert security.validate_transfer_nft_input(body) is not None


@pytest.mark.parametrize("bad_ts", [0, -1, None])
def test_transfer_nft_invalid_timestamp_returns_error(bad_ts: object) -> None:
    body = _valid_transfer_nft_payload()
    body["timestamp"] = bad_ts

    assert security.validate_transfer_nft_input(body) is not None


@pytest.mark.parametrize("bad_quantity", [0, -1, 1001, "2", 2.5, None, True])
def test_arm_invalid_quantity_returns_error(bad_quantity: object) -> None:
    body = _valid_arm_payload()
    body["quantity"] = bad_quantity

    assert security.validate_arm_input(body) is not None


def test_arm_quantity_error_message_mentions_quantity_not_max_quantity() -> None:
    body = _valid_arm_payload()
    body["quantity"] = 0

    error = security.validate_arm_input(body)

    assert error is not None
    assert "quantity" in error
    assert "maxQuantity" not in error


@pytest.mark.parametrize("bad_price", ["not-a-number", "-1", None])
def test_arm_invalid_max_price_wei_returns_error(bad_price: object) -> None:
    body = _valid_arm_payload()
    body["maxPriceWei"] = bad_price

    assert security.validate_arm_input(body) is not None


def test_arm_max_price_wei_exceeding_ceiling_returns_error() -> None:
    body = _valid_arm_payload()
    body["maxPriceWei"] = str(11 * 10**18)  # above the 10 ETH sanity ceiling

    assert security.validate_arm_input(body) is not None


def test_arm_max_price_error_message_mentions_max_price_wei() -> None:
    body = _valid_arm_payload()
    body["maxPriceWei"] = "not-a-number"

    error = security.validate_arm_input(body)

    assert error is not None
    assert "maxPriceWei" in error
    assert "valueCapWei" not in error
