import json
import threading
import time

import pytest

from opensea_automint import firing, messages, store
from wallet_crypto import encrypt_secret

OWNER = "0xowner1234567890123456789012345678901234"
SESSION_ADDRESS = "0xsessionaddress123456789012345678901234"
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


def _wait_for_arm_status(arm_id: int, expected_statuses, timeout: float = 2.0) -> dict:
    """Firing now happens off a background thread (see firing._countdown_and_fire)
    for any arm request that's already at/near its go-live time — even the
    'already live' fast path fires via a spawned thread now, specifically so
    a slow fire_mint call can never block the tick from checking OTHER
    pending arm requests. That means check_and_fire_armed_requests() can
    return before a same-tick fire attempt has actually finished, so tests
    must poll for the real outcome rather than asserting immediately."""
    if isinstance(expected_statuses, str):
        expected_statuses = {expected_statuses}
    deadline = time.time() + timeout
    arm = store.get_arm_request(arm_id)
    while arm["status"] not in expected_statuses and time.time() < deadline:
        time.sleep(0.01)
        arm = store.get_arm_request(arm_id)
    return arm


def _wait_for_mint_attempt_count(arm_id: int, min_count: int, timeout: float = 2.0) -> list:
    """'armed' status is ambiguous on its own — it's set both right BEFORE
    a fire attempt starts and, separately, after a failed-but-retry-eligible
    attempt finishes (see firing._fire_one). Waiting for the status string
    alone can catch the former and read stale/absent mint_attempts. Use
    this instead whenever a test needs to know a real attempt was actually
    recorded, not just that the status briefly equals 'armed'."""
    deadline = time.time() + timeout
    attempts = store.get_mint_attempts(arm_id)
    while len(attempts) < min_count and time.time() < deadline:
        time.sleep(0.01)
        attempts = store.get_mint_attempts(arm_id)
    return attempts


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
        session_address=SESSION_ADDRESS,
        encrypted_session_key=encrypted_session_key,
        permission_config=json.dumps({"functionName": "mintPublic", "maxQuantity": max_quantity}),
        allowed_targets=json.dumps(targets),
        value_cap_wei=value_cap_wei,
        expires_at=expires_at if expires_at is not None else time.time() + 3600,
    ))


# ── message building / signature freshness ─────────────────────────────

def test_build_arm_message_is_deterministic() -> None:
    m1 = messages.build_arm_message("cool-drop", 2, "1000", 1700000000)
    m2 = messages.build_arm_message("cool-drop", 2, "1000", 1700000000)

    assert m1 == m2


def test_build_arm_message_changes_with_any_field() -> None:
    base = messages.build_arm_message("cool-drop", 2, "1000", 1700000000)

    assert messages.build_arm_message("other-drop", 2, "1000", 1700000000) != base
    assert messages.build_arm_message("cool-drop", 3, "1000", 1700000000) != base
    assert messages.build_arm_message("cool-drop", 2, "2000", 1700000000) != base
    assert messages.build_arm_message("cool-drop", 2, "1000", 1700000001) != base
    assert messages.build_arm_message("cool-drop", 2, "1000", 1700000000, "GTD") != base


def test_build_arm_message_default_stage_label_matches_explicit_empty_string() -> None:
    # A signature for the public stage must verify the same way whether
    # the caller omits stageLabel entirely or sends it as "" explicitly —
    # both mean the same thing (see arm_drop's docstring).
    assert (
        messages.build_arm_message("cool-drop", 2, "1000", 1700000000)
        == messages.build_arm_message("cool-drop", 2, "1000", 1700000000, "")
    )


def test_build_arm_message_different_stage_labels_produce_different_messages() -> None:
    gtd = messages.build_arm_message("cool-drop", 2, "1000", 1700000000, "GTD")
    fcfs = messages.build_arm_message("cool-drop", 2, "1000", 1700000000, "FCFS")

    assert gtd != fcfs


def test_build_cancel_message_is_deterministic() -> None:
    m1 = messages.build_cancel_message(5, 1700000000)
    m2 = messages.build_cancel_message(5, 1700000000)

    assert m1 == m2


def test_build_cancel_message_changes_with_any_field() -> None:
    base = messages.build_cancel_message(5, 1700000000)

    assert messages.build_cancel_message(6, 1700000000) != base
    assert messages.build_cancel_message(5, 1700000001) != base


def test_build_revoke_grant_message_is_deterministic() -> None:
    m1 = messages.build_revoke_grant_message(5, 1700000000)
    m2 = messages.build_revoke_grant_message(5, 1700000000)

    assert m1 == m2


def test_build_revoke_grant_message_changes_with_any_field() -> None:
    base = messages.build_revoke_grant_message(5, 1700000000)

    assert messages.build_revoke_grant_message(6, 1700000000) != base
    assert messages.build_revoke_grant_message(5, 1700000001) != base


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
    # max_price_wei is already a TOTAL for the whole arm request (matches
    # what ethClient.fireMint actually compares against — see
    # firing.arm_drop's own comment) — not multiplied by quantity again.
    slug = _make_drop()
    _make_grant(value_cap_wei="1000")

    result = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="1200")

    assert "error" in result
    assert "spend cap" in result["error"]


