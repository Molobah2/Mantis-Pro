import pytest

from nft_insights import opensea_client


class _FakeResponse:
    """Minimal stand-in for requests.Response, matching this test suite's mocking style."""

    def __init__(self, status_code: int = 200, json_data=None, raise_json_error: bool = False) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("response body is not valid JSON")
        return self._json_data


def _patch_get(monkeypatch: pytest.MonkeyPatch, responses):
    """responses: a callable(url, **kwargs) -> _FakeResponse, or a fixed _FakeResponse."""
    if not callable(responses):
        fixed = responses
        responses = lambda *a, **k: fixed
    monkeypatch.setattr(opensea_client.requests, "get", responses)


# ── get_collection ───────────────────────────────────────────────────────

def test_get_collection_parses_name_image_and_supply(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {
        "name": "Boonies",
        "image_url": "https://i2c.seadn.io/eth/abc/image.png",
        "total_supply": 10000,
    }))
    result = opensea_client.get_collection("boonies")
    assert result == {
        "slug": "boonies",
        "name": "Boonies",
        "image_url": "https://i2c.seadn.io/eth/abc/image.png",
        "total_supply": 10000,
    }


def test_get_collection_omits_untrusted_image_and_missing_supply(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {
        "name": "Boonies",
        "image_url": "https://evil.example.com/image.png",
    }))
    result = opensea_client.get_collection("boonies")
    assert result["image_url"] is None
    assert "total_supply" not in result


def test_get_collection_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(404, {"errors": ["Not found"]}))
    assert opensea_client.get_collection("nonexistent") is None


def test_get_collection_returns_none_on_request_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def raise_it(*a, **k):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr(opensea_client.requests, "get", raise_it)
    assert opensea_client.get_collection("boonies") is None


def test_get_collection_returns_none_on_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, raise_json_error=True))
    assert opensea_client.get_collection("boonies") is None


def test_get_collection_returns_none_on_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, ["not", "a", "dict"]))
    assert opensea_client.get_collection("boonies") is None


# ── get_collection_stats ─────────────────────────────────────────────────

def test_get_collection_stats_parses_nested_total_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {
        "total": {"floor_price": 0.0426, "floor_price_symbol": "ETH", "num_owners": 4200, "market_cap": 512.3},
    }))
    result = opensea_client.get_collection_stats("boonies")
    assert result == {
        "floor_price": 0.0426,
        "floor_price_symbol": "ETH",
        "num_owners": 4200,
        "market_cap": 512.3,
    }


def test_get_collection_stats_falls_back_to_flat_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {"floor_price": 0.05}))
    result = opensea_client.get_collection_stats("boonies")
    assert result["floor_price"] == 0.05
    assert result["floor_price_symbol"] == "ETH"


def test_get_collection_stats_none_when_no_recognizable_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {"total": {}}))
    assert opensea_client.get_collection_stats("boonies") is None


# ── get_listings ─────────────────────────────────────────────────────────

_BASIC_LISTING = {
    "price": {"current": {"currency": "ETH", "decimals": 18, "value": "50000000000000000"}},
    "protocol_data": {
        "parameters": {
            "offer": [{"itemType": 2, "token": "0xabc", "identifierOrCriteria": "1234"}],
        },
    },
}


def test_get_listings_parses_price_and_token_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {"listings": [_BASIC_LISTING], "next": None}))
    result = opensea_client.get_listings("boonies", max_pages=1)
    assert result.items == [{"token_id": 1234, "price": 0.05, "currency": "ETH"}]
    assert result.complete is True


def test_get_listings_skips_unparseable_entries_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {"price": {"current": {"value": "not-an-int", "decimals": 18}}, "protocol_data": {}}
    _patch_get(monkeypatch, _FakeResponse(200, {"listings": [broken, _BASIC_LISTING], "next": None}))
    result = opensea_client.get_listings("boonies", max_pages=1)
    assert result.items == [{"token_id": 1234, "price": 0.05, "currency": "ETH"}]


