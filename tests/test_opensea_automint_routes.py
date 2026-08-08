import pytest
from flask import Flask
from flask.testing import FlaskClient

from opensea_automint import collection_details, drops, node_client, store
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


# ── GET /api/opensea/eth/smart-account-address ───────────────────────────

OWNER = "0x" + "a1" * 20
SMART_ACCOUNT = "0x" + "b2" * 20


def test_smart_account_address_rejects_missing_owner_with_400(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        node_client, "get_smart_account_address", lambda owner: calls.append(owner) or SMART_ACCOUNT
    )

    resp = client.get("/api/opensea/eth/smart-account-address")

    assert resp.status_code == 400
    assert calls == []


def test_smart_account_address_rejects_invalid_owner_with_400(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        node_client, "get_smart_account_address", lambda owner: calls.append(owner) or SMART_ACCOUNT
    )

    resp = client.get("/api/opensea/eth/smart-account-address?owner=not-an-address")

    assert resp.status_code == 400
    assert calls == []


def test_smart_account_address_returns_cached_row_without_calling_node(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        store, "get_smart_account",
        lambda owner: {"owner_address": owner, "smart_account_address": SMART_ACCOUNT, "created_at": 1000.0},
    )
    calls = []
    monkeypatch.setattr(
        node_client, "get_smart_account_address", lambda owner: calls.append(owner) or SMART_ACCOUNT
    )

    resp = client.get(f"/api/opensea/eth/smart-account-address?owner={OWNER}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"ownerAddress": OWNER, "smartAccountAddress": SMART_ACCOUNT}
    assert calls == []


def test_smart_account_address_derives_and_persists_on_cache_miss(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "get_smart_account", lambda owner: None)
    monkeypatch.setattr(node_client, "get_smart_account_address", lambda owner: SMART_ACCOUNT)
    upsert_calls = []
    monkeypatch.setattr(
        store, "upsert_smart_account",
        lambda owner, smart_account_address: upsert_calls.append((owner, smart_account_address)),
    )

    resp = client.get(f"/api/opensea/eth/smart-account-address?owner={OWNER}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"ownerAddress": OWNER, "smartAccountAddress": SMART_ACCOUNT}
    assert upsert_calls == [(OWNER, SMART_ACCOUNT)]


def test_smart_account_address_returns_502_on_node_client_runtime_error(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "get_smart_account", lambda owner: None)

    def fake_derive(owner: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(node_client, "get_smart_account_address", fake_derive)
    upsert_calls = []
    monkeypatch.setattr(
        store, "upsert_smart_account",
        lambda owner, smart_account_address: upsert_calls.append((owner, smart_account_address)),
    )

    resp = client.get(f"/api/opensea/eth/smart-account-address?owner={OWNER}")

    assert resp.status_code == 502
    body = resp.get_json()
    assert "boom" in body["error"]
    assert upsert_calls == []


def test_smart_account_address_rate_limits_cache_miss_requests(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "get_smart_account", lambda owner: None)
    monkeypatch.setattr(node_client, "get_smart_account_address", lambda owner: SMART_ACCOUNT)
    monkeypatch.setattr(store, "upsert_smart_account", lambda owner, smart_account_address: None)

    last_resp = None
    for _ in range(31):
        last_resp = client.get(f"/api/opensea/eth/smart-account-address?owner={OWNER}")

    assert last_resp.status_code == 429