def test_arm_drop_max_price_wei_is_not_multiplied_by_quantity() -> None:
    # Regression test: max_price_wei=600 with quantity=2 must NOT be
    # treated as 1200 — it's already the total for this arm request, so it
    # fits comfortably under a 1000-wei cap.
    slug = _make_drop()
    _make_grant(value_cap_wei="1000")

    result = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="600")

    assert "armId" in result


def test_arm_drop_enforces_cumulative_quantity_across_prior_succeeded_arms() -> None:
    # Regression test for re-arming past a grant's authorized total: a
    # grant permits maxQuantity=3 total, not 3 per arm request. After one
    # arm request already consumed 2 of that 3 (whether pending or
    # succeeded), a second request for 2 more must be rejected even though
    # 2 alone is within the grant's raw maxQuantity.
    slug = _make_drop()
    grant_id = _make_grant(max_quantity=3)
    first = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="0")
    assert "armId" in first
    store.update_arm_request_status(first["armId"], "succeeded")

    result = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="0")

    assert "error" in result
    assert "remaining allowance" in result["error"]


def test_arm_drop_enforces_cumulative_spend_across_prior_succeeded_arms() -> None:
    slug = _make_drop()
    grant_id = _make_grant(value_cap_wei="1000")
    first = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="700")
    assert "armId" in first
    store.update_arm_request_status(first["armId"], "succeeded")

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="400")

    assert "error" in result
    assert "spend cap" in result["error"]


def test_arm_drop_allows_new_arm_after_cancelled_one_did_not_consume_allowance() -> None:
    # A cancelled/failed/expired arm never actually spent anything, so it
    # must NOT count against the grant's remaining allowance the way a
    # succeeded one does.
    slug = _make_drop()
    _make_grant(max_quantity=2)
    first = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="0")
    assert "armId" in first
    store.update_arm_request_status(first["armId"], "cancelled")

    result = firing.arm_drop(OWNER, slug, quantity=2, max_price_wei="0")

    assert "armId" in result


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

    def bypassed_for_the_race(owner, drop_id, stage_label=""):
        calls["n"] += 1
        if calls["n"] <= 2:
            return None
        return real_lookup(owner, drop_id, stage_label)

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
        owner_address=OWNER, session_address=SESSION_ADDRESS,
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


# ── arm_drop with stage_label (signed-presale stages) ──────────────────────

GTD_GO_LIVE = 1786100000.0


def _mock_gtd_schedule(monkeypatch: pytest.MonkeyPatch, slug: str) -> None:
    monkeypatch.setattr(
        firing.collection_details, "get_collection_details",
        lambda s: {"mint_schedule": [
            {"name": "GTD", "stage_type": "Allowlist", "starts_epoch": GTD_GO_LIVE},
            {"name": "Public", "stage_type": "Public", "starts_epoch": GTD_GO_LIVE + 3600},
        ]},
    )


def _mock_eligibility(monkeypatch: pytest.MonkeyPatch, stages: list) -> None:
    monkeypatch.setattr(firing.opensea_session, "is_configured", lambda: True)
    monkeypatch.setattr(
        firing.opensea_session, "fetch_drop_eligibility", lambda slug, owner: stages,
    )


def _eligible_gtd_stages() -> list:
    return [
        {"stageType": "SIGNED_PRESALE", "stageIndex": 1, "isEligible": True},
        {"stageType": "PUBLIC_SALE", "stageIndex": 0, "isEligible": True},
    ]


def test_arm_drop_with_stage_label_resolves_and_stores_stage_index_and_go_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, _eligible_gtd_stages())

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "armId" in result
    arm = store.get_arm_request(result["armId"])
    assert arm["stage_label"] == "GTD"
    assert arm["stage_index"] == 1
    assert arm["go_live_at"] == GTD_GO_LIVE


def test_arm_drop_stage_label_fails_when_opensea_session_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    monkeypatch.setattr(firing.opensea_session, "is_configured", lambda: False)

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "error" in result
    assert "session" in result["error"].lower()


def test_arm_drop_stage_label_fails_when_stage_name_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, _eligible_gtd_stages())

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="FCFS")

    assert "error" in result


def test_arm_drop_stage_label_fails_when_eligibility_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    monkeypatch.setattr(firing.opensea_session, "is_configured", lambda: True)
    monkeypatch.setattr(firing.opensea_session, "fetch_drop_eligibility", lambda slug, owner: None)

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "error" in result


def test_arm_drop_stage_label_fails_when_not_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, [
        {"stageType": "SIGNED_PRESALE", "stageIndex": 1, "isEligible": False},
        {"stageType": "PUBLIC_SALE", "stageIndex": 0, "isEligible": True},
    ])

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "error" in result
    assert "eligible" in result["error"].lower()


def test_arm_drop_stage_label_succeeds_when_eligibility_is_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test: DropEligibilityQuery's isEligible often comes back
    # null (not true/false) due to an OpenSea-side "Active address was not
    # provided" auth quirk on that specific query - verified live
    # 2026-08-13 for a wallet that WAS genuinely eligible per OpenSea's own
    # page. Only an explicit False should block arming; null must not.
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, [
        {"stageType": "SIGNED_PRESALE", "stageIndex": 1, "isEligible": None},
        {"stageType": "PUBLIC_SALE", "stageIndex": 0, "isEligible": None},
    ])

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "armId" in result


