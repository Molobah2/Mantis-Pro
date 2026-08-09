import json
import time

import pytest

from opensea_automint import firing, store
from wallet_crypto import encrypt_secret

OWNER = "0xowner1234567890123456789012345678901234"
SMART_ACCOUNT = "0xsmartaccount1234567890123456789012345678"
CONTRACT = "0xcontract123456789012345678901234567890ab"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "opensea_automint_test.db"
    monkeypatch.setattr(store, "_DB", str(db_path))
    yield db_path


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SESSION_KEY_ENCRYPTION_KEY", "a" * 64)


def _make_drop(contract_address: str = CONTRACT, slug: str = "some-drop") -> str:
    """Returns the collection_slug — what firing.arm_drop actually takes
    (the frontend never sees the internal integer id). Use _drop_db_id(slug)
    when a test needs that internal id directly (e.g. to build an
    ArmRequestInput by hand for watcher-tick tests)."""
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug=slug, name="Some Drop", contract_address=contract_address,
        mint_page_url="https://opensea.io/collection/" + slug,
        source="playwright", stage_data="{}",
    ))
    return slug


def _drop_db_id(slug: str) -> int:
    return store.get_tracked_drop_by_slug(slug)["id"]


def _make_grant(
    owner: str = OWNER, targets=None, max_quantity: int = 5,
    value_cap_wei: str = "1000000000000000000", expires_at: float | None = None,
    encrypted_session_key: str | None = None,
) -> int:
    # Real ciphertext by default (round-trips through decrypt_secret cleanly)
    # so tests that reach the actual firing step don't fail on decryption
    # for reasons unrelated to what they're testing. Tests that specifically
    # want a decryption failure pass their own (invalid) value.
    if encrypted_session_key is None:
        encrypted_session_key = encrypt_secret("dummy-serialized-approval")
    targets = targets if targets is not None else [CONTRACT]
    return store.insert_session_grant(store.SessionGrantInput(
        owner_address=owner,
        smart_account_address=SMART_ACCOUNT,
        encrypted_session_key=encrypted_session_key,
        permission_config=json.dumps({"functionName": "mintPublic", "maxQuantity": max_quantity}),
        allowed_targets=json.dumps(targets),
        value_cap_wei=value_cap_wei,
        expires_at=expires_at if expires_at is not None else time.time() + 3600,
    ))


# ── message building / signature freshness ─────────────────────────────

def test_build_arm_message_is_deterministic() -> None:
    m1 = firing.build_arm_message("cool-drop", 2, "1000", 1700000000)
    m2 = firing.build_arm_message("cool-drop", 2, "1000", 1700000000)

    assert m1 == m2


def test_build_arm_message_changes_with_any_field() -> None:
    base = firing.build_arm_message("cool-drop", 2, "1000", 1700000000)

    assert firing.build_arm_message("other-drop", 2, "1000", 1700000000) != base
    assert firing.build_arm_message("cool-drop", 3, "1000", 1700000000) != base
    assert firing.build_arm_message("cool-drop", 2, "2000", 1700000000) != base
    assert firing.build_arm_message("cool-drop", 2, "1000", 1700000001) != base


def test_build_cancel_message_is_deterministic() -> None:
    m1 = firing.build_cancel_message(5, 1700000000)
    m2 = firing.build_cancel_message(5, 1700000000)

    assert m1 == m2


def test_build_cancel_message_changes_with_any_field() -> None:
    base = firing.build_cancel_message(5, 1700000000)

    assert firing.build_cancel_message(6, 1700000000) != base
    assert firing.build_cancel_message(5, 1700000001) != base


def test_is_signature_timestamp_fresh_accepts_current_timestamp() -> None:
    now = 1700000000.0
    assert firing.is_signature_timestamp_fresh(now, now=now) is True


def test_is_signature_timestamp_fresh_rejects_stale_timestamp() -> None:
    now = 1700000000.0
    stale = now - firing.SIGNATURE_MAX_AGE_SECONDS - 1

    assert firing.is_signature_timestamp_fresh(stale, now=now) is False


def test_is_signature_timestamp_fresh_rejects_future_timestamp() -> None:
    now = 1700000000.0
    future = now + firing.SIGNATURE_MAX_AGE_SECONDS + 1

    assert firing.is_signature_timestamp_fresh(future, now=now) is False


def test_is_signature_timestamp_fresh_accepts_boundary() -> None:
    now = 1700000000.0
    at_boundary = now - firing.SIGNATURE_MAX_AGE_SECONDS

    assert firing.is_signature_timestamp_fresh(at_boundary, now=now) is True


# ── arm_drop ─────────────────────────────────────────────────────────────

