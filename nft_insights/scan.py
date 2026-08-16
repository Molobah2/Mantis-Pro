"""
Orchestrates one collection scan: calls opensea_client for stats/listings/
NFTs, sales_history for retroactive weekly sales comparisons, history for
persisted floor/listing snapshots, runs the InsightEngine, and caches the
result in memory — same TTL-dict-cache idiom as agent.py's _holders_cache
(agent.py:~1292), just scoped to this package instead of living in agent.py.

Every fetch here also records a snapshot and marks the collection tracked
(history.py) — a "free" data point layered on requests we're making
anyway, on top of the periodic background job (agent.py's APScheduler
block) that keeps snapshotting collections between scans.
"""
import threading
import time

from . import config, history, insights, opensea_client, sales_history

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _snapshot_input(slug: str, stats: dict, listings_result, supply: int | None) -> "history.SnapshotInput":
    return history.SnapshotInput(
        slug=slug,
        floor_price=stats.get("floor_price"),
        floor_price_symbol=stats.get("floor_price_symbol"),
        # Only recorded when the listings fetch actually reached its
        # natural end — a capped/partial count would corrupt future
        # "listings -41%" comparisons built on this row.
        listed_count=len(listings_result.items) if listings_result.complete else None,
        supply=supply,
        num_owners=stats.get("num_owners"),
    )


def _fetch(slug: str) -> dict:
    real_collection = opensea_client.get_collection(slug)
    collection = real_collection or {"slug": slug, "name": slug, "image_url": None}
    stats = opensea_client.get_collection_stats(slug) or {}
    listings_result = opensea_client.get_listings(slug)

    # Sales-history calls run BEFORE the NFT/trait fetch below (which can be
    # up to MAX_NFT_PAGES=100 sequential requests on its own for a large
    # collection) rather than after — a single scan can add up to ~160
    # sequential OpenSea calls, and whichever calls run last are the ones
    # most likely to get rate-limited if OpenSea throttles a burst from one
    # key. period_performance's retroactive comparison is the whole point
    # of this fetch; it shouldn't lose the rate-limit budget race to the
    # NFT pagination that only rarity-dependent insights need.
    now = time.time()
    window = config.PERIOD_PERFORMANCE_WINDOW_S
    this_period = sales_history.get_sales_in_range(slug, now - window, now)
    last_period = sales_history.get_sales_in_range(slug, now - 2 * window, now - window)
    snapshot_week_ago = history.get_snapshot_near(slug, now - window, config.SNAPSHOT_STALENESS_TOLERANCE_S)

    supply = collection.get("total_supply")
    # complete=False by default: if the supply-cap check below skips the
    # fetch entirely, "complete" doesn't apply — we never attempted it.
    nfts_result = opensea_client.PaginatedResult(items=[], complete=False)
    if supply is None or supply <= config.MAX_SUPPLY_FOR_RARITY:
        nfts_result = opensea_client.get_collection_nfts(slug)

    scan_data = {
        "collection": collection,
        "stats": stats,
        "listings": listings_result.items,
        "listings_complete": listings_result.complete,
        "nfts": nfts_result.items,
        "nfts_complete": nfts_result.complete,
        "sales_this_period": this_period.items,
        "sales_this_period_complete": this_period.complete,
        "sales_last_period": last_period.items,
        "sales_last_period_complete": last_period.complete,
        "snapshot_week_ago": snapshot_week_ago,
        "days_tracked": history.days_tracked(slug),
    }

    # Only track/snapshot slugs OpenSea actually confirms are real
    # collections — routes.py's slug regex is a shape check, not an
    # existence check, so without this a garbage-but-regex-valid slug
    # would get tracked forever and re-fetched by the background snapshot
    # job (agent.py) every cycle, an unbounded, attacker-steerable cost
    # against the OpenSea API budget.
    if real_collection is not None:
        history.track_collection(slug)
        history.record_snapshot(_snapshot_input(slug, stats, listings_result, supply))

    return {
        "scan": scan_data,
        "insights": insights.generate(scan_data),
        "fetched_at": now,
    }


def get_scan(slug: str, force_refresh: bool = False) -> dict:
    """Returns {"scan": {...}, "insights": [Insight, ...], "fetched_at": epoch}
    for the given slug, using an in-memory cache (config.SCAN_CACHE_TTL_S)
    unless force_refresh is set. Not process-persistent — same tradeoff
    accepted by the other in-memory caches in this codebase; a restart just
    means the next request re-scans."""
    with _cache_lock:
        cached = _cache.get(slug)
        if not force_refresh and cached and time.time() - cached["fetched_at"] < config.SCAN_CACHE_TTL_S:
            return cached

    result = _fetch(slug)
    with _cache_lock:
        _cache[slug] = result
    return result


def find_insight(slug: str, insight_id: str) -> tuple[dict, "insights.Insight | None"]:
    """Returns (scan_result, insight) for a cached/fresh scan, insight=None
    if insight_id doesn't match anything the current scan produced (e.g. a
    stale link after the cache refreshed and that story no longer holds)."""
    result = get_scan(slug)
    match = next((i for i in result["insights"] if i.id == insight_id), None)
    return result, match


def snapshot_tracked_collections() -> None:
    """Background job (see agent.py's APScheduler wiring, config.SNAPSHOT_INTERVAL_HOURS):
    takes a fresh floor/listing snapshot for the stalest tracked
    collections, independent of whether anyone is actively scanning them
    right now — this is what actually builds up comparable history over
    time, on top of the "free" snapshot get_scan already takes on a cache
    miss. Capped at config.MAX_SNAPSHOT_COLLECTIONS_PER_TICK per run (each
    slug costs several outbound OpenSea calls) — history.get_stalest_
    tracked_collections still rotates fairly through a larger tracked set
    across successive runs by always picking whichever slugs have gone
    longest without a snapshot. Deliberately doesn't call
    history.track_collection (that would bump last_scanned_at, which
    should only reflect real user activity, not this job ticking). One
    broken slug never blocks the rest of the batch."""
    slugs = history.get_stalest_tracked_collections(config.MAX_SNAPSHOT_COLLECTIONS_PER_TICK)
    for slug in slugs:
        try:
            collection = opensea_client.get_collection(slug)
            stats = opensea_client.get_collection_stats(slug) or {}
            listings_result = opensea_client.get_listings(slug)
            supply = collection.get("total_supply") if collection else None
            history.record_snapshot(_snapshot_input(slug, stats, listings_result, supply))
        except Exception as e:
            print(f"[nft_insights] background snapshot failed for {slug!r}: {e}")
