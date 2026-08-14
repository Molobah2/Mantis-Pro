import pytest

from nft_insights import config, opensea_client, scan


@pytest.fixture(autouse=True)
def _clear_cache():
    scan._cache.clear()
    yield
    scan._cache.clear()


def _stub_client(monkeypatch: pytest.MonkeyPatch, call_counter: list) -> None:
    def fake_get_collection(slug):
        call_counter.append(slug)
        return {"slug": slug, "name": "Boonies", "image_url": None, "total_supply": 100}

    monkeypatch.setattr(opensea_client, "get_collection", fake_get_collection)
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.1})
    monkeypatch.setattr(
        opensea_client, "get_listings",
        lambda slug: opensea_client.PaginatedResult(items=[{"token_id": 1, "price": 0.1, "currency": "ETH"}], complete=True),
    )
    monkeypatch.setattr(
        opensea_client, "get_collection_nfts",
        lambda slug: opensea_client.PaginatedResult(items=[{"token_id": 1, "attrs": {"Eyes": "Laser"}}], complete=True),
    )


def test_get_scan_returns_insights_and_scan_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, [])
    result = scan.get_scan("boonies")
    assert result["scan"]["collection"]["name"] == "Boonies"
    assert len(result["insights"]) >= 1
    assert "fetched_at" in result


def test_get_scan_uses_cache_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _stub_client(monkeypatch, calls)
    scan.get_scan("boonies")
    scan.get_scan("boonies")
    assert calls == ["boonies"]  # second call served from cache, no re-fetch


def test_get_scan_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _stub_client(monkeypatch, calls)
    scan.get_scan("boonies")
    scan.get_scan("boonies", force_refresh=True)
    assert calls == ["boonies", "boonies"]


def test_get_scan_refetches_after_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    _stub_client(monkeypatch, calls)
    scan.get_scan("boonies")
    scan._cache["boonies"]["fetched_at"] -= config.SCAN_CACHE_TTL_S + 1
    scan.get_scan("boonies")
    assert calls == ["boonies", "boonies"]


def test_get_scan_skips_nft_fetch_when_supply_exceeds_rarity_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": "Big", "image_url": None, "total_supply": config.MAX_SUPPLY_FOR_RARITY + 1})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))
    calls: list = []
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: calls.append(slug))
    scan.get_scan("huge-collection")
    assert calls == []


def test_get_scan_survives_all_client_calls_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: None)
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: None)
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    result = scan.get_scan("ghost-collection")
    assert result["scan"]["collection"]["slug"] == "ghost-collection"
    assert len(result["insights"]) == 1  # market snapshot only


def test_get_scan_threads_completeness_flags_into_scan_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": "Boonies", "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    result = scan.get_scan("boonies")
    assert result["scan"]["listings_complete"] is False
    assert result["scan"]["nfts_complete"] is False


def test_find_insight_returns_matching_insight(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, [])
    result, found = scan.find_insight("boonies", "market_snapshot")
    assert found is not None
    assert found.id == "market_snapshot"


def test_find_insight_returns_none_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, [])
    _, found = scan.find_insight("boonies", "does_not_exist")
    assert found is None