def test_arm_drop_succeeds_with_valid_grant_and_within_bounds() -> None:
    slug = _make_drop()
    _make_grant()

    result = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="100000000000000000")

    assert "armId" in result
    arm = store.get_arm_request(result["armId"])
    assert arm["status"] == "pending_schedule"
    assert arm["quantity"] == 2


def test_arm_drop_unknown_drop_returns_error() -> None:
    result = firing.arm_drop(OWNER, "no-such-slug", quantity=1, max_price_wei="0")

    assert "error" in result


def test_arm_drop_drop_without_contract_address_returns_error() -> None:
    slug = _make_drop(contract_address="")

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "error" in result


def test_arm_drop_no_active_grant_returns_error() -> None:
    slug = _make_drop()
    # no grant created

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "error" in result


def test_arm_drop_grant_not_covering_contract_returns_error() -> None:
    slug = _make_drop()
    _make_grant(targets=["0xSomeOtherContract"])

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "error" in result
    assert "does not cover" in result["error"]


def test_arm_drop_quantity_exceeding_grant_max_returns_error() -> None:
    slug = _make_drop()
    _make_grant(max_quantity=2)

    result = firing.arm_drop(OWNER, slug, quantity=3, max_price_wei="0")

    assert "error" in result
    assert "Quantity exceeds" in result["error"]


def test_arm_drop_total_price_exceeding_cap_returns_error() -> None:
    slug = _make_drop()
    _make_grant(value_cap_wei="1000")

    result = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="600")  # 1200 > 1000

    assert "error" in result
    assert "spend cap" in result["error"]


def test_arm_drop_second_arm_for_same_drop_returns_existing_arm_id() -> None:
    slug = _make_drop()
    _make_grant()
    first = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    second = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "error" in second
    assert second.get("armId") == first["armId"]


def test_arm_drop_db_constraint_prevents_duplicate_active_arm_even_if_precheck_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulates the actual TOCTOU race this guards against: the app-level
    # "already armed" check (get_active_arm_request_for_drop) is
    # bypassed/racy (as it could be under real concurrent requests, since
    # the check and the insert are two separate lock acquisitions, not one
    # transaction) — but the real safety net, a partial unique index on
    # (owner_address, drop_id) for non-terminal statuses, must still
    # prevent two active arm requests for the same (owner, drop).
    slug = _make_drop()
    _make_grant()
    real_lookup = store.get_active_arm_request_for_drop
    # The first TWO precheck calls (one per arm_drop invocation) are
    # bypassed — simulating both requests racing past the check before
    # either has inserted. The THIRD call is the fallback lookup inside
    # arm_drop's except-IntegrityError branch, which runs AFTER the DB
    # constraint has already done its job — that one uses the real
    # function, so it can find and report the row that actually won.
    calls = {"n": 0}

    def bypassed_for_the_race(owner, drop_id):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None
        return real_lookup(owner, drop_id)

    monkeypatch.setattr(store, "get_active_arm_request_for_drop", bypassed_for_the_race)

    first = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")
    second = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "armId" in first
    assert "error" in second
    assert second["armId"] == first["armId"]

    active_count = sum(
        1 for r in store.get_pending_arm_requests() if r["owner_address"] == OWNER.lower()
    )
    assert active_count == 1


def test_arm_drop_malformed_permission_config_returns_error() -> None:
    slug = _make_drop()
    store.insert_session_grant(store.SessionGrantInput(
        owner_address=OWNER, smart_account_address=SMART_ACCOUNT,
        encrypted_session_key="key", permission_config="not-json",
        allowed_targets=json.dumps([CONTRACT]), value_cap_wei="0",
        expires_at=time.time() + 3600,
    ))

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "error" in result


def test_arm_drop_lowercases_owner_address() -> None:
    slug = _make_drop()
    _make_grant(owner=OWNER.lower())

    result = firing.arm_drop(OWNER.upper().replace("0X", "0x"), slug, quantity=1, max_price_wei="0")

    assert "armId" in result


# ── cancel_arm ───────────────────────────────────────────────────────────

def test_cancel_arm_succeeds_for_pending_request() -> None:
    slug = _make_drop()
    _make_grant()
    arm_id = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")["armId"]

    result = firing.cancel_arm(arm_id, OWNER)

    assert result == {"cancelled": True}
    assert store.get_arm_request(arm_id)["status"] == "cancelled"


def test_cancel_arm_wrong_owner_returns_error() -> None:
    slug = _make_drop()
    _make_grant()
    arm_id = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")["armId"]

    result = firing.cancel_arm(arm_id, "0xSomeoneElse0000000000000000000000000000")

    assert "error" in result
    assert store.get_arm_request(arm_id)["status"] == "pending_schedule"


