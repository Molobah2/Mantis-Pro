"""
Retroactive sales/volume history via OpenSea's v2 events endpoint —
GET /api/v2/events/collection/{slug}?event_type=sale&after=X&before=Y.

Verified live against the deployed proxy (mantispro.xyz/api/opensea, which
forwards to OpenSea with the real key): after/before correctly bound
distinct windows, 100 events/page, cursor-paginated. Unlike floor price or
listing counts (see history.py's docstring — no such endpoint exists),
OpenSea keeps the full sale event log, so "this week vs last week"
volume/sales/avg-price comparisons work on the very first scan of a
collection, no waiting for our own snapshots to accumulate.

Modeled on opensea_client.py: every function returns a PaginatedResult
(items + complete) rather than raising, and never presents a truncated
fetch as if it covered the whole window.
"""
import logging

from opensea_automint.drops import _validate_image_url

from . import config
from .opensea_client import PaginatedResult, _clean_text, _get

logger = logging.getLogger(__name__)

_SALE_CURRENCIES = ("ETH", "WETH")  # 1:1-pegged — safe to sum as one "volume" figure


def _extract_sale_event(raw: dict) -> dict | None:
    try:
        payment = raw["payment"]
        value = int(payment["quantity"])
        decimals = int(payment["decimals"])
        symbol = _clean_text(payment.get("symbol")) or "ETH"
        price = value / (10 ** decimals)
        timestamp = float(raw["event_timestamp"])
    except (KeyError, TypeError, ValueError):
        return None

    nft = raw.get("nft") or {}
    try:
        token_id = int(nft["identifier"])
    except (KeyError, TypeError, ValueError):
        token_id = None

    return {
        "token_id": token_id,
        "price": price,
        "symbol": symbol,
        "timestamp": timestamp,
        "name": _clean_text(nft.get("name")),
        "image_url": _validate_image_url(nft.get("image_url")),
    }


def get_sales_in_range(
    slug: str, after_epoch: float, before_epoch: float,
    max_pages: int = config.MAX_SALES_EVENT_PAGES,
) -> PaginatedResult:
    """Individual sale events within [after_epoch, before_epoch), paginated
    and capped at max_pages * SALES_EVENT_PAGE_SIZE. complete=False whenever
    there may be more sales than we fetched (page cap hit or a request
    failed mid-pagination) — callers must not treat a truncated result as
    the whole window (see insights.py's no-fabrication rule)."""
    sales: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {
            "event_type": "sale",
            "after": int(after_epoch),
            "before": int(before_epoch),
            "limit": config.SALES_EVENT_PAGE_SIZE,
        }
        if cursor:
            params["next"] = cursor
        data = _get(f"/events/collection/{slug}", params=params)
        if data is None:
            return PaginatedResult(items=sales, complete=False)
        for raw in data.get("asset_events") or []:
            parsed = _extract_sale_event(raw)
            if parsed:
                sales.append(parsed)
        cursor = data.get("next")
        if not cursor:
            return PaginatedResult(items=sales, complete=True)
    return PaginatedResult(items=sales, complete=False)


def summarize_sales(sales: list[dict]) -> dict:
    """{"count", "volume_eth", "avg_price_eth", "median_price_eth",
    "other_currency_count"}. Only ETH/WETH sales feed the price figures —
    summing across other currencies (USDC, DAI, ...) would silently
    corrupt "volume" as a single number; those sales are still counted but
    called out separately instead of mixed in."""
    eth_sales = [s for s in sales if s["symbol"] in _SALE_CURRENCIES]
    other_count = len(sales) - len(eth_sales)
    if not eth_sales:
        return {
            "count": len(sales), "volume_eth": 0.0, "avg_price_eth": None,
            "median_price_eth": None, "other_currency_count": other_count,
        }
    prices = sorted(s["price"] for s in eth_sales)
    volume = sum(prices)
    mid = len(prices) // 2
    median = prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / 2
    return {
        "count": len(sales),
        "volume_eth": round(volume, 4),
        "avg_price_eth": round(volume / len(prices), 4),
        "median_price_eth": round(median, 4),
        "other_currency_count": other_count,
    }
