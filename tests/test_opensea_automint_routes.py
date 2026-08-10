import json
import time

import pytest
from flask import Flask
from flask.testing import FlaskClient

from opensea_automint import collection_details, drops, node_client, routes, store
from opensea_automint.routes import opensea_automint_bp
from portal_upvote import security as _sec


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Flask:
    """A minimal Flask app registering ONLY opensea_automint_bp — deliberately
    avoids importing agent.py, which has heavy unrelated startup side effects."""
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(opensea_automint_bp)
    return flask_app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


# ── GET /api/opensea/drops ───────────────────────────────────────────────

def _minting_now_row() -> dict:
    return {
        "id": 1,
        "collection_slug": "cheap-shot",
        "name": "Cheap Shot",
        "contract_address": "",
        "chain": "ethereum",
        "mint_page_url": "https://opensea.io/collection/cheap-shot",
        "discovered_at": 1000.0,
        "source": "playwright",
        "stage_data": '{"status": "minting_now", "status_detail": null}',
        "updated_at": 1000.0,
    }


def _not_minting_row() -> dict:
    return {
        "id": 2,
        "collection_slug": "god-pull",
        "name": "GOD PULL",
        "contract_address": "",
        "chain": "ethereum",
        "mint_page_url": "https://opensea.io/collection/godpull",
        "discovered_at": 1000.0,
        "source": "playwright",
        "stage_data": '{"status": "not_minting", "status_detail": null}',
        "updated_at": 1000.0,
    }


def _upcoming_row() -> dict:
    return {
        "id": 5,
        "collection_slug": "divergents",
        "name": "DIVERGENTS",
        "contract_address": "",
        "chain": "ethereum",
        "mint_page_url": "https://opensea.io/collection/divergents",
        "discovered_at": 1000.0,
        "source": "playwright",
        "stage_data": '{"status": "upcoming", "status_detail": "August 14 at 1:00 PM GMT"}',
        "updated_at": 1000.0,
    }


def test_api_drops_returns_expected_shape(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "get_tracked_drops", lambda: [_minting_now_row(), _upcoming_row()])

    resp = client.get("/api/opensea/drops")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "drops" in body
    assert len(body["drops"]) == 2

    minting = next(d for d in body["drops"] if d["collection_slug"] == "cheap-shot")
    assert minting["status"] == "minting_now"
    assert minting["is_publicly_mintable"] is True

    upcoming = next(d for d in body["drops"] if d["collection_slug"] == "divergents")
    assert upcoming["status"] == "upcoming"
    assert upcoming["is_publicly_mintable"] is False