def test_cancel_arm_unknown_id_returns_error() -> None:
    result = firing.cancel_arm(999999, OWNER)

    assert "error" in result


def test_cancel_arm_already_fired_returns_error() -> None:
    slug = _make_drop()
    _make_grant()
    arm_id = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")["armId"]
    store.update_arm_request_status(arm_id, "fired")

    result = firing.cancel_arm(arm_id, OWNER)

    assert "error" in result


# ── get_arm_status_for_drop ────────────────────────────────────────────────

def test_get_arm_status_for_drop_returns_arm_and_attempts() -> None:
    slug = _make_drop()
    _make_grant()
    arm_id = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")["armId"]

    result = firing.get_arm_status_for_drop(OWNER, slug)

    assert result is not None
    assert result["arm"]["id"] == arm_id
    assert result["attempts"] == []


def test_get_arm_status_for_drop_returns_none_when_not_armed() -> None:
    slug = _make_drop()

    assert firing.get_arm_status_for_drop(OWNER, slug) is None


def test_get_arm_status_for_drop_returns_none_for_unknown_slug() -> None:
    assert firing.get_arm_status_for_drop(OWNER, "no-such-slug") is None


# ── check_and_fire_armed_requests / _check_and_fire_one ───────────────────

def test_watcher_marks_scheduled_when_window_not_yet_open(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    future_start = time.time() + 3600
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": future_start, "endTime": future_start + 3600, "mintPriceWei": "0"},
    )

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "scheduled"


def test_watcher_expires_when_window_already_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 7200, "endTime": time.time() - 3600, "mintPriceWei": "0"},
    )

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "expired"


