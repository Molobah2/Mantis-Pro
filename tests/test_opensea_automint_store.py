import time

import pytest

from opensea_automint import store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the store module at a fresh temp DB file for every test so tests
    never touch a real/shared DB file."""
    db_path = tmp_path / "opensea_automint_test.db"
    monkeypatch.setattr(store, "_DB", str(db_path))
    yield db_path


# ── Table creation ───────────────────────────────────────────────────

def test_conn_table_creation_is_idempotent() -> None:
    c1 = store._conn()
    c1.close()
    # Calling _conn() again must not raise (CREATE TABLE IF NOT EXISTS + guarded ALTER TABLE)
    c2 = store._conn()
    c2.close()


# ── Session grants ───────────────────────────────────────────────────
# Note: this project originally used a `smart_accounts` cache table (ERC-4337
# ZeroDev smart-account address derivation). Replaced by direct-signed-
# transaction firing from a plain session EOA — session_grants.session_address
# IS that key's own address; no separate smart-account concept exists anymore.

def test_session_grant_insert_and_get_active_round_trips() -> None:
    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address="0xOwner2",
        session_address="0xSession2",
        encrypted_session_key="encrypted-blob",
        permission_config='{"scope":"mint"}',
        allowed_targets='["0xCollection"]',
        value_cap_wei="1000000000000000000",
        expires_at=time.time() + 3600,
    ))

    active = store.get_active_session_grant("0xOwner2")

    assert isinstance(grant_id, int)
    assert active is not None
    assert active["id"] == grant_id
    assert active["session_address"] == "0xSession2"
    assert active["encrypted_session_key"] == "encrypted-blob"
    assert active["revoked"] == 0


def test_get_active_session_grant_excludes_revoked_grants() -> None:
    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address="0xOwner3",
        session_address="0xSession3",
        encrypted_session_key="key",
        permission_config="{}",
        allowed_targets="[]",
        value_cap_wei="0",
        expires_at=time.time() + 3600,
    ))
    store.revoke_session_grant(grant_id)

    active = store.get_active_session_grant("0xOwner3")

    assert active is None


def test_try_revoke_session_grant_returns_true_on_first_call() -> None:
    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address="0xOwner3b",
        session_address="0xSession3b",
        encrypted_session_key="key",
        permission_config="{}",
        allowed_targets="[]",
        value_cap_wei="0",
        expires_at=time.time() + 3600,
    ))

    result = store.try_revoke_session_grant(grant_id)

    assert result is True
    assert store.get_session_grant(grant_id)["revoked"] == 1


def test_try_revoke_session_grant_returns_false_when_already_revoked() -> None:
    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address="0xOwner3c",
        session_address="0xSession3c",
        encrypted_session_key="key",
        permission_config="{}",
        allowed_targets="[]",
        value_cap_wei="0",
        expires_at=time.time() + 3600,
    ))
    assert store.try_revoke_session_grant(grant_id) is True

    result = store.try_revoke_session_grant(grant_id)

    assert result is False


def test_try_revoke_session_grant_returns_false_for_unknown_id() -> None:
    assert store.try_revoke_session_grant(999999) is False


def test_get_active_session_grant_excludes_expired_grants() -> None:
    store.insert_session_grant(store.SessionGrantInput(
        owner_address="0xOwner4",
        session_address="0xSession4",
        encrypted_session_key="key",
        permission_config="{}",
        allowed_targets="[]",
        value_cap_wei="0",
        expires_at=time.time() - 3600,  # already expired
    ))

    active = store.get_active_session_grant("0xOwner4")

    assert active is None


# ── Tracked drops ─────────────────────────────────────────────────────

def test_tracked_drop_upsert_by_slug_updates_existing_row_not_duplicate() -> None:
    id1 = store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="cool-drop",
        name="Cool Drop v1",
        contract_address="0xContract1",
        mint_page_url="https://opensea.io/collection/cool-drop",
        source="api",
        stage_data="{}",
    ))
    id2 = store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="cool-drop",
        name="Cool Drop v2",
        contract_address="0xContract1Updated",
        mint_page_url="https://opensea.io/collection/cool-drop",
        source="playwright",
        stage_data='{"stage":"live"}',
    ))

    drops = store.get_tracked_drops()

    assert id1 == id2
    assert len(drops) == 1
    assert drops[0]["name"] == "Cool Drop v2"
    assert drops[0]["contract_address"] == "0xContract1Updated"
    assert drops[0]["source"] == "playwright"


def test_get_tracked_drop_by_id_round_trips() -> None:
    drop_id = store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="another-drop",
        name="Another Drop",
        contract_address="0xContract2",
        mint_page_url="https://opensea.io/collection/another-drop",
        source="manual",
        stage_data="{}",
    ))

    result = store.get_tracked_drop(drop_id)

    assert result is not None
    assert result["collection_slug"] == "another-drop"


def test_get_tracked_drop_returns_none_when_not_found() -> None:
    assert store.get_tracked_drop(999999) is None


# ── Arm requests ──────────────────────────────────────────────────────

def test_arm_request_status_transitions_and_get() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner5",
        drop_id=1,
        session_grant_id=1,
        quantity=2,
        max_price_wei="500000000000000000",
        go_live_at=time.time() + 60,
    ))

    initial = store.get_arm_request(arm_id)
    assert initial["status"] == "pending_schedule"

    store.update_arm_request_status(arm_id, "armed")
    updated = store.get_arm_request(arm_id)
    assert updated["status"] == "armed"


def test_get_pending_arm_requests_filters_by_status() -> None:
    # Distinct drop_ids per row — idx_arm_requests_active_owner_drop only
    # allows ONE non-terminal arm request per (owner, drop) pair now, so
    # these can't all target the same drop.
    pending_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner6", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    scheduled_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner6", drop_id=2, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(scheduled_id, "scheduled")

    fired_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner6", drop_id=3, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(fired_id, "fired")

    pending = store.get_pending_arm_requests()
    pending_ids = {r["id"] for r in pending}

    assert pending_id in pending_ids
    assert scheduled_id in pending_ids
    assert fired_id not in pending_ids


def test_get_arm_request_returns_none_when_not_found() -> None:
    assert store.get_arm_request(999999) is None


def test_get_pending_arm_requests_includes_armed_status() -> None:
    armed_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner6b", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(armed_id, "armed")

    pending_ids = {r["id"] for r in store.get_pending_arm_requests()}

    assert armed_id in pending_ids


def test_get_active_arm_request_for_drop_finds_non_terminal_request() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner7", drop_id=42, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    result = store.get_active_arm_request_for_drop("0xOwner7", 42)

    assert result is not None
    assert result["id"] == arm_id


def test_get_active_arm_request_for_drop_is_case_insensitive_on_owner() -> None:
    store.create_arm_request(store.ArmRequestInput(
        owner_address="0xowner7lower", drop_id=43, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    result = store.get_active_arm_request_for_drop("0xOwner7Lower", 43)

    assert result is not None


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled", "expired"])
def test_get_active_arm_request_for_drop_excludes_terminal_statuses(terminal_status: str) -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner8", drop_id=44, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(arm_id, terminal_status)

    result = store.get_active_arm_request_for_drop("0xOwner8", 44)

    assert result is None


def test_get_active_arm_request_for_drop_returns_none_when_none_exists() -> None:
    assert store.get_active_arm_request_for_drop("0xNoSuchOwner", 999) is None


def test_try_claim_arm_request_succeeds_when_status_is_armed() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner9", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(arm_id, "armed")

    claimed = store.try_claim_arm_request(arm_id)

    assert claimed is True
    assert store.get_arm_request(arm_id)["status"] == "fired"


def test_try_claim_arm_request_fails_when_status_is_not_armed() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner10", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    # status is 'pending_schedule', not 'armed'

    claimed = store.try_claim_arm_request(arm_id)

    assert claimed is False
    assert store.get_arm_request(arm_id)["status"] == "pending_schedule"


def test_try_claim_arm_request_only_one_of_two_concurrent_claims_succeeds() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner11", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(arm_id, "armed")

    first_claim = store.try_claim_arm_request(arm_id)
    second_claim = store.try_claim_arm_request(arm_id)  # simulates a second concurrent tick

    assert first_claim is True
    assert second_claim is False


def test_try_cancel_arm_request_succeeds_for_pending_request() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner12", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    cancelled = store.try_cancel_arm_request(arm_id)

    assert cancelled is True
    assert store.get_arm_request(arm_id)["status"] == "cancelled"


def test_try_cancel_arm_request_fails_once_already_fired() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner13", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.update_arm_request_status(arm_id, "fired")

    cancelled = store.try_cancel_arm_request(arm_id)

    assert cancelled is False
    assert store.get_arm_request(arm_id)["status"] == "fired"


def test_get_session_grant_round_trips_regardless_of_revoked_status() -> None:
    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address="0xOwner14",
        session_address="0xSession14",
        encrypted_session_key="key",
        permission_config="{}",
        allowed_targets="[]",
        value_cap_wei="0",
        expires_at=time.time() + 3600,
    ))
    store.revoke_session_grant(grant_id)

    result = store.get_session_grant(grant_id)

    assert result is not None
    assert result["id"] == grant_id
    assert result["revoked"] == 1


def test_get_session_grant_returns_none_when_not_found() -> None:
    assert store.get_session_grant(999999) is None


# ── Mint attempts ─────────────────────────────────────────────────────

def test_record_and_get_mint_attempts() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner7", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash="0xTx1", user_op_hash="0xOp1",
        status="succeeded", error_message=None, gas_used="21000", block_number=12345,
    ))
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash=None, user_op_hash="0xOp2",
        status="failed", error_message="insufficient funds", gas_used=None, block_number=None,
    ))

    attempts = store.get_mint_attempts(arm_id)

    assert len(attempts) == 2
    assert attempts[0]["status"] == "succeeded"
    assert attempts[1]["status"] == "failed"
    assert attempts[1]["error_message"] == "insufficient funds"


def test_get_mint_attempts_returns_empty_list_when_none_recorded() -> None:
    assert store.get_mint_attempts(999999) == []


def test_record_mint_attempt_round_trips_fired_at_and_latency_ms() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner7b", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    fired_at = time.time()

    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash="0xTx1", user_op_hash=None,
        status="success", error_message=None, gas_used="21000", block_number=12345,
        fired_at=fired_at, latency_ms=842,
    ))

    attempts = store.get_mint_attempts(arm_id)

    assert len(attempts) == 1
    assert attempts[0]["fired_at"] == fired_at
    assert attempts[0]["latency_ms"] == 842


def test_record_mint_attempt_fired_at_and_latency_ms_default_to_none() -> None:
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address="0xOwner7c", drop_id=1, session_grant_id=1,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash=None, user_op_hash=None,
        status="error", error_message="could not decrypt session key",
        gas_used=None, block_number=None,
    ))

    attempts = store.get_mint_attempts(arm_id)

    assert attempts[0]["fired_at"] is None
    assert attempts[0]["latency_ms"] is None


# ── Mint history ──────────────────────────────────────────────────────

def _make_history_fixture(
    owner: str = "0xHistoryOwner1", slug: str = "history-drop-1", quantity: int = 2,
    status: str = "success", fired_at: float | None = None, latency_ms: int | None = None,
) -> int:
    """Sets up one full drop -> grant -> arm -> mint_attempt chain, as
    get_mint_history's join expects, and returns the arm_request id."""
    drop_id = store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug=slug, name="History Drop", contract_address="0xHistoryContract",
        mint_page_url="https://opensea.io/collection/" + slug,
        source="playwright", stage_data="{}",
    ))
    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address=owner, session_address="0xHistorySession",
        encrypted_session_key="key", permission_config="{}",
        allowed_targets="[]", value_cap_wei="0", expires_at=time.time() + 3600,
    ))
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=owner, drop_id=drop_id, session_grant_id=grant_id,
        quantity=quantity, max_price_wei="0", go_live_at=None,
    ))
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash="0xHistoryTx", user_op_hash=None,
        status=status, error_message=None, gas_used="21000", block_number=1,
        fired_at=fired_at, latency_ms=latency_ms,
    ))
    return arm_id