def test_arm_drop_stage_label_fails_when_position_is_actually_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Defends against a scraped-order/eligibility-order mismatch: if the
    # stage at the correlated position isn't actually SIGNED_PRESALE,
    # refuse rather than silently arming the wrong stage.
    slug = _make_drop()
    _make_grant()
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, [
        {"stageType": "PUBLIC_SALE", "stageIndex": 0, "isEligible": True},
        {"stageType": "SIGNED_PRESALE", "stageIndex": 1, "isEligible": True},
    ])

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "error" in result
    assert "not a signed-presale stage" in result["error"]


def test_arm_drop_two_different_stages_can_both_be_armed_simultaneously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    _make_grant(max_quantity=10, value_cap_wei="1000000000000000000")
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, _eligible_gtd_stages())

    gtd_result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")
    public_result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="")

    assert "armId" in gtd_result
    assert "armId" in public_result
    assert gtd_result["armId"] != public_result["armId"]


def test_arm_drop_same_stage_twice_returns_already_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    _make_grant(max_quantity=10, value_cap_wei="1000000000000000000")
    _mock_gtd_schedule(monkeypatch, slug)
    _mock_eligibility(monkeypatch, _eligible_gtd_stages())

    first = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")
    second = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0", stage_label="GTD")

    assert "armId" in first
    assert "error" in second
    assert second.get("armId") == first["armId"]


# ── revoke_grant ─────────────────────────────────────────────────────────

def test_revoke_grant_succeeds_for_owners_own_grant() -> None:
    grant_id = _make_grant()

    result = firing.revoke_grant(grant_id, OWNER)

    assert result == {"revoked": True}
    assert store.get_session_grant(grant_id)["revoked"] == 1


def test_revoke_grant_wrong_owner_returns_error_and_does_not_revoke() -> None:
    grant_id = _make_grant()

    result = firing.revoke_grant(grant_id, "0xSomeoneElse0000000000000000000000000000")

    assert "error" in result
    assert store.get_session_grant(grant_id)["revoked"] == 0


def test_revoke_grant_unknown_id_returns_error() -> None:
    result = firing.revoke_grant(999999, OWNER)

    assert "error" in result


def test_revoke_grant_already_revoked_returns_error() -> None:
    grant_id = _make_grant()
    first = firing.revoke_grant(grant_id, OWNER)
    assert first == {"revoked": True}

    second = firing.revoke_grant(grant_id, OWNER)

    assert "error" in second
    assert "already" in second["error"].lower()


def test_revoke_grant_lowercases_owner_address() -> None:
    grant_id = _make_grant(owner=OWNER.lower())

    result = firing.revoke_grant(grant_id, OWNER.upper().replace("0X", "0x"))

    assert result == {"revoked": True}


def test_revoked_grant_can_no_longer_be_armed() -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    firing.revoke_grant(grant_id, OWNER)

    result = firing.arm_drop(OWNER, slug, quantity=1, max_price_wei="0")

    assert "error" in result


# ── sweep_grant ──────────────────────────────────────────────────────────

def test_sweep_grant_succeeds_for_owners_own_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    grant_id = _make_grant()
    calls = []

    def fake_sweep(session_private_key, destination_address, chain):
        calls.append((session_private_key, destination_address, chain))
        return {"success": True, "txHash": "0x1", "amountSweptWei": "123"}

    monkeypatch.setattr(firing.node_client, "sweep_session_key", fake_sweep)

    result = firing.sweep_grant(grant_id, OWNER)

    assert result == {"success": True, "txHash": "0x1", "amountSweptWei": "123"}
    assert len(calls) == 1
    key, destination, chain = calls[0]
    assert key == "dummy-serialized-approval"
    assert destination == OWNER
    assert chain == "ethereum"  # CONTRACT was never tracked in this test


def test_sweep_grant_wrong_owner_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    grant_id = _make_grant()
    monkeypatch.setattr(firing.node_client, "sweep_session_key", lambda *a, **k: {"success": True})

    result = firing.sweep_grant(grant_id, "0xSomeoneElse0000000000000000000000000000")

    assert "error" in result


def test_sweep_grant_unknown_id_returns_error() -> None:
    result = firing.sweep_grant(999999, OWNER)

    assert "error" in result


def test_sweep_grant_works_even_if_already_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sweeping must still work after revoke — revoke has zero on-chain
    # effect, so a revoked grant's key can still hold real, recoverable ETH.
    grant_id = _make_grant()
    firing.revoke_grant(grant_id, OWNER)
    monkeypatch.setattr(
        firing.node_client, "sweep_session_key",
        lambda *a, **k: {"success": True, "txHash": "0x1", "amountSweptWei": "1"},
    )

    result = firing.sweep_grant(grant_id, OWNER)

    assert result.get("success") is True


def test_sweep_grant_resolves_chain_from_tracked_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="robinhood-drop", name="Robinhood Drop", contract_address=CONTRACT,
        mint_page_url="https://opensea.io/collection/robinhood-drop",
        source="manual", stage_data="{}", chain="robinhood",
    ))
    grant_id = _make_grant(targets=[CONTRACT])
    calls = []
    monkeypatch.setattr(
        firing.node_client, "sweep_session_key",
        lambda key, dest, chain: calls.append(chain) or {"success": True},
    )

    firing.sweep_grant(grant_id, OWNER)

    assert calls == ["robinhood"]


def test_sweep_grant_decrypt_failure_returns_error() -> None:
    grant_id = _make_grant(encrypted_session_key="not-valid-ciphertext")

    result = firing.sweep_grant(grant_id, OWNER)

    assert "error" in result


