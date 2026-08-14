import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image

from nft_insights import routes, scan
from nft_insights.routes import nft_insights_bp
from portal_upvote import security as _sec


@pytest.fixture
def app() -> Flask:
    """A minimal Flask app registering ONLY nft_insights_bp — avoids
    importing agent.py, which has heavy unrelated startup side effects."""
    flask_app = Flask(__name__)
    flask_app.register_blueprint(nft_insights_bp)
    return flask_app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_state():
    """rate_limit and scan's cache are both module-level in-memory state
    shared across the test process — clear both before/after each test."""
    _sec._buckets.clear()
    scan._cache.clear()
    yield
    _sec._buckets.clear()
    scan._cache.clear()


def _stub_scan(monkeypatch: pytest.MonkeyPatch, *, with_nft_images: bool = True) -> None:
    nft = {"token_id": 1, "name": "Boonie #1", "attrs": {"Eyes": "Laser"}}
    if with_nft_images:
        nft["image_url"] = "https://i2c.seadn.io/eth/abc/1.png"
    scan_data = {
        "collection": {"slug": "boonies", "name": "Boonies", "image_url": None, "total_supply": 100},
        "stats": {"floor_price": 0.1, "floor_price_symbol": "ETH"},
        "listings": [{"token_id": 1, "price": 0.1, "currency": "ETH"}],
        "nfts": [nft],
    }

    def fake_fetch(slug):
        from nft_insights import insights as insights_mod
        return {"scan": scan_data, "insights": insights_mod.generate(scan_data), "fetched_at": 0.0}

    monkeypatch.setattr(scan, "_fetch", fake_fetch)


# ── GET /api/insights/scan ───────────────────────────────────────────────

def test_api_scan_rejects_invalid_slug(client: FlaskClient) -> None:
    resp = client.get("/api/insights/scan?slug=NOT_VALID!!")
    assert resp.status_code == 400


def test_api_scan_returns_ranked_insights(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    resp = client.get("/api/insights/scan?slug=boonies")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["collection"]["name"] == "Boonies"
    assert len(body["insights"]) >= 1
    assert body["insights"][0]["is_best"] is True
    best = body["insights"][0]
    assert "headline" in best and "captions" in best


def test_api_scan_resolves_nft_image_urls(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    resp = client.get("/api/insights/scan?slug=boonies")
    body = resp.get_json()
    rarest_trait = next((i for i in body["insights"] if i["type"] == "rarest_listed_trait"), None)
    if rarest_trait:
        assert rarest_trait["nfts"][0]["image_url"] == "https://i2c.seadn.io/eth/abc/1.png"


def test_api_scan_rate_limits_after_threshold(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    last_resp = None
    for _ in range(31):
        last_resp = client.get("/api/insights/scan?slug=boonies")
    assert last_resp.status_code == 429


# ── GET /api/insights/card ───────────────────────────────────────────────

def test_api_card_rejects_missing_params(client: FlaskClient) -> None:
    assert client.get("/api/insights/card?slug=boonies").status_code == 400
    assert client.get("/api/insights/card?insight_id=x").status_code == 400


def test_api_card_returns_png_for_valid_insight(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_scan(monkeypatch)
    monkeypatch.setattr(routes.card_renderer, "fetch_nft_image", lambda url: None)
    resp = client.get("/api/insights/card?slug=boonies&insight_id=market_snapshot&format=1:1")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"


def test_api_card_404s_for_unknown_insight_id(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    resp = client.get("/api/insights/card?slug=boonies&insight_id=does-not-exist")
    assert resp.status_code == 404


def test_api_card_fetches_real_grid_images(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    fetched_urls = []

    def fake_fetch(url):
        fetched_urls.append(url)
        return Image.new("RGBA", (10, 10), (0, 0, 0, 255)) if url else None

    monkeypatch.setattr(routes.card_renderer, "fetch_nft_image", fake_fetch)
    resp = client.get("/api/insights/card?slug=boonies&insight_id=rarest_listed_trait")
    if resp.status_code == 200:
        assert "https://i2c.seadn.io/eth/abc/1.png" in fetched_urls


# ── GET /api/insights/caption ─────────────────────────────────────────────

def test_api_caption_returns_headline_and_three_captions(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_scan(monkeypatch)
    resp = client.get("/api/insights/caption?slug=boonies&insight_id=market_snapshot")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "headline" in body
    assert len(body["captions"]) == 3


def test_api_caption_404s_for_unknown_insight_id(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    resp = client.get("/api/insights/caption?slug=boonies&insight_id=nope")
    assert resp.status_code == 404


def test_api_caption_rate_limits_after_threshold(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_scan(monkeypatch)
    last_resp = None
    for _ in range(31):
        last_resp = client.get("/api/insights/caption?slug=boonies&insight_id=market_snapshot")
    assert last_resp.status_code == 429
