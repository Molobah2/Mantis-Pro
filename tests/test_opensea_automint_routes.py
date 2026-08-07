import pytest
from flask import Flask
from flask.testing import FlaskClient

from opensea_automint import drops, store
from opensea_automint.routes import opensea_automint_bp


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


def test_api_drops_returns_expected_shape(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "get_tracked_drops", lambda: [_minting_now_row(), _not_minting_row()])

    resp = client.get("/api/opensea/drops")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "drops" in body
    assert len(body["drops"]) == 2

    minting = next(d for d in body["drops"] if d["collection_slug"] == "cheap-shot")
    assert minting["status"] == "minting_now"
    assert minting["is_publicly_mintable"] is True

    not_minting = next(d for d in body["drops"] if d["collection_slug"] == "god-pull")
    assert not_minting["status"] == "not_minting"
    assert not_minting["is_publicly_mintable"] is False


def test_api_drops_handles_malformed_stage_data_without_crashing(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_row = {
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
    monkeypatch.setattr(store, "get_tracked_drops", lambda: [bad_row])

    resp = client.get("/api/opensea/drops")

    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["drops"]) == 1
    assert body["drops"][0]["status"] == ""
    assert body["drops"][0]["is_publicly_mintable"] is False


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
    assert body["drops"][0]["status"] == ""
    assert body["drops"][0]["status_detail"] is None
    assert body["drops"][0]["is_publicly_mintable"] is False


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