def test_sweep_grant_propagates_node_helper_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    grant_id = _make_grant()

    def raise_runtime_error(*a, **k):
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(firing.node_client, "sweep_session_key", raise_runtime_error)

    result = firing.sweep_grant(grant_id, OWNER)

    assert "error" in result
    assert "3456" in result["error"]


# ── transfer_minted_nft ──────────────────────────────────────────────────

def _make_successful_mint(
    contract_address: str = CONTRACT, owner: str = OWNER,
    tx_hash: str = "0xMintTx", chain: str = "ethereum", slug: str = "mint-drop",
) -> int:
    """Sets up a full drop -> grant -> arm -> successful mint_attempt chain
    and returns the mint_attempt id."""
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug=slug, name="Mint Drop", contract_address=contract_address,
        mint_page_url="https://opensea.io/collection/" + slug,
        source="playwright", stage_data="{}", chain=chain,
    ))
    drop_id = store.get_tracked_drop_by_slug(slug)["id"]
    grant_id = _make_grant(owner=owner, targets=[contract_address])
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=owner, drop_id=drop_id, session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash=tx_hash, user_op_hash=None,
        status="success", error_message=None, gas_used="21000", block_number=1,
        fired_at=time.time(), latency_ms=100,
    ))
    return store.get_mint_attempts(arm_id)[0]["id"]


def test_transfer_minted_nft_succeeds_for_owners_own_mint(monkeypatch: pytest.MonkeyPatch) -> None:
    mint_attempt_id = _make_successful_mint(tx_hash="0xMintTxA")
    calls = []

    def fake_transfer(session_private_key, contract, tx_hash, destination, chain):
        calls.append((session_private_key, contract, tx_hash, destination, chain))
        return {"success": True, "transfers": [{"tokenId": "441", "success": True, "txHash": "0x1"}]}

    monkeypatch.setattr(firing.node_client, "transfer_minted_nft", fake_transfer)

    result = firing.transfer_minted_nft(mint_attempt_id, OWNER)

    assert result["success"] is True
    assert len(calls) == 1
    key, contract, tx_hash, destination, chain = calls[0]
    assert key == "dummy-serialized-approval"
    assert contract == CONTRACT
    assert tx_hash == "0xMintTxA"
    assert destination == OWNER
    assert chain == "ethereum"


def test_transfer_minted_nft_wrong_owner_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mint_attempt_id = _make_successful_mint(tx_hash="0xMintTxB")
    monkeypatch.setattr(firing.node_client, "transfer_minted_nft", lambda *a, **k: {"success": True})

    result = firing.transfer_minted_nft(mint_attempt_id, "0xSomeoneElse0000000000000000000000000000")

    assert "error" in result


def test_transfer_minted_nft_unknown_id_returns_error() -> None:
    result = firing.transfer_minted_nft(999999, OWNER)

    assert "error" in result


def test_transfer_minted_nft_fails_when_mint_not_successful() -> None:
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="failed-mint-drop", name="Failed Mint Drop", contract_address=CONTRACT,
        mint_page_url="https://opensea.io/collection/failed-mint-drop",
        source="playwright", stage_data="{}",
    ))
    drop_id = store.get_tracked_drop_by_slug("failed-mint-drop")["id"]
    grant_id = _make_grant(targets=[CONTRACT])
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=drop_id, session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash=None, user_op_hash=None,
        status="failed", error_message="reverted", gas_used=None, block_number=None,
        fired_at=time.time(), latency_ms=None,
    ))
    mint_attempt_id = store.get_mint_attempts(arm_id)[0]["id"]

    result = firing.transfer_minted_nft(mint_attempt_id, OWNER)

    assert "error" in result


def test_transfer_minted_nft_resolves_chain_from_tracked_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    mint_attempt_id = _make_successful_mint(tx_hash="0xMintTxC", chain="robinhood")
    calls = []
    monkeypatch.setattr(
        firing.node_client, "transfer_minted_nft",
        lambda key, contract, tx_hash, destination, chain: calls.append(chain) or {"success": True},
    )

    firing.transfer_minted_nft(mint_attempt_id, OWNER)

    assert calls == ["robinhood"]


def test_transfer_minted_nft_decrypt_failure_returns_error() -> None:
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="decrypt-fail-drop", name="Decrypt Fail Drop", contract_address=CONTRACT,
        mint_page_url="https://opensea.io/collection/decrypt-fail-drop",
        source="playwright", stage_data="{}",
    ))
    drop_id = store.get_tracked_drop_by_slug("decrypt-fail-drop")["id"]
    grant_id = _make_grant(targets=[CONTRACT], encrypted_session_key="not-valid-ciphertext")
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=drop_id, session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    store.record_mint_attempt(store.MintAttemptInput(
        arm_request_id=arm_id, tx_hash="0xMintTxD", user_op_hash=None,
        status="success", error_message=None, gas_used="21000", block_number=1,
        fired_at=time.time(), latency_ms=100,
    ))
    mint_attempt_id = store.get_mint_attempts(arm_id)[0]["id"]

    result = firing.transfer_minted_nft(mint_attempt_id, OWNER)

    assert "error" in result


