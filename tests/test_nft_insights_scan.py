import time

import pytest

from nft_insights import config, history, opensea_client, sales_history, scan


@pytest.fixture(autouse=True)
def _clear_cache():
    scan._cache.clear()
    yield
    scan._cache.clear()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """scan.py now writes real snapshot/tracking rows on every fetch — point
    history at a fresh temp DB so tests never touch a real/shared DB file."""
    db_path = tmp_path / "nft_insights_snapshots_test.db"
    monkeypatch.setattr(history, "_DB", str(db_path))
    yield db_path


def _empty_sales(slug, after_epoch, before_epoch, max_pages=config.MAX_SALES_EVENT_PAGES):
    return opensea_client.PaginatedResult(items=[], complete=True)


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
    monkeypatch.setattr(sales_history, "get_sales_in_range", _empty_sales)


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
    monkeypatch.setattr(sales_history, "get_sales_in_range", _empty_sales)
    calls: list = []
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: calls.append(slug))
    scan.get_scan("huge-collection")
    assert calls == []


def test_get_scan_survives_all_client_calls_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: None)
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: None)
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(sales_history, "get_sales_in_range", _empty_sales)
    result = scan.get_scan("ghost-collection")
    assert result["scan"]["collection"]["slug"] == "ghost-collection"
    assert len(result["insights"]) == 1  # market snapshot only


def test_get_scan_threads_completeness_flags_into_scan_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": "Boonies", "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(sales_history, "get_sales_in_range", _empty_sales)
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


# ── history wiring ────────────────────────────────────────────────────

def test_get_scan_tracks_collection_and_records_a_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, [])
    scan.get_scan("boonies")
    assert history.get_tracked_collections() == ["boonies"]
    snap = history.get_snapshot_near("boonies", time.time(), max_distance_s=60)
    assert snap is not None
    assert snap["floor_price"] == 0.1


def test_get_scan_never_tracks_a_slug_opensea_does_not_recognize(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a garbage-but-regex-valid slug that doesn't resolve to a
    real OpenSea collection must never enter tracked_collections — otherwise
    it gets re-fetched forever by the background snapshot job, an unbounded
    cost an attacker could trigger for free."""
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: None)
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: None)
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=False))
    monkeypatch.setattr(sales_history, "get_sales_in_range", _empty_sales)

    scan.get_scan("not-a-real-collection-zzz")

    assert history.get_tracked_collections() == []
    assert history.get_snapshot_near("not-a-real-collection-zzz", time.time(), max_distance_s=60) is None


def test_get_scan_records_listed_count_only_when_listings_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": "Boonies", "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.1})
    monkeypatch.setattr(
        opensea_client, "get_listings",
        lambda slug: opensea_client.PaginatedResult(items=[{"token_id": 1, "price": 0.1, "currency": "ETH"}], complete=False),
    )
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))
    monkeypatch.setattr(sales_history, "get_sales_in_range", _empty_sales)

    scan.get_scan("boonies")
    snap = history.get_snapshot_near("boonies", time.time(), max_distance_s=60)
    assert snap is not None
    assert snap["listed_count"] is None  # capped/incomplete fetch -> never recorded as fact


def test_get_scan_passes_sales_history_windows_into_scan_data(monkeypatch: pytest.MonkeyPatch) -> None:
    sale = {"token_id": 1, "price": 0.1, "symbol": "ETH", "timestamp": 0, "name": None, "image_url": None}
    calls = []

    def fake_sales(slug, after_epoch, before_epoch, max_pages=config.MAX_SALES_EVENT_PAGES):
        calls.append((after_epoch, before_epoch))
        return opensea_client.PaginatedResult(items=[sale], complete=True)

    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": "Boonies", "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.1})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))
    monkeypatch.setattr(opensea_client, "get_collection_nfts", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))
    monkeypatch.setattr(sales_history, "get_sales_in_range", fake_sales)

    result = scan.get_scan("boonies")
    assert result["scan"]["sales_this_period"] == [sale]
    assert result["scan"]["sales_last_period"] == [sale]
    assert result["scan"]["sales_this_period_complete"] is True
    assert len(calls) == 2
    # this-period window is more recent than last-period window
    assert calls[0][0] > calls[1][0]


def test_get_scan_includes_days_tracked_in_scan_data(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_client(monkeypatch, [])
    result = scan.get_scan("boonies")
    assert result["scan"]["days_tracked"] == pytest.approx(0.0, abs=0.01)


# ── background snapshot job ──────────────────────────────────────────

def test_snapshot_tracked_collections_records_a_snapshot_per_tracked_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    history.track_collection("boonies")
    history.track_collection("godpull")

    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": slug, "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.05})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))

    scan.snapshot_tracked_collections()

    for slug in ("boonies", "godpull"):
        snap = history.get_snapshot_near(slug, time.time(), max_distance_s=60)
        assert snap is not None
        assert snap["floor_price"] == 0.05


def test_snapshot_tracked_collections_does_not_bump_last_scanned_at(monkeypatch: pytest.MonkeyPatch) -> None:
    history.track_collection("boonies")
    before = history.days_tracked("boonies")

    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: {"slug": slug, "name": slug, "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.05})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))
    scan.snapshot_tracked_collections()

    # days_tracked is derived from first_tracked_at, which the background
    # job must not touch (only get_scan's real user-triggered path should).
    assert history.days_tracked("boonies") == pytest.approx(before, abs=0.01)


def test_snapshot_tracked_collections_one_bad_slug_does_not_block_others(monkeypatch: pytest.MonkeyPatch) -> None:
    history.track_collection("broken")
    history.track_collection("boonies")

    def fake_get_collection(slug):
        if slug == "broken":
            raise RuntimeError("boom")
        return {"slug": slug, "name": slug, "image_url": None, "total_supply": 100}

    monkeypatch.setattr(opensea_client, "get_collection", fake_get_collection)
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.05})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))

    scan.snapshot_tracked_collections()  # must not raise

    assert history.get_snapshot_near("boonies", time.time(), max_distance_s=60) is not None
    assert history.get_snapshot_near("broken", time.time(), max_distance_s=60) is None


def test_snapshot_tracked_collections_handles_zero_tracked_collections(monkeypatch: pytest.MonkeyPatch) -> None:
    scan.snapshot_tracked_collections()  # must not raise on a fresh deploy with nothing tracked yet


def test_snapshot_tracked_collections_bounded_by_per_tick_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: an unbounded tracked_collections table must not make one
    background-job run's cost grow without limit."""
    for i in range(10):
        history.track_collection(f"slug-{i}")
    monkeypatch.setattr(config, "MAX_SNAPSHOT_COLLECTIONS_PER_TICK", 3)

    calls: list = []
    monkeypatch.setattr(opensea_client, "get_collection", lambda slug: calls.append(slug) or {"slug": slug, "name": slug, "image_url": None, "total_supply": 100})
    monkeypatch.setattr(opensea_client, "get_collection_stats", lambda slug: {"floor_price": 0.05})
    monkeypatch.setattr(opensea_client, "get_listings", lambda slug: opensea_client.PaginatedResult(items=[], complete=True))

    scan.snapshot_tracked_collections()
    assert len(calls) == 3