def test_get_mint_history_returns_a_successful_mint_with_joined_fields() -> None:
    _make_history_fixture(
        owner="0xHistoryOwnerA", slug="history-drop-a", quantity=3,
        fired_at=1700000000.123, latency_ms=500,
    )

    history = store.get_mint_history()
    row = next(r for r in history if r["collection_slug"] == "history-drop-a")

    assert row["name"] == "History Drop"
    assert row["owner_address"] == "0xhistoryownera"
    assert row["quantity"] == 3
    assert row["tx_hash"] == "0xHistoryTx"
    assert row["fired_at"] == 1700000000.123
    assert row["latency_ms"] == 500


def test_get_mint_history_excludes_failed_attempts() -> None:
    _make_history_fixture(slug="history-drop-failed", status="failed")

    history = store.get_mint_history()

    assert all(r["collection_slug"] != "history-drop-failed" for r in history)


def test_get_mint_history_filters_by_owner() -> None:
    _make_history_fixture(owner="0xHistoryOwnerB1", slug="history-drop-b1")
    _make_history_fixture(owner="0xHistoryOwnerB2", slug="history-drop-b2")

    history = store.get_mint_history(owner_address="0xHistoryOwnerB1")

    slugs = {r["collection_slug"] for r in history}
    assert "history-drop-b1" in slugs
    assert "history-drop-b2" not in slugs