def test_transfer_minted_nft_propagates_node_helper_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mint_attempt_id = _make_successful_mint(tx_hash="0xMintTxE")

    def raise_runtime_error(*a, **k):
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(firing.node_client, "transfer_minted_nft", raise_runtime_error)

    result = firing.transfer_minted_nft(mint_attempt_id, OWNER)

    assert "error" in result
    assert "3456" in result["error"]


# ── _fire_signed_presale ─────────────────────────────────────────────────

_TX_DATA = {
    "to": "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5",
    "data": "0x4b61cd6f" + "00" * 32,
    "valueWei": "1000000000000000",
}


def test_fire_signed_presale_returns_failure_when_transaction_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: None,
    )
    arm = {"owner_address": OWNER, "quantity": 1, "max_price_wei": "1000000000000000"}
    drop = {"collection_slug": "some-drop", "contract_address": CONTRACT}

    result = firing._fire_signed_presale(arm, drop, "decrypted-key", "GTD")

    assert result["success"] is False
    assert "unavailable" in result["error"]


def test_fire_signed_presale_calls_fire_raw_transaction_with_fetched_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_calls = []
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: fetch_calls.append(
            (owner, contract, quantity, chain)
        ) or _TX_DATA,
    )
    calls = []

    def fake_fire_raw_transaction(approval, to, data, value_wei, chain):
        calls.append((approval, to, data, value_wei, chain))
        return {"success": True, "txHash": "0x1", "blockNumber": "1", "gasUsed": "1"}

    monkeypatch.setattr(firing.node_client, "fire_raw_transaction", fake_fire_raw_transaction)

    arm = {"owner_address": OWNER, "quantity": 2, "max_price_wei": "2000000000000000"}
    drop = {"collection_slug": "some-drop", "contract_address": CONTRACT}

    result = firing._fire_signed_presale(arm, drop, "decrypted-key", "GTD")

    assert result["success"] is True
    assert fetch_calls == [(OWNER, CONTRACT, 2, "ethereum")]
    assert len(calls) == 1
    approval, to, data, value_wei, chain = calls[0]
    assert approval == "decrypted-key"
    assert to == _TX_DATA["to"]
    assert data == _TX_DATA["data"]
    assert value_wei == _TX_DATA["valueWei"]
    assert chain == "ethereum"


def test_fire_signed_presale_refuses_to_fire_when_cost_exceeds_spend_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: _TX_DATA,  # valueWei = 1_000_000_000_000_000
    )
    calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_raw_transaction",
        lambda *a, **k: calls.append(1) or {"success": True},
    )

    arm = {"owner_address": OWNER, "quantity": 1, "max_price_wei": "1"}  # cap far below real cost
    drop = {"collection_slug": "some-drop", "contract_address": CONTRACT}

    result = firing._fire_signed_presale(arm, drop, "decrypted-key", "GTD")

    assert result["success"] is False
    assert "exceeds the granted spend cap" in result["error"]
    assert calls == []  # never even attempted — caught before firing


def test_fire_signed_presale_resolves_chain_from_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_calls = []
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: fetch_calls.append(chain) or _TX_DATA,
    )
    monkeypatch.setattr(
        firing.node_client, "fire_raw_transaction",
        lambda *a, **k: {"success": True},
    )

    arm = {"owner_address": OWNER, "quantity": 1, "max_price_wei": "1000000000000000"}
    drop = {"collection_slug": "some-drop", "contract_address": CONTRACT, "chain": "robinhood"}

    firing._fire_signed_presale(arm, drop, "decrypted-key", "GTD")

    assert fetch_calls == ["robinhood"]


def test_fire_signed_presale_treats_node_helper_timeout_as_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: _TX_DATA,
    )

    def raise_timeout(*a, **k):
        raise RuntimeError("Node wallet-helper timed out")

    monkeypatch.setattr(firing.node_client, "fire_raw_transaction", raise_timeout)

    arm = {"owner_address": OWNER, "quantity": 1, "max_price_wei": "1000000000000000"}
    drop = {"collection_slug": "some-drop", "contract_address": CONTRACT}

    result = firing._fire_signed_presale(arm, drop, "decrypted-key", "GTD")

    assert result["success"] is False
    assert result["ambiguous"] is True


# ── end-to-end: signed-presale stage firing via the countdown thread ──────

def test_watcher_fires_signed_presale_stage_when_go_live_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    go_live = time.time() + 0.3
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000000000000000", go_live_at=go_live,
        stage_label="GTD", stage_index=1,
    ))
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: _TX_DATA,
    )
    fire_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_raw_transaction",
        lambda *a, **k: fire_calls.append(1) or {
            "success": True, "txHash": "0x1", "blockNumber": "1", "gasUsed": "1",
        },
    )

    firing.check_and_fire_armed_requests()

    arm = _wait_for_arm_status(arm_id, "succeeded", timeout=3.0)
    assert arm["status"] == "succeeded"
    assert len(fire_calls) == 1


def test_watcher_never_calls_public_fire_mint_for_a_presale_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    go_live = time.time() - 100  # already live
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=go_live,
        stage_label="GTD", stage_index=1,
    ))
    monkeypatch.setattr(
        firing.opensea_session, "fetch_signed_mint_authorization",
        lambda slug, owner, idx: _AUTH,
    )
    monkeypatch.setattr(
        firing.node_client, "fire_signed_mint",
        lambda *a, **k: {"success": True, "txHash": "0x1", "blockNumber": "1", "gasUsed": "1"},
    )
    public_fire_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: public_fire_calls.append(1) or {"success": True, "txHash": "0x2"},
    )

    firing.check_and_fire_armed_requests()
    _wait_for_arm_status(arm_id, "succeeded", timeout=3.0)

    assert public_fire_calls == []  # never touched the mintPublic path
    # get_public_drop_window is on-chain-only and irrelevant to a presale
    # stage's timing — confirm it's never even consulted.


