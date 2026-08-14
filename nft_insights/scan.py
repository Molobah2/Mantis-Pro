"""
Orchestrates one collection scan: calls opensea_client for stats/listings/
NFTs, runs the InsightEngine, and caches the result in memory — same
TTL-dict-cache idiom as agent.py's _holders_cache (agent.py:~1292), just
scoped to this package instead of living in agent.py.
"""
import threading
import time

from . import config, insights, opensea_client

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _fetch(slug: str) -> dict:
    collection = opensea_client.get_collection(slug) or {"slug": slug, "name": slug, "image_url": None}
    stats = opensea_client.get_collection_stats(slug) or {}
    listings_result = opensea_client.get_listings(slug)

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
    }
    return {
        "scan": scan_data,
        "insights": insights.generate(scan_data),
        "fetched_at": time.time(),
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