def test_api_drops_excludes_not_minting_and_unknown_status(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown_status_row = {
        "id": 3,
        "collection_slug": "broken",
        "name": "Broken Drop",
        "contract_address": "",
        "chain": "ethereum",
        "mint_page_url": "https://opensea.io/collection/broken",
        "discovered_at": 1000.0,
        "source": "playwright",
        "stage_data": "not valid json{{{",
        "updated_at": 1000.0,
    }
    monkeypatch.setattr(
        store, "get_tracked_drops",
        lambda: [_minting_now_row(), _not_minting_row(), unknown_status_row],
    )

    resp = client.get("/api/opensea/drops")

    assert resp.status_code == 200
    body = resp.get_json()
    slugs = {d["collection_slug"] for d in body["drops"]}
    assert slugs == {"cheap-shot"}


def test_api_drops_handles_missing_stage_data_field(client, monkeypatch: pytest.MonkeyPatch) -> None:
    row_without_stage_data = {
        "id": 4,
        "collection_slug": "no-stage",
        "name": "No Stage",
        "contract_address": "",
        "chain": "ethereum",
        "mint_page_url": "https://opensea.io/collection/no-stage",
        "discovered_at": 1000.0,
        "source": "playwright",
        "stage_data": None,
        "updated_at": 1000.0,
    }
    monkeypatch.setattr(store, "get_tracked_drops", lambda: [row_without_stage_data])

    resp = client.get("/api/opensea/drops")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["drops"] == []


def test_api_drops_returns_empty_list_when_no_drops_tracked(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "get_tracked_drops", lambda: [])

    resp = client.get("/api/opensea/drops")

    assert resp.status_code == 200
    assert resp.get_json() == {"drops": []}


# ── POST /api/opensea/drops/refresh ──────────────────────────────────────

def test_refresh_without_admin_auth_is_rejected(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_SECRET", "supersecret")

    resp = client.post("/api/opensea/drops/refresh")

    assert resp.status_code == 401


def test_refresh_fails_closed_when_admin_secret_not_configured(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately does NOT inherit require_admin's open-if-unset fallback —
    this route triggers real outbound Playwright automation, so it must
    refuse rather than default open."""
    monkeypatch.delenv("ADMIN_SECRET", raising=False)

    resp = client.post("/api/opensea/drops/refresh", headers={"X-Admin-Key": "anything"})

    assert resp.status_code == 503


def test_refresh_with_valid_admin_auth_calls_get_drops_force_refresh_and_returns_shaped_rows(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMIN_SECRET", "supersecret")
    refresh_calls = []

    def fake_get_drops(force_refresh: bool = False) -> list:
        refresh_calls.append(force_refresh)
        return [_minting_now_row()]

    monkeypatch.setattr(drops, "get_drops", fake_get_drops)
    monkeypatch.setattr(store, "get_tracked_drops", lambda: [_minting_now_row()])

    resp = client.post(
        "/api/opensea/drops/refresh", headers={"X-Admin-Key": "supersecret"}
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert refresh_calls == [True]
    assert body["count"] == 1
    # Same to_display_dict shape as GET /api/opensea/drops — not the raw
    # get_drops() return value, which is shape-inconsistent depending on
    # whether the live scrape succeeded or fell back to stored rows.
    assert body["drops"][0]["collection_slug"] == "cheap-shot"
    assert body["drops"][0]["status"] == "minting_now"
    assert body["drops"][0]["is_publicly_mintable"] is True


# ── POST /api/opensea/drops/track ────────────────────────────────────────

def test_track_drop_returns_shaped_drop_on_success(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drops, "track_drop_by_slug", lambda slug: _minting_now_row())

    resp = client.post("/api/opensea/drops/track", json={"collectionSlug": "cheap-shot"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["drop"]["collection_slug"] == "cheap-shot"
    assert body["drop"]["status"] == "minting_now"


def test_track_drop_returns_404_when_no_contract_found(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drops, "track_drop_by_slug", lambda slug: None)

    resp = client.post("/api/opensea/drops/track", json={"collectionSlug": "cheap-shot"})

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_track_drop_rejects_invalid_slug(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(drops, "track_drop_by_slug", lambda slug: calls.append(1) or None)

    resp = client.post("/api/opensea/drops/track", json={"collectionSlug": "UPPERCASE"})

    assert resp.status_code == 400
    assert calls == []


def test_track_drop_rate_limits_after_threshold(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drops, "track_drop_by_slug", lambda slug: _minting_now_row())

    last_resp = None
    for _ in range(21):
        last_resp = client.post("/api/opensea/drops/track", json={"collectionSlug": "cheap-shot"})

    assert last_resp.status_code == 429


def test_track_drop_malformed_json_body_returns_400_not_500(client) -> None:
    resp = client.post(
        "/api/opensea/drops/track", data="not-json{{{", content_type="application/json",
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


# ── GET /opensea-automint ─────────────────────────────────────────────────

def test_dashboard_page_serves_html(client) -> None:
    resp = client.get("/opensea-automint")

    assert resp.status_code == 200
    assert "text/html" in resp.content_type


# ── GET /api/opensea/collection/<slug> ───────────────────────────────────

@pytest.fixture(autouse=True)
def reset_rate_limit_buckets():
    """portal_upvote.security.rate_limit tracks state in a module-level,
    in-memory dict shared across the whole test process — clear it before
    each test so earlier tests' request counts can't bleed into these
    rate-limit assertions."""
    _sec._buckets.clear()
    yield
    _sec._buckets.clear()


def test_api_collection_details_returns_expected_shape_for_valid_slug(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_details = {
        "description": "A cool collection.",
        "links": {"twitter": "https://x.com/foo", "website": "https://foo.xyz"},
    }
    monkeypatch.setattr(collection_details, "get_collection_details", lambda slug: fake_details)

    resp = client.get("/api/opensea/collection/cheap-shot")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == fake_details


def test_api_collection_details_rejects_invalid_slug_with_400(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        collection_details, "get_collection_details",
        lambda slug: calls.append(slug) or {"description": None, "links": {}},
    )

    resp = client.get("/api/opensea/collection/UPPERCASE")

    assert resp.status_code == 400
    # Route-layer validation must reject before ever calling into the module.
    assert calls == []


def test_api_collection_details_rate_limits_after_threshold(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        collection_details, "get_collection_details",
        lambda slug: {"description": None, "links": {}},
    )

    last_resp = None
    for _ in range(31):
        last_resp = client.get("/api/opensea/collection/cheap-shot")

    assert last_resp.status_code == 429


# ── POST /api/opensea/session-grant ──────────────────────────────────────

GRANT_OWNER = "0x" + "a1" * 20
GRANT_SESSION_ADDRESS = "0x" + "b2" * 20
GRANT_SESSION_PRIVATE_KEY = "0x" + "cd" * 32
GRANT_NFT_CONTRACT = "0x" + "d4" * 20
GRANT_SIGNATURE = "0x" + "ab" * 65


def _valid_grant_payload() -> dict:
    return {
        "ownerAddress": GRANT_OWNER,
        "sessionAddress": GRANT_SESSION_ADDRESS,
        "sessionPrivateKey": GRANT_SESSION_PRIVATE_KEY,
        "nftContract": GRANT_NFT_CONTRACT,
        "maxQuantity": 3,
        "valueCapWei": "50000000000000000",
        "expiresAt": time.time() + 3600,
        "signature": GRANT_SIGNATURE,
        "timestamp": time.time(),
    }


def _mock_verify_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: True)
    monkeypatch.setattr(node_client, "verify_session_key", lambda *a, **k: True)


def _mock_no_prior_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "get_active_session_grant", lambda owner: None)


def test_session_grant_valid_payload_returns_200_and_calls_insert_with_encrypted_key(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []

    def fake_insert(grant: store.SessionGrantInput) -> int:
        captured.append(grant)
        return 42

    _mock_verify_success(monkeypatch)
    _mock_no_prior_grant(monkeypatch)
    monkeypatch.setattr(store, "insert_session_grant", fake_insert)
    monkeypatch.setattr(routes, "encrypt_secret", lambda plaintext: f"encrypted:{plaintext}")

    payload = _valid_grant_payload()
    resp = client.post("/api/opensea/session-grant", json=payload)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"grantId": 42, "sessionAddress": GRANT_SESSION_ADDRESS.lower()}

    assert len(captured) == 1
    grant = captured[0]
    assert grant.owner_address == GRANT_OWNER.lower()
    assert grant.session_address == GRANT_SESSION_ADDRESS.lower()
    assert grant.encrypted_session_key != payload["sessionPrivateKey"]
    assert grant.encrypted_session_key == f"encrypted:{payload['sessionPrivateKey']}"

    parsed_targets = json.loads(grant.allowed_targets)
    assert parsed_targets == [GRANT_NFT_CONTRACT.lower()]

    parsed_config = json.loads(grant.permission_config)
    assert parsed_config == {"functionName": "mintPublic", "maxQuantity": 3}

    assert grant.value_cap_wei == "50000000000000000"


def test_session_grant_addresses_are_lowercased_consistently(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []
    _mock_verify_success(monkeypatch)
    _mock_no_prior_grant(monkeypatch)
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: captured.append(grant) or 1
    )
    monkeypatch.setattr(routes, "encrypt_secret", lambda plaintext: "encrypted")

    payload = _valid_grant_payload()
    payload["sessionAddress"] = "0x" + "B2" * 20  # mixed/upper-case on the wire

    resp = client.post("/api/opensea/session-grant", json=payload)

    assert resp.status_code == 200
    assert captured[0].session_address == ("0x" + "b2" * 20)


def test_session_grant_invalid_payload_returns_400_and_never_calls_insert(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    payload = _valid_grant_payload()
    payload["ownerAddress"] = "not-an-address"

    resp = client.post("/api/opensea/session-grant", json=payload)

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert calls == []


def test_session_grant_stale_signature_timestamp_returns_401_without_verifying(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    verify_calls = []
    monkeypatch.setattr(
        node_client, "verify_owner_signature", lambda *a, **k: verify_calls.append(1) or True,
    )
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    payload = _valid_grant_payload()
    payload["timestamp"] = time.time() - firing.SIGNATURE_MAX_AGE_SECONDS - 100

    resp = client.post("/api/opensea/session-grant", json=payload)

    assert resp.status_code == 401
    assert verify_calls == []
    assert calls == []


def test_session_grant_ownership_verification_failure_returns_401_and_never_calls_insert(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The signature is well-formed but doesn't actually recover to the
    claimed owner address (e.g. a spoofed claim) — must be rejected before
    ever touching encryption or the DB."""
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert resp.status_code == 401
    assert "error" in resp.get_json()
    assert calls == []


def test_session_grant_session_key_mismatch_returns_400_and_never_calls_insert(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner signature is valid, but the submitted sessionPrivateKey
    doesn't actually derive to the claimed sessionAddress — must be
    rejected before ever touching encryption or the DB."""
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: True)
    monkeypatch.setattr(node_client, "verify_session_key", lambda *a, **k: False)
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert calls == []


def test_session_grant_node_helper_failure_during_signature_verification_returns_502(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_runtime_error(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(node_client, "verify_owner_signature", raise_runtime_error)
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert resp.status_code == 502
    assert "error" in resp.get_json()
    assert calls == []


def test_session_grant_node_helper_failure_during_session_key_verification_returns_502(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_runtime_error(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: True)
    monkeypatch.setattr(node_client, "verify_session_key", raise_runtime_error)
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert resp.status_code == 502
    assert "error" in resp.get_json()
    assert calls == []


def test_session_grant_revokes_prior_active_grant_for_same_owner(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_verify_success(monkeypatch)
    monkeypatch.setattr(routes, "encrypt_secret", lambda plaintext: "encrypted")
    monkeypatch.setattr(store, "insert_session_grant", lambda grant: 99)

    monkeypatch.setattr(
        store, "get_active_session_grant", lambda owner: {"id": 7, "owner_address": owner}
    )
    revoked_ids = []
    monkeypatch.setattr(
        store, "revoke_session_grant", lambda grant_id: revoked_ids.append(grant_id)
    )

    resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert resp.status_code == 200
    assert revoked_ids == [7]


def test_session_grant_rate_limits_after_threshold(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_verify_success(monkeypatch)
    _mock_no_prior_grant(monkeypatch)
    monkeypatch.setattr(store, "insert_session_grant", lambda grant: 1)
    monkeypatch.setattr(routes, "encrypt_secret", lambda plaintext: "encrypted")

    last_resp = None
    for _ in range(11):
        last_resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert last_resp.status_code == 429


def test_session_grant_malformed_json_body_returns_400_not_500(client) -> None:
    resp = client.post(
        "/api/opensea/session-grant",
        data="not-valid-json{{{",
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_session_grant_missing_body_returns_400_not_500(client) -> None:
    resp = client.post("/api/opensea/session-grant")

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_session_grant_encrypt_secret_value_error_returns_500(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_encrypt(plaintext: str) -> str:
        raise ValueError("SESSION_KEY_ENCRYPTION_KEY not set or invalid (need 64-char hex)")

    _mock_verify_success(monkeypatch)
    _mock_no_prior_grant(monkeypatch)
    monkeypatch.setattr(routes, "encrypt_secret", fake_encrypt)
    calls = []
    monkeypatch.setattr(
        store, "insert_session_grant", lambda grant: calls.append(grant) or 1
    )

    resp = client.post("/api/opensea/session-grant", json=_valid_grant_payload())

    assert resp.status_code == 500
    assert "error" in resp.get_json()
    assert calls == []


# ── POST /api/opensea/session-grant/<id>/revoke ──────────────────────────

REVOKE_OWNER = "0x" + "e5" * 20
REVOKE_SIGNATURE = "0x" + "ab" * 65


def _valid_revoke_grant_payload() -> dict:
    return {"ownerAddress": REVOKE_OWNER, "signature": REVOKE_SIGNATURE, "timestamp": time.time()}


def _mock_revoke_signature_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: True)


def test_revoke_grant_valid_request_returns_200(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_revoke_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "revoke_grant", lambda grant_id, owner: {"revoked": True})

    resp = client.post("/api/opensea/session-grant/5/revoke", json=_valid_revoke_grant_payload())

    assert resp.status_code == 200
    assert resp.get_json() == {"revoked": True}


def test_revoke_grant_firing_error_returns_400(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_revoke_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "revoke_grant", lambda grant_id, owner: {"error": "Session grant not found"})

    resp = client.post("/api/opensea/session-grant/5/revoke", json=_valid_revoke_grant_payload())

    assert resp.status_code == 400


def test_revoke_grant_invalid_owner_address_returns_400_without_calling_revoke_grant(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    calls = []
    monkeypatch.setattr(
        firing, "revoke_grant", lambda grant_id, owner: calls.append(1) or {"revoked": True},
    )

    payload = _valid_revoke_grant_payload()
    payload["ownerAddress"] = "not-an-address"
    resp = client.post("/api/opensea/session-grant/5/revoke", json=payload)

    assert resp.status_code == 400
    assert calls == []


def test_revoke_grant_invalid_signature_returns_401_and_never_calls_revoke_grant(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    calls = []
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: False)
    monkeypatch.setattr(
        firing, "revoke_grant", lambda grant_id, owner: calls.append(1) or {"revoked": True},
    )

    resp = client.post("/api/opensea/session-grant/5/revoke", json=_valid_revoke_grant_payload())

    assert resp.status_code == 401
    assert calls == []


def test_revoke_grant_stale_signature_timestamp_returns_401(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    verify_calls = []
    monkeypatch.setattr(
        node_client, "verify_owner_signature", lambda *a, **k: verify_calls.append(1) or True,
    )
    monkeypatch.setattr(firing, "revoke_grant", lambda grant_id, owner: {"revoked": True})

    payload = _valid_revoke_grant_payload()
    payload["timestamp"] = time.time() - firing.SIGNATURE_MAX_AGE_SECONDS - 100
    resp = client.post("/api/opensea/session-grant/5/revoke", json=payload)

    assert resp.status_code == 401
    assert verify_calls == []


def test_revoke_grant_node_helper_failure_returns_502(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_runtime_error(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(node_client, "verify_owner_signature", raise_runtime_error)

    resp = client.post("/api/opensea/session-grant/5/revoke", json=_valid_revoke_grant_payload())

    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_revoke_grant_rate_limits_after_threshold(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_revoke_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "revoke_grant", lambda grant_id, owner: {"revoked": True})

    last_resp = None
    for _ in range(21):
        last_resp = client.post("/api/opensea/session-grant/5/revoke", json=_valid_revoke_grant_payload())

    assert last_resp.status_code == 429


def test_revoke_grant_malformed_json_body_returns_400_not_500(client) -> None:
    resp = client.post(
        "/api/opensea/session-grant/5/revoke", data="not-json{{{", content_type="application/json",
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


# ── POST /api/opensea/arm ────────────────────────────────────────────────

ARM_OWNER = "0x" + "e5" * 20
ARM_SIGNATURE = "0x" + "ab" * 65


def _valid_arm_payload() -> dict:
    return {
        "ownerAddress": ARM_OWNER,
        "collectionSlug": "cool-drop",
        "quantity": 2,
        "maxPriceWei": "50000000000000000",
        "signature": ARM_SIGNATURE,
        "timestamp": time.time(),
    }


def _mock_arm_signature_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: True)


def test_arm_valid_payload_returns_200_and_arm_id(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    _mock_arm_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "arm_drop", lambda *a, **k: {"armId": 5})

    resp = client.post("/api/opensea/arm", json=_valid_arm_payload())

    assert resp.status_code == 200
    assert resp.get_json() == {"armId": 5}


def test_arm_invalid_payload_returns_400_and_never_calls_arm_drop(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    calls = []
    monkeypatch.setattr(firing, "arm_drop", lambda *a, **k: calls.append(1) or {"armId": 1})

    payload = _valid_arm_payload()
    payload["ownerAddress"] = "not-an-address"

    resp = client.post("/api/opensea/arm", json=payload)

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert calls == []


def test_arm_invalid_signature_returns_401_and_never_calls_arm_drop(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    calls = []
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: False)
    monkeypatch.setattr(firing, "arm_drop", lambda *a, **k: calls.append(1) or {"armId": 1})

    resp = client.post("/api/opensea/arm", json=_valid_arm_payload())

    assert resp.status_code == 401
    assert "error" in resp.get_json()
    assert calls == []


def test_arm_stale_signature_timestamp_returns_401_without_verifying_signature(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    verify_calls = []
    monkeypatch.setattr(
        node_client, "verify_owner_signature", lambda *a, **k: verify_calls.append(1) or True,
    )
    monkeypatch.setattr(firing, "arm_drop", lambda *a, **k: {"armId": 1})

    payload = _valid_arm_payload()
    payload["timestamp"] = time.time() - firing.SIGNATURE_MAX_AGE_SECONDS - 100

    resp = client.post("/api/opensea/arm", json=payload)

    assert resp.status_code == 401
    assert verify_calls == []  # rejected on staleness before ever calling Node


def test_arm_node_helper_failure_during_signature_verification_returns_502(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_runtime_error(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(node_client, "verify_owner_signature", raise_runtime_error)

    resp = client.post("/api/opensea/arm", json=_valid_arm_payload())

    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_arm_validation_failure_from_firing_returns_400(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    _mock_arm_signature_valid(monkeypatch)
    monkeypatch.setattr(
        firing, "arm_drop", lambda *a, **k: {"error": "No active minting permission for this wallet — grant one first"},
    )

    resp = client.post("/api/opensea/arm", json=_valid_arm_payload())

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_arm_already_armed_returns_409_with_existing_arm_id(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    _mock_arm_signature_valid(monkeypatch)
    monkeypatch.setattr(
        firing, "arm_drop", lambda *a, **k: {"error": "This drop is already armed", "armId": 3},
    )

    resp = client.post("/api/opensea/arm", json=_valid_arm_payload())

    assert resp.status_code == 409
    assert resp.get_json()["armId"] == 3


def test_arm_rate_limits_after_threshold(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_arm_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "arm_drop", lambda *a, **k: {"armId": 1})

    last_resp = None
    for _ in range(11):
        last_resp = client.post("/api/opensea/arm", json=_valid_arm_payload())

    assert last_resp.status_code == 429


def test_arm_malformed_json_body_returns_400_not_500(client) -> None:
    resp = client.post("/api/opensea/arm", data="not-json{{{", content_type="application/json")

    assert resp.status_code == 400
    assert "error" in resp.get_json()


# ── GET /api/opensea/arm/for-drop ────────────────────────────────────────

def test_arm_for_drop_returns_arm_and_attempts_when_armed(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    monkeypatch.setattr(
        firing, "get_arm_status_for_drop",
        lambda owner, slug, stage_label="": {"arm": {"id": 1, "status": "armed"}, "attempts": []},
    )

    resp = client.get(f"/api/opensea/arm/for-drop?owner={ARM_OWNER}&collectionSlug=cool-drop")

    assert resp.status_code == 200
    assert resp.get_json()["arm"]["id"] == 1


def test_arm_for_drop_returns_null_arm_when_not_armed(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    monkeypatch.setattr(firing, "get_arm_status_for_drop", lambda owner, slug, stage_label="": None)

    resp = client.get(f"/api/opensea/arm/for-drop?owner={ARM_OWNER}&collectionSlug=cool-drop")

    assert resp.status_code == 200
    assert resp.get_json() == {"arm": None, "attempts": []}


def test_arm_for_drop_rejects_invalid_owner(client) -> None:
    resp = client.get("/api/opensea/arm/for-drop?owner=not-an-address&collectionSlug=cool-drop")

    assert resp.status_code == 400


def test_arm_for_drop_rate_limits_after_threshold(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    monkeypatch.setattr(firing, "get_arm_status_for_drop", lambda owner, slug, stage_label="": None)

    last_resp = None
    for _ in range(61):
        last_resp = client.get(f"/api/opensea/arm/for-drop?owner={ARM_OWNER}&collectionSlug=cool-drop")

    assert last_resp.status_code == 429


def test_arm_for_drop_rejects_invalid_slug(client) -> None:
    resp = client.get(f"/api/opensea/arm/for-drop?owner={ARM_OWNER}&collectionSlug=UPPERCASE")

    assert resp.status_code == 400


# ── GET /api/opensea/mint-history ────────────────────────────────────────

def test_mint_history_returns_shaped_list(client, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mints = [{"collection_slug": "cool-drop", "name": "Cool Drop", "quantity": 2}]
    calls = []
    monkeypatch.setattr(
        store, "get_mint_history",
        lambda owner_address=None, limit=200: calls.append((owner_address, limit)) or fake_mints,
    )

    resp = client.get("/api/opensea/mint-history")

    assert resp.status_code == 200
    assert resp.get_json() == {"mints": fake_mints}
    assert calls == [(None, 200)]


def test_mint_history_passes_through_owner_filter(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        store, "get_mint_history",
        lambda owner_address=None, limit=200: calls.append((owner_address, limit)) or [],
    )

    resp = client.get(f"/api/opensea/mint-history?owner={ARM_OWNER}")

    assert resp.status_code == 200
    assert calls == [(ARM_OWNER, 200)]


def test_mint_history_rejects_invalid_owner(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        store, "get_mint_history",
        lambda owner_address=None, limit=200: calls.append(1) or [],
    )

    resp = client.get("/api/opensea/mint-history?owner=not-an-address")

    assert resp.status_code == 400
    assert calls == []


def test_mint_history_rate_limits_after_threshold(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "get_mint_history", lambda owner_address=None, limit=200: [])

    last_resp = None
    for _ in range(61):
        last_resp = client.get("/api/opensea/mint-history")

    assert last_resp.status_code == 429


# ── POST /api/opensea/arm/<id>/cancel ────────────────────────────────────

def _valid_cancel_payload() -> dict:
    return {"ownerAddress": ARM_OWNER, "signature": ARM_SIGNATURE, "timestamp": time.time()}


def _mock_cancel_signature_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: True)


def test_cancel_arm_valid_request_returns_200(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_cancel_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "cancel_arm", lambda arm_id, owner: {"cancelled": True})

    resp = client.post("/api/opensea/arm/5/cancel", json=_valid_cancel_payload())

    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True}


def test_cancel_arm_firing_error_returns_400(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_cancel_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "cancel_arm", lambda arm_id, owner: {"error": "Arm request not found"})

    resp = client.post("/api/opensea/arm/5/cancel", json=_valid_cancel_payload())

    assert resp.status_code == 400


def test_cancel_arm_invalid_owner_address_returns_400_without_calling_cancel_arm(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    calls = []
    monkeypatch.setattr(
        firing, "cancel_arm", lambda arm_id, owner: calls.append(1) or {"cancelled": True},
    )

    payload = _valid_cancel_payload()
    payload["ownerAddress"] = "not-an-address"
    resp = client.post("/api/opensea/arm/5/cancel", json=payload)

    assert resp.status_code == 400
    assert calls == []


def test_cancel_arm_invalid_signature_returns_401_and_never_calls_cancel_arm(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    calls = []
    monkeypatch.setattr(node_client, "verify_owner_signature", lambda *a, **k: False)
    monkeypatch.setattr(
        firing, "cancel_arm", lambda arm_id, owner: calls.append(1) or {"cancelled": True},
    )

    resp = client.post("/api/opensea/arm/5/cancel", json=_valid_cancel_payload())

    assert resp.status_code == 401
    assert calls == []


def test_cancel_arm_stale_signature_timestamp_returns_401(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opensea_automint import firing

    verify_calls = []
    monkeypatch.setattr(
        node_client, "verify_owner_signature", lambda *a, **k: verify_calls.append(1) or True,
    )
    monkeypatch.setattr(firing, "cancel_arm", lambda arm_id, owner: {"cancelled": True})

    payload = _valid_cancel_payload()
    payload["timestamp"] = time.time() - firing.SIGNATURE_MAX_AGE_SECONDS - 100
    resp = client.post("/api/opensea/arm/5/cancel", json=payload)

    assert resp.status_code == 401
    assert verify_calls == []


def test_cancel_arm_rate_limits_after_threshold(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from opensea_automint import firing

    _mock_cancel_signature_valid(monkeypatch)
    monkeypatch.setattr(firing, "cancel_arm", lambda arm_id, owner: {"cancelled": True})

    last_resp = None
    for _ in range(21):
        last_resp = client.post("/api/opensea/arm/5/cancel", json=_valid_cancel_payload())

    assert last_resp.status_code == 429