def test_watcher_expires_a_presale_arm_after_the_fire_window_with_no_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    go_live = time.time() - firing.SIGNED_PRESALE_FIRE_WINDOW_SECONDS - 10
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=go_live,
        stage_label="GTD", stage_index=1,
    ))

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "expired"


def test_watcher_fails_presale_arm_with_no_go_live_at_resolved() -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
        stage_label="GTD", stage_index=1,
    ))

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "failed"


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
        lambda contract, chain=None: {"startTime": future_start, "endTime": future_start + 3600, "mintPriceWei": "0"},
    )

    firing.check_and_fire_armed_requests()

    assert store.get_arm_request(arm_id)["status"] == "scheduled"


# ── countdown-thread precision firing ───────────────────────────────────

def test_watcher_fires_via_countdown_thread_within_critical_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Inside CRITICAL_WINDOW_SECONDS but not live yet — must spawn a
    # countdown thread (not wait for the next 5s scheduler tick) and fire
    # shortly after the real go-live moment, entirely off the tick cadence.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    start_time = time.time() + 0.2  # well inside the 20s critical window
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": start_time, "endTime": start_time + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: fire_mint_calls.append(1) or {
            "success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1",
        },
    )

    firing.check_and_fire_armed_requests()

    # Immediately after the tick, still waiting — proves firing did NOT
    # happen synchronously inside this call (which would block the tick).
    assert store.get_arm_request(arm_id)["status"] in ("scheduled", "armed")

    arm = _wait_for_arm_status(arm_id, "succeeded", timeout=3.0)
    assert arm["status"] == "succeeded"
    assert len(fire_mint_calls) == 1


def test_countdown_thread_refuses_to_fire_if_drop_contract_changes_during_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test: the countdown thread is spawned with a drop/grant
    # SNAPSHOT and can then sleep for a while before actually firing. If the
    # drop's contract address changes underneath it during that wait (e.g. a
    # re-scrape), it must re-validate against the CURRENT contract right
    # before firing, not trust the stale snapshot it was spawned with.
    slug = _make_drop()
    grant_id = _make_grant()  # allowed_targets=[CONTRACT]
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    start_time = time.time() + 0.3  # inside the critical window
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": start_time, "endTime": start_time + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: fire_mint_calls.append(1) or {
            "success": True, "txHash": "0x2", "blockNumber": "1", "gasUsed": "1",
        },
    )

    firing.check_and_fire_armed_requests()  # spawns the countdown thread

    # Mutate the drop's contract address WHILE the countdown thread is still
    # waiting for start_time to pass.
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug=slug, name="Some Drop",
        contract_address="0xadifferentcontract0000000000000000000000",
        mint_page_url="https://opensea.io/collection/" + slug,
        source="playwright", stage_data="{}",
    ))

    arm = _wait_for_arm_status(arm_id, "failed", timeout=3.0)
    assert arm["status"] == "failed"
    assert fire_mint_calls == []  # never fired against the stale contract


def test_countdown_thread_refuses_to_fire_if_grant_is_revoked_during_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same TOCTOU concern as the contract-change test above, but for grant
    # revocation — the owner's one "kill switch" for a compromised session
    # key must actually stop an in-flight countdown, not just future ticks.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    start_time = time.time() + 0.3
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": start_time, "endTime": start_time + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: fire_mint_calls.append(1) or {
            "success": True, "txHash": "0x2", "blockNumber": "1", "gasUsed": "1",
        },
    )

    firing.check_and_fire_armed_requests()  # spawns the countdown thread

    store.revoke_session_grant(grant_id)  # the owner's kill switch, mid-wait

    arm = _wait_for_arm_status(arm_id, "failed", timeout=3.0)
    assert arm["status"] == "failed"
    assert fire_mint_calls == []


def test_watcher_does_not_spawn_duplicate_countdown_on_repeated_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    start_time = time.time() + 0.3
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": start_time, "endTime": start_time + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: fire_mint_calls.append(1) or {
            "success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1",
        },
    )

    # Simulate several scheduler ticks landing while the countdown thread
    # is still waiting — must not spawn a second thread / fire twice.
    firing.check_and_fire_armed_requests()
    firing.check_and_fire_armed_requests()
    firing.check_and_fire_armed_requests()

    arm = _wait_for_arm_status(arm_id, "succeeded", timeout=3.0)
    assert arm["status"] == "succeeded"
    assert len(fire_mint_calls) == 1


def test_active_countdowns_cleaned_up_after_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    start_time = time.time() + 0.1
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": start_time, "endTime": start_time + 3600, "mintPriceWei": "50"},
    )
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: {"success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1"},
    )

    firing.check_and_fire_armed_requests()
    _wait_for_arm_status(arm_id, "succeeded", timeout=3.0)

    # A brief grace period for the countdown thread's finally block to run
    # (it executes right after the status update, but is one more Python
    # statement away from that point).
    deadline = time.time() + 1.0
    while arm_id in firing._active_countdowns and time.time() < deadline:
        time.sleep(0.01)

    assert arm_id not in firing._active_countdowns