def test_get_mint_history_is_newest_first() -> None:
    _make_history_fixture(owner="0xHistoryOwnerC", slug="history-drop-c1")
    _make_history_fixture(owner="0xHistoryOwnerC", slug="history-drop-c2")

    history = store.get_mint_history(owner_address="0xHistoryOwnerC")

    assert [r["collection_slug"] for r in history][:2] == ["history-drop-c2", "history-drop-c1"]


def test_get_mint_history_respects_limit() -> None:
    for i in range(5):
        _make_history_fixture(owner="0xHistoryOwnerD", slug=f"history-drop-d{i}")

    history = store.get_mint_history(owner_address="0xHistoryOwnerD", limit=2)

    assert len(history) == 2


def test_get_mint_history_returns_empty_list_when_none_recorded() -> None:
    assert store.get_mint_history(owner_address="0xNoHistoryOwner") == []


# ── Eligibility cache ─────────────────────────────────────────────────

def test_eligibility_upsert_and_get_round_trips() -> None:
    store.upsert_eligibility(store.EligibilityInput(
        drop_id=1, owner_address="0xOwner8", is_eligible=True,
        merkle_proof='["0xproof1","0xproof2"]', phase_id="public", source="api",
    ))

    result = store.get_eligibility(1, "0xOwner8")

    assert result is not None
    assert result["is_eligible"] is True
    assert result["merkle_proof"] == '["0xproof1","0xproof2"]'
    assert result["phase_id"] == "public"


def test_eligibility_upsert_with_unknown_status_stores_none() -> None:
    store.upsert_eligibility(store.EligibilityInput(
        drop_id=2, owner_address="0xOwner9", is_eligible=None,
        merkle_proof=None, phase_id=None, source="api",
    ))

    result = store.get_eligibility(2, "0xOwner9")

    assert result is not None
    assert result["is_eligible"] is None


def test_eligibility_upsert_updates_existing_row() -> None:
    store.upsert_eligibility(store.EligibilityInput(
        drop_id=3, owner_address="0xOwner10", is_eligible=None,
        merkle_proof=None, phase_id=None, source="api",
    ))
    store.upsert_eligibility(store.EligibilityInput(
        drop_id=3, owner_address="0xOwner10", is_eligible=False,
        merkle_proof=None, phase_id="whitelist", source="playwright",
    ))

    result = store.get_eligibility(3, "0xOwner10")

    assert result["is_eligible"] is False
    assert result["phase_id"] == "whitelist"
    assert result["source"] == "playwright"


def test_get_eligibility_returns_none_when_not_found() -> None:
    assert store.get_eligibility(999999, "0xNoSuchOwner") is None