def test_get_listings_follows_pagination_cursor_until_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"listings": [_BASIC_LISTING], "next": "cursor-2"},
        {"listings": [_BASIC_LISTING], "next": None},
    ]
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params.get("next"))
        return _FakeResponse(200, pages[len(calls) - 1])

    monkeypatch.setattr(opensea_client.requests, "get", fake_get)
    result = opensea_client.get_listings("boonies", max_pages=5)
    assert len(result.items) == 2
    assert result.complete is True
    assert calls == [None, "cursor-2"]


def test_get_listings_stops_at_max_pages_even_if_more_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, {"listings": [_BASIC_LISTING], "next": "always-more"})

    monkeypatch.setattr(opensea_client.requests, "get", fake_get)
    result = opensea_client.get_listings("boonies", max_pages=3)
    assert len(result.items) == 3
    # Capped by max_pages while OpenSea still had more -> not complete, so
    # callers (insights.py) don't present this as the whole picture.
    assert result.complete is False


def test_get_listings_returns_partial_result_flagged_incomplete_on_first_page_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_get(monkeypatch, _FakeResponse(500))
    result = opensea_client.get_listings("boonies", max_pages=3)
    assert result.items == []
    assert result.complete is False


def test_get_listings_mid_pagination_failure_returns_partial_items_flagged_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResponse(200, {"listings": [_BASIC_LISTING], "next": "cursor-2"})
        return _FakeResponse(500)

    monkeypatch.setattr(opensea_client.requests, "get", fake_get)
    result = opensea_client.get_listings("boonies", max_pages=5)
    assert len(result.items) == 1  # first page's listing kept, not discarded
    assert result.complete is False


# ── get_collection_nfts ──────────────────────────────────────────────────

_RAW_NFT = {
    "identifier": "42",
    "name": "Boonie #42",
    "image_url": "https://i2c.seadn.io/eth/abc/42.png",
    "traits": [{"trait_type": "Background", "value": "Blue"}, {"trait_type": "Eyes", "value": "Laser"}],
}


def test_get_collection_nfts_parses_id_name_image_traits(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _FakeResponse(200, {"nfts": [_RAW_NFT], "next": None}))
    result = opensea_client.get_collection_nfts("boonies", max_pages=1)
    assert result.items == [{
        "token_id": 42,
        "name": "Boonie #42",
        "image_url": "https://i2c.seadn.io/eth/abc/42.png",
        "attrs": {"Background": "Blue", "Eyes": "Laser"},
    }]
    assert result.complete is True


def test_get_collection_nfts_skips_entries_missing_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {"name": "no id"}
    _patch_get(monkeypatch, _FakeResponse(200, {"nfts": [broken, _RAW_NFT], "next": None}))
    result = opensea_client.get_collection_nfts("boonies", max_pages=1)
    assert len(result.items) == 1
    assert result.items[0]["token_id"] == 42


def test_get_collection_nfts_untrusted_image_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {**_RAW_NFT, "image_url": "https://evil.example.com/42.png"}
    _patch_get(monkeypatch, _FakeResponse(200, {"nfts": [raw], "next": None}))
    result = opensea_client.get_collection_nfts("boonies", max_pages=1)
    assert result.items[0]["image_url"] is None


def test_get_collection_nfts_capped_by_max_pages_flagged_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url, headers=None, params=None, timeout=None):
        return _FakeResponse(200, {"nfts": [_RAW_NFT], "next": "always-more"})

    monkeypatch.setattr(opensea_client.requests, "get", fake_get)
    result = opensea_client.get_collection_nfts("boonies", max_pages=2)
    assert len(result.items) == 2
    assert result.complete is False


def test_get_collection_nfts_sanitizes_control_characters_in_name_and_trait_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = {
        "identifier": "7",
        "name": "Evil​Name",
        "traits": [{"trait_type": "Back‮ground", "value": "Bl​ue"}],
    }
    _patch_get(monkeypatch, _FakeResponse(200, {"nfts": [hostile], "next": None}))
    result = opensea_client.get_collection_nfts("boonies", max_pages=1)
    nft = result.items[0]
    assert nft["name"] == "EvilName"
    assert nft["attrs"] == {"Background": "Blue"}