def test_watcher_fires_immediately_when_already_past_go_live_without_blocking_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The "already live" fast path must ALSO fire via a background thread
    # (not synchronously inside the tick) — otherwise a slow fire_mint call
    # for one arm request would delay every OTHER pending arm request in
    # the same tick.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    fire_started = threading.Event()
    fire_may_finish = threading.Event()

    def slow_fire_mint(*a, **k):
        fire_started.set()
        fire_may_finish.wait(timeout=3.0)
        return {"success": True, "userOpHash": "0x1", "txHash": "0x2", "blockNumber": "1", "gasUsed": "1"}

    monkeypatch.setattr(firing.node_client, "fire_mint", slow_fire_mint)

    tick_start = time.time()
    firing.check_and_fire_armed_requests()
    tick_duration = time.time() - tick_start

    # The tick itself must return quickly even though fire_mint is
    # deliberately blocked — proves it was handed off to a thread.
    assert tick_duration < 1.0
    assert fire_started.wait(timeout=1.0)

    fire_may_finish.set()
    arm = _wait_for_arm_status(arm_id, "succeeded", timeout=3.0)
    assert arm["status"] == "succeeded"


def test_watcher_expires_when_window_already_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="0", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": time.time() - 7200, "endTime": time.time() - 3600, "mintPriceWei": "0"},
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
    monkeypatch.setattr(firing.node_client, "get_public_drop_window", lambda contract, chain=None: None)

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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    fire_mint_calls = []

    def fake_fire_mint(approval, contract, quantity, value_cap_wei, chain=None):
        fire_mint_calls.append((approval, contract, quantity, value_cap_wei))
        return {
            "success": True, "txHash": "0xtxhash",
            "blockNumber": "123", "gasUsed": "50000",
        }

    monkeypatch.setattr(firing.node_client, "fire_mint", fake_fire_mint)

    firing.check_and_fire_armed_requests()

    arm = _wait_for_arm_status(arm_id, "succeeded")
    assert arm["status"] == "succeeded"
    assert len(fire_mint_calls) == 1
    _, contract, quantity, value_cap_wei = fire_mint_calls[0]
    assert contract == CONTRACT
    assert quantity == 2
    assert value_cap_wei == "100"

    attempts = store.get_mint_attempts(arm_id)
    assert len(attempts) == 1
    assert attempts[0]["status"] == "success"
    # go-live was ~100s in the past — latency_ms reflects that real gap
    # (plus the GO_LIVE_SAFETY_MARGIN_SECONDS=1s the countdown always waits
    # out), not a huge or missing value.
    assert attempts[0]["fired_at"] is not None
    assert 100_000 <= attempts[0]["latency_ms"] <= 105_000


def test_fire_one_computes_latency_ms_relative_to_go_live_at() -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    store.update_arm_request_status(arm_id, "armed")  # _fire_one only fires from 'armed'
    arm = store.get_arm_request(arm_id)
    drop = store.get_tracked_drop(_drop_db_id(slug))
    grant = store.get_session_grant(grant_id)
    go_live_at = time.time() - 2.0  # fired ~2s after go-live

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            firing.node_client, "fire_mint",
            lambda *a, **k: {"success": True, "txHash": "0x1", "blockNumber": "1", "gasUsed": "1"},
        )
        firing._fire_one(arm, drop, grant, go_live_at=go_live_at)

    attempts = store.get_mint_attempts(arm_id)
    assert attempts[0]["fired_at"] is not None
    assert 1900 <= attempts[0]["latency_ms"] <= 2500  # ~2000ms, generous test-runtime margin


def test_fire_one_records_no_latency_when_go_live_at_unknown() -> None:
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    store.update_arm_request_status(arm_id, "armed")  # _fire_one only fires from 'armed'
    arm = store.get_arm_request(arm_id)
    drop = store.get_tracked_drop(_drop_db_id(slug))
    grant = store.get_session_grant(grant_id)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(
            firing.node_client, "fire_mint",
            lambda *a, **k: {"success": True, "txHash": "0x1", "blockNumber": "1", "gasUsed": "1"},
        )
        firing._fire_one(arm, drop, grant)  # no go_live_at passed

    attempts = store.get_mint_attempts(arm_id)
    assert attempts[0]["fired_at"] is not None  # still recorded
    assert attempts[0]["latency_ms"] is None  # nothing to compute latency against