def test_watcher_does_nothing_when_no_public_window_available(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    monkeypatch.setattr(firing.node_client, "get_public_drop_window", lambda contract: None)

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "pending_schedule"


def test_watcher_fails_arm_request_when_grant_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    store.revoke_session_grant(grant_id)
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "failed"


def test_watcher_fails_arm_request_when_grant_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant(expires_at=time.time() - 10)
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "failed"


def test_watcher_fires_and_records_success_when_window_open(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=2, max_price_wei="100", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []

    def fake_fire_mint(approval, contract, smart_account, quantity, value_cap_wei):
        fire_mint_calls.append((approval, contract, smart_account, quantity, value_cap_wei))
        return {
            "success": True, "userOpHash": "0xuserop", "txHash": "0xtxhash",
            "blockNumber": "123", "gasUsed": "50000",
        }

    monkeypatch.setattr(firing.node_client, "fire_mint", fake_fire_mint)

    firing.check_and_fire_armed_requests()

    arm = store.get_arm_request(arm_id)
    assert arm["status"] == "succeeded"
    assert len(fire_mint_calls) == 1
    _, contract, smart_account, quantity, value_cap_wei = fire_mint_calls[0]
    assert contract == CONTRACT
    assert smart_account == SMART_ACCOUNT
    assert quantity == 2
    assert value_cap_wei == "100"

    attempts = store.get_mint_attempts(arm_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "success"
    assert attempts[0]["tx_hash"] == "0xtxhash"


def test_watcher_decrypts_the_stored_session_key_before_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    real_secret = "this-is-the-real-serialized-approval"
    grant_id = _make_grant(encrypted_session_key=encrypt_secret(real_secret))
    store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    seen_approvals = []

    def fake_fire_mint(approval, contract, smart_account, quantity, value_cap_wei):
        seen_approvals.append(approval)
        return {"success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1"}

    monkeypatch.setattr(firing.node_client, "fire_mint", fake_fire_mint)

    firing.check_and_fire_armed_requests()

    assert seen_approvals == [real_secret]


def test_watcher_retries_on_failure_up_to_max_attempts_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: {
            "success": False, "userOpHash": "", "txHash": None,
            "blockNumber": None, "gasUsed": None, "error": "simulated failure",
        },
    )

    for _ in range(firing.MAX_FIRE_ATTEMPTS):
        firing.check_and_fire_armed_requests()
        arm = store.get_arm_request(arm_id)
        if arm["status"] == "failed":
            break
        # Between attempts the arm request cycles back through 'armed' —
        # simulate the next tick seeing it in that state again.
        assert arm["status"] == "armed"

    final = store.get_arm_request(arm_id)
    assert final["status"] == "failed"
    assert len(store.get_mint_attempts(arm_id)) == firing.MAX_FIRE_ATTEMPTS


def test_watcher_never_fires_twice_for_the_same_arm_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression test for the double-fire guard: even if _fire_one were
    # somehow invoked twice in a row for the same already-armed request
    # (e.g. overlapping scheduler ticks), fire_mint must only be called
    # once — the atomic try_claim_arm_request transition is what prevents
    # the second call.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []

    def fake_fire_mint(*a, **k):
        fire_mint_calls.append(1)
        return {"success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1"}

    monkeypatch.setattr(firing.node_client, "fire_mint", fake_fire_mint)

    store.update_arm_request_status(arm_id, "armed")
    arm = store.get_arm_request(arm_id)
    drop = store.get_tracked_drop(_drop_db_id(slug))
    grant = store.get_session_grant(grant_id)

    # Simulate two overlapping ticks both reaching the fire step for the
    # same already-'armed' request.
    firing._fire_one(arm, drop, grant)
    firing._fire_one(arm, drop, grant)

    assert len(fire_mint_calls) == 1


def test_watcher_records_error_attempt_and_fails_when_decryption_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    # encrypted_session_key is NOT actually encrypted with the configured
    # key — decrypt_secret will raise ValueError.
    grant_id = _make_grant(encrypted_session_key="not-real-ciphertext-at-all")
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    firing.check_and_fire_armed_requests()

    arm = store.get_arm_request(arm_id)
    assert arm["status"] == "failed"
    attempts = store.get_mint_attempts(arm_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "error"


def test_watcher_treats_node_helper_runtime_error_as_a_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    def raise_runtime_error(*a, **k):
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(firing.node_client, "fire_mint", raise_runtime_error)

    firing.check_and_fire_armed_requests()

    arm = store.get_arm_request(arm_id)
    assert arm["status"] == "armed"  # first of MAX_FIRE_ATTEMPTS, retried
    attempts = store.get_mint_attempts(arm_id)
    assert attempts[0]["status"] == "failed"
    assert "not running" in attempts[0]["error_message"]


def test_watcher_never_retries_an_ambiguous_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    # A "submitted but couldn't confirm the outcome" result (fireMint's
    # own waitForUserOperationReceipt timeout) must NEVER be retried,
    # regardless of MAX_FIRE_ATTEMPTS — a prior attempt may already have
    # landed on-chain, and resubmitting risks a genuine duplicate spend.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: {
            "success": False, "ambiguous": True, "userOpHash": "0xrealuserophash",
            "txHash": None, "blockNumber": None, "gasUsed": None,
            "error": "Submitted but receipt not confirmed within timeout",
        },
    )

    firing.check_and_fire_armed_requests()

    arm = store.get_arm_request(arm_id)
    assert arm["status"] == "failed"  # NOT "armed" — must not be retry-eligible
    attempts = store.get_mint_attempts(arm_id)
    assert len(attempts) == 1
    assert attempts[0]["user_op_hash"] == "0xrealuserophash"


def test_watcher_never_retries_after_a_node_helper_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # A RuntimeError containing "timed out" from node_client.fire_mint is
    # ALSO ambiguous — the Node helper's own internal receipt-wait may
    # still have been mid-flight — and must not be retried either.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    def raise_timeout(*a, **k):
        raise RuntimeError("Node wallet-helper timed out")

    monkeypatch.setattr(firing.node_client, "fire_mint", raise_timeout)

    firing.check_and_fire_armed_requests()

    arm = store.get_arm_request(arm_id)
    assert arm["status"] == "failed"  # ambiguous — must not be retried


def test_watcher_fails_when_drop_contract_no_longer_covered_by_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test: a re-scrape (agent.py's 15-min job) can overwrite
    # tracked_drops.contract_address after an arm request was already
    # created. The fire path must re-check the CURRENT contract against
    # the grant's allowed_targets, not just trust what was true at arm time.
    slug = _make_drop()
    grant_id = _make_grant()  # allowed_targets=[CONTRACT]
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug=slug, name="Some Drop",
        contract_address="0xadifferentcontract0000000000000000000000",
        mint_page_url="https://opensea.io/collection/" + slug,
        source="playwright", stage_data="{}",
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "0"},
    )
    fire_mint_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: fire_mint_calls.append(1) or {
            "success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1",
        },
    )

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "failed"
    assert fire_mint_calls == []  # never even attempted — caught before firing


def test_check_and_fire_armed_requests_continues_after_one_arm_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    broken_arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=999999, session_grant_id=grant_id,  # nonexistent drop
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    ok_arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract: {"startTime": time.time() + 3600, "endTime": time.time() + 7200, "mintPriceWei": "0"},
    )

    firing.check_and_fire_armed_requests()  # must not raise

    # The broken one (unknown drop) is a no-op and stays pending; the valid
    # one still gets processed normally.
    assert store.get_arm_request(broken_arm_id)["status"] == "pending_schedule"
    assert store.get_arm_request(ok_arm_id)["status"] == "scheduled"
