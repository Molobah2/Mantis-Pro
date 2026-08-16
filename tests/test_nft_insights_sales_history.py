import pytest

from nft_insights import sales_history

_RAW_SALE = {
    "event_type": "sale",
    "event_timestamp": 1786906895,
    "payment": {"quantity": "16478799999000000", "decimals": 18, "symbol": "ETH"},
    "nft": {
        "identifier": "2382",
        "name": "OMEN #2382",
        "image_url": "https://i2c.seadn.io/ethereum/abc/2382.jpeg",
    },
}


def _patch_get(monkeypatch: pytest.MonkeyPatch, responses):
    if not callable(responses):
        fixed = responses
        responses = lambda *a, **k: fixed
    monkeypatch.setattr(sales_history, "_get", responses)


# ── get_sales_in_range ────────────────────────────────────────────────

def test_get_sales_in_range_parses_price_and_token_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, {"asset_events": [_RAW_SALE], "next": None})
    result = sales_history.get_sales_in_range("godpull", 1000, 2000, max_pages=1)
    assert result.complete is True
    assert len(result.items) == 1
    sale = result.items[0]
    assert sale["token_id"] == 2382
    assert sale["price"] == pytest.approx(0.016478799999)
    assert sale["symbol"] == "ETH"
    assert sale["image_url"] == "https://i2c.seadn.io/ethereum/abc/2382.jpeg"


def test_get_sales_in_range_skips_unparseable_events(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {"payment": {"quantity": "not-an-int", "decimals": 18}}
    _patch_get(monkeypatch, {"asset_events": [broken, _RAW_SALE], "next": None})
    result = sales_history.get_sales_in_range("godpull", 1000, 2000, max_pages=1)
    assert len(result.items) == 1


def test_get_sales_in_range_untrusted_image_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {**_RAW_SALE, "nft": {**_RAW_SALE["nft"], "image_url": "https://evil.example.com/x.png"}}
    _patch_get(monkeypatch, {"asset_events": [raw], "next": None})
    result = sales_history.get_sales_in_range("godpull", 1000, 2000, max_pages=1)
    assert result.items[0]["image_url"] is None


def test_get_sales_in_range_follows_pagination_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"asset_events": [_RAW_SALE], "next": "cursor-2"},
        {"asset_events": [_RAW_SALE], "next": None},
    ]
    calls = []

    def fake_get(path, params=None):
        calls.append(params.get("next"))
        return pages[len(calls) - 1]

    monkeypatch.setattr(sales_history, "_get", fake_get)
    result = sales_history.get_sales_in_range("godpull", 1000, 2000, max_pages=5)
    assert len(result.items) == 2
    assert result.complete is True
    assert calls == [None, "cursor-2"]


def test_get_sales_in_range_passes_after_before_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_get(path, params=None):
        captured.update(params)
        return {"asset_events": [], "next": None}

    monkeypatch.setattr(sales_history, "_get", fake_get)
    sales_history.get_sales_in_range("godpull", 1000.7, 2000.2, max_pages=1)
    assert captured["after"] == 1000
    assert captured["before"] == 2000
    assert captured["event_type"] == "sale"


def test_get_sales_in_range_stops_at_max_pages_flagged_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(path, params=None):
        return {"asset_events": [_RAW_SALE], "next": "always-more"}

    monkeypatch.setattr(sales_history, "_get", fake_get)
    result = sales_history.get_sales_in_range("godpull", 1000, 2000, max_pages=3)
    assert len(result.items) == 3
    assert result.complete is False


def test_get_sales_in_range_returns_incomplete_on_first_page_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, None)
    result = sales_history.get_sales_in_range("godpull", 1000, 2000, max_pages=3)
    assert result.items == []
    assert result.complete is False


# ── summarize_sales ───────────────────────────────────────────────────

def _sale(price, symbol="ETH"):
    return {"token_id": 1, "price": price, "symbol": symbol, "timestamp": 0, "name": None, "image_url": None}


def test_summarize_sales_computes_count_volume_avg_median() -> None:
    summary = sales_history.summarize_sales([_sale(0.1), _sale(0.2), _sale(0.3)])
    assert summary["count"] == 3
    assert summary["volume_eth"] == pytest.approx(0.6)
    assert summary["avg_price_eth"] == pytest.approx(0.2)
    assert summary["median_price_eth"] == pytest.approx(0.2)
    assert summary["other_currency_count"] == 0


def test_summarize_sales_median_of_even_count_averages_middle_two() -> None:
    summary = sales_history.summarize_sales([_sale(0.1), _sale(0.2), _sale(0.3), _sale(0.4)])
    assert summary["median_price_eth"] == pytest.approx(0.25)


def test_summarize_sales_excludes_other_currencies_from_volume_but_counts_them() -> None:
    summary = sales_history.summarize_sales([_sale(0.1), _sale(500, symbol="USDC")])
    assert summary["count"] == 2
    assert summary["volume_eth"] == pytest.approx(0.1)
    assert summary["other_currency_count"] == 1


def test_summarize_sales_treats_weth_same_as_eth() -> None:
    summary = sales_history.summarize_sales([_sale(0.1, symbol="ETH"), _sale(0.2, symbol="WETH")])
    assert summary["volume_eth"] == pytest.approx(0.3)
    assert summary["other_currency_count"] == 0


def test_summarize_sales_empty_list() -> None:
    summary = sales_history.summarize_sales([])
    assert summary["count"] == 0
    assert summary["volume_eth"] == 0.0
    assert summary["avg_price_eth"] is None
    assert summary["median_price_eth"] is None


def test_summarize_sales_all_non_eth_yields_no_price_figures() -> None:
    summary = sales_history.summarize_sales([_sale(500, symbol="USDC")])
    assert summary["count"] == 1
    assert summary["volume_eth"] == 0.0
    assert summary["avg_price_eth"] is None
    assert summary["other_currency_count"] == 1