def test_watcher_decrypts_the_stored_session_key_before_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = _make_drop()
    real_secret = "this-is-the-real-serialized-approval"
    grant_id = _make_grant(encrypted_session_key=encrypt_secret(real_secret))
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=None,
    ))
    monkeypatch.setattr(
        firing.node_client, "get_public_drop_window",
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    seen_approvals = []

    def fake_fire_mint(approval, contract, quantity, value_cap_wei, chain=None):
        seen_approvals.append(approval)
        return {"success": True, "txHash": "0x2", "blockNumber": "1", "gasUsed": "1"}

    monkeypatch.setattr(firing.node_client, "fire_mint", fake_fire_mint)

    firing.check_and_fire_armed_requests()
    _wait_for_arm_status(arm_id, "succeeded")

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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: {
            "success": False, "userOpHash": "", "txHash": None,
            "blockNumber": None, "gasUsed": None, "error": "simulated failure",
        },
    )

    # Keep ticking until the arm request reaches its terminal 'failed'
    # state. Each tick either spawns a new fire-attempt thread or (if the
    # previous attempt's thread hasn't finished cleaning up its
    # _active_countdowns entry yet) is a harmless no-op — so this doesn't
    # assume an exact 1:1 correspondence between tick calls and attempts,
    # only that repeated ticking eventually converges on the right outcome.
    deadline = time.time() + 5.0
    arm = store.get_arm_request(arm_id)
    while arm["status"] != "failed" and time.time() < deadline:
        firing.check_and_fire_armed_requests()
        time.sleep(0.05)
        arm = store.get_arm_request(arm_id)

    assert arm["status"] == "failed"
    assert len(store.get_mint_attempts(arm_id)) == firing.MAX_FIRE_ATTEMPTS


def test_watcher_retries_signed_presale_up_to_higher_max_attempts_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test: verified live 2026-08-13 that a real allowlist mint
    # (Redflags) exhausted the public path's MAX_FIRE_ATTEMPTS (3) within
    # ~20-30 seconds, all reverting with SeaDrop's own NotActive error -
    # not a real ineligibility/sellout, just this project's free RPC's
    # already-documented inconsistent view of the chain tip. Signed-presale
    # stages get a much higher budget (SIGNED_PRESALE_MAX_FIRE_ATTEMPTS) so
    # a short allowlist window has a real chance to outlast that.
    slug = _make_drop()
    grant_id = _make_grant()
    arm_id = store.create_arm_request(store.ArmRequestInput(
        owner_address=OWNER, drop_id=_drop_db_id(slug), session_grant_id=grant_id,
        quantity=1, max_price_wei="1000", go_live_at=time.time() - 100,
        stage_label="GTD", stage_index=1,
    ))
    monkeypatch.setattr(
        firing.opensea_session, "fetch_mint_transaction_data",
        lambda owner, contract, quantity, chain: _TX_DATA,
    )
    monkeypatch.setattr(
        firing.node_client, "fire_raw_transaction",
        lambda *a, **k: {
            "success": False, "txHash": None, "blockNumber": None,
            "gasUsed": None, "error": "simulated NotActive revert",
        },
    )

    deadline = time.time() + 5.0
    arm = store.get_arm_request(arm_id)
    while arm["status"] != "failed" and time.time() < deadline:
        firing.check_and_fire_armed_requests()
        time.sleep(0.05)
        arm = store.get_arm_request(arm_id)

    assert arm["status"] == "failed"
    attempt_count = len(store.get_mint_attempts(arm_id))
    assert attempt_count == firing.SIGNED_PRESALE_MAX_FIRE_ATTEMPTS
    assert attempt_count > firing.MAX_FIRE_ATTEMPTS


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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    firing.check_and_fire_armed_requests()

    arm = _wait_for_arm_status(arm_id, "failed")
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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    def raise_runtime_error(*a, **k):
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(firing.node_client, "fire_mint", raise_runtime_error)

    firing.check_and_fire_armed_requests()

    attempts = _wait_for_mint_attempt_count(arm_id, 1)
    assert attempts[0]["status"] == "failed"
    assert "not running" in attempts[0]["error_message"]

    # The attempt is recorded slightly before the final status transition
    # (see firing._fire_one: record_mint_attempt, then
    # update_arm_request_status) — 'fired' is always transient here (it's
    # the claim status set right before the attempt), so waiting past it
    # is safe and unambiguous, unlike waiting for 'armed' outright.
    arm = _wait_for_arm_status(arm_id, {"armed", "failed"})
    assert arm["status"] == "armed"  # first of MAX_FIRE_ATTEMPTS, retried


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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )
    monkeypatch.setattr(
        firing.node_client, "fire_mint",
        lambda *a, **k: {
            "success": False, "ambiguous": True,
            "txHash": "0xrealtxhash", "blockNumber": None, "gasUsed": None,
            "error": "Submitted but receipt not confirmed within timeout",
        },
    )

    firing.check_and_fire_armed_requests()

    arm = _wait_for_arm_status(arm_id, "failed")
    assert arm["status"] == "failed"  # NOT "armed" — must not be retry-eligible
    attempts = store.get_mint_attempts(arm_id)
    assert len(attempts) == 1
    assert attempts[0]["tx_hash"] == "0xrealtxhash"


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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "50"},
    )

    def raise_timeout(*a, **k):
        raise RuntimeError("Node wallet-helper timed out")

    monkeypatch.setattr(firing.node_client, "fire_mint", raise_timeout)

    firing.check_and_fire_armed_requests()

    arm = _wait_for_arm_status(arm_id, "failed")
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
        lambda contract, chain=None: {"startTime": time.time() - 100, "endTime": time.time() + 3600, "mintPriceWei": "0"},
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
        lambda contract, chain=None: {"startTime": time.time() + 3600, "endTime": time.time() + 7200, "mintPriceWei": "0"},
    )

    firing.check_and_fire_armed_requests()  # must not raise

    # The broken one (unknown drop) is a no-op and stays pending; the valid
    # one still gets processed normally.
    assert store.get_arm_request(broken_arm_id)["status"] == "pending_schedule"
    assert store.get_arm_request(ok_arm_id)["status"] == "scheduled"
