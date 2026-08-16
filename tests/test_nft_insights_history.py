import time
from contextlib import closing

import pytest

from nft_insights import history


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the module at a fresh temp DB file for every test so tests
    never touch a real/shared DB file — mirrors
    tests/test_opensea_automint_store.py's isolated_db fixture."""
    db_path = tmp_path / "nft_insights_snapshots_test.db"
    monkeypatch.setattr(history, "_DB", str(db_path))
    yield db_path


def _snapshot(slug="boonies", floor_price=0.1, listed_count=50, supply=10000, num_owners=3000):
    return history.SnapshotInput(
        slug=slug, floor_price=floor_price, floor_price_symbol="ETH",
        listed_count=listed_count, supply=supply, num_owners=num_owners,
    )


# ── schema / connection ──────────────────────────────────────────────

def test_conn_table_creation_is_idempotent() -> None:
    c1 = history._conn()
    c1.close()
    c2 = history._conn()
    c2.close()


# ── tracked_collections ──────────────────────────────────────────────

def test_track_collection_adds_new_slug() -> None:
    history.track_collection("boonies")
    assert history.get_tracked_collections() == ["boonies"]


def test_track_collection_is_idempotent_for_same_slug() -> None:
    history.track_collection("boonies")
    history.track_collection("boonies")
    assert history.get_tracked_collections() == ["boonies"]


def test_track_collection_tracks_multiple_slugs() -> None:
    history.track_collection("boonies")
    history.track_collection("godpull")
    assert set(history.get_tracked_collections()) == {"boonies", "godpull"}


def _set_first_tracked_at(slug: str, epoch: float) -> None:
    """Test-only: backdates a slug's first_tracked_at directly via SQL,
    rather than mocking the global time module, so time-based assertions
    stay isolated to this test's own DB row."""
    with history._lock, closing(history._conn()) as c:
        c.execute("UPDATE tracked_collections SET first_tracked_at=? WHERE slug=?", (epoch, slug))
        c.commit()


def test_days_tracked_zero_for_untracked_slug() -> None:
    assert history.days_tracked("never-tracked") == 0.0


def test_days_tracked_reflects_first_tracked_at() -> None:
    history.track_collection("boonies")
    _set_first_tracked_at("boonies", time.time() - 3 * 86400)
    assert history.days_tracked("boonies") == pytest.approx(3.0, abs=0.01)


# ── collection_snapshots ──────────────────────────────────────────────

def test_record_snapshot_returns_row_id() -> None:
    row_id = history.record_snapshot(_snapshot())
    assert isinstance(row_id, int)
    assert row_id > 0


def test_get_snapshot_near_returns_none_when_no_snapshots_exist() -> None:
    assert history.get_snapshot_near("boonies", time.time(), max_distance_s=86400) is None


def _set_captured_at(row_id: int, epoch: float) -> None:
    """Test-only: backdates a snapshot row's captured_at directly via SQL."""
    with history._lock, closing(history._conn()) as c:
        c.execute("UPDATE collection_snapshots SET captured_at=? WHERE id=?", (epoch, row_id))
        c.commit()


def test_get_snapshot_near_finds_closest_snapshot_within_tolerance() -> None:
    now = time.time()
    week_ago_id = history.record_snapshot(_snapshot(floor_price=0.05))
    _set_captured_at(week_ago_id, now - 7 * 86400)
    today_id = history.record_snapshot(_snapshot(floor_price=0.09))
    _set_captured_at(today_id, now)

    result = history.get_snapshot_near("boonies", now - 7 * 86400, max_distance_s=3600)
    assert result is not None
    assert result["floor_price"] == 0.05


def test_get_snapshot_near_returns_none_when_closest_is_outside_tolerance() -> None:
    now = time.time()
    row_id = history.record_snapshot(_snapshot())
    _set_captured_at(row_id, now - 30 * 86400)

    # Only one snapshot exists, 30 days before "now" -> way outside a tight
    # tolerance for a "~7 days ago" query. Must not be silently substituted
    # as "close enough".
    result = history.get_snapshot_near("boonies", now - 7 * 86400, max_distance_s=86400)
    assert result is None


def test_get_snapshot_near_scoped_to_slug() -> None:
    history.record_snapshot(_snapshot(slug="boonies", floor_price=0.1))
    history.record_snapshot(_snapshot(slug="godpull", floor_price=0.02))

    result = history.get_snapshot_near("godpull", time.time(), max_distance_s=60)
    assert result is not None
    assert result["floor_price"] == 0.02
    assert result["slug"] == "godpull"


# ── get_stalest_tracked_collections ──────────────────────────────────

def test_get_stalest_tracked_collections_prioritizes_never_snapshotted() -> None:
    history.track_collection("has-snapshot")
    history.record_snapshot(_snapshot(slug="has-snapshot"))
    history.track_collection("never-snapshotted")

    result = history.get_stalest_tracked_collections(10)
    assert result[0] == "never-snapshotted"
    assert "has-snapshot" in result


def test_get_stalest_tracked_collections_orders_by_oldest_snapshot_first() -> None:
    now = time.time()
    history.track_collection("recent")
    recent_id = history.record_snapshot(_snapshot(slug="recent"))
    _set_captured_at(recent_id, now)

    history.track_collection("stale")
    stale_id = history.record_snapshot(_snapshot(slug="stale"))
    _set_captured_at(stale_id, now - 10 * 86400)

    result = history.get_stalest_tracked_collections(10)
    assert result == ["stale", "recent"]


def test_get_stalest_tracked_collections_respects_limit() -> None:
    for i in range(5):
        history.track_collection(f"slug-{i}")

    result = history.get_stalest_tracked_collections(2)
    assert len(result) == 2


def test_get_stalest_tracked_collections_empty_when_nothing_tracked() -> None:
    assert history.get_stalest_tracked_collections(10) == []
