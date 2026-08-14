"""
Thin, defensive client for the OpenSea v2 REST API — the pieces of it this
package needs (collection stats, active listings, per-NFT traits/images).

Modeled on opensea_automint/collection_details.py's
fetch_collection_details_via_api: every public function here returns None /
an empty list on any failure (bad slug, network error, non-200, malformed
JSON) rather than raising, and logs a warning so failures are visible without
taking the caller down. Never fabricates data that OpenSea didn't actually
return — callers (insights.py) rely on that to honor their own
never-fabricate rule.
"""
import logging
import re
from dataclasses import dataclass

import requests

from opensea_automint.drops import _validate_image_url

from . import config

logger = logging.getLogger(__name__)

_NFT_ITEM_TYPES = (2, 3)  # Seaport ItemType.ERC721, ItemType.ERC1155

# Collection names and NFT trait values are attacker-controllable — anyone
# can name an OpenSea collection or mint an NFT with an arbitrary trait
# value, and that text flows straight into rendered card images and
# auto-generated X captions. Strip control/bidi-override/zero-width
# characters (which can make displayed text read differently than its
# literal content) and cap length (so one adversarial name can't blow up
# card layout) at the source, once, rather than at every place this text
# later gets used.
_CONTROL_CHARS_RE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    "​-‏‪-‮⁠-⁤]"
)
_MAX_TEXT_LENGTH = 80


@dataclass(frozen=True)
class PaginatedResult:
    """items: whatever was successfully parsed. complete: True only if
    pagination actually reached its natural end (OpenSea stopped returning a
    "next" cursor) — False if it was cut short by max_pages or a mid-fetch
    request failure. Callers (insights.py) must not present a claim derived
    from an incomplete result as if it covered the whole collection — see
    insights.py's no-fabrication rule."""
    items: list[dict]
    complete: bool


def _clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL_CHARS_RE.sub("", value).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_TEXT_LENGTH]


def _headers() -> dict:
    headers = {"accept": "application/json"}
    key = config.get_api_key()
    if key:
        headers["x-api-key"] = key
    return headers


def _get(path: str, params: dict | None = None) -> dict | None:
    url = f"{config.OPENSEA_API_BASE}{path}"
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=config.API_REQUEST_TIMEOUT_S)
    except requests.exceptions.RequestException as e:
        logger.warning("[nft_insights] OpenSea request failed for %s: %s", path, e)
        return None
    if r.status_code != 200:
        logger.warning("[nft_insights] OpenSea returned HTTP %d for %s", r.status_code, path)
        return None
    try:
        data = r.json()
    except ValueError as e:
        logger.warning("[nft_insights] OpenSea non-JSON response for %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("[nft_insights] OpenSea unexpected response shape for %s", path)
        return None
    return data


def get_collection(slug: str) -> dict | None:
    """Collection identity + supply. Returns {"slug", "name", "image_url",
    "total_supply"} or None. total_supply is omitted (not zero-filled) when
    OpenSea doesn't report it, so callers never mistake "unknown" for "empty
    collection"."""
    data = _get(f"/collections/{slug}")
    if data is None:
        return None
    out = {
        "slug": slug,
        "name": _clean_text(data.get("name")) or slug,
        "image_url": _validate_image_url(data.get("image_url")),
    }
    supply = data.get("total_supply")
    if isinstance(supply, int) and supply > 0:
        out["total_supply"] = supply
    return out


def get_collection_stats(slug: str) -> dict | None:
    """Floor price + market stats from the dedicated /stats endpoint.
    Returns {"floor_price", "floor_price_symbol", "num_owners",
    "market_cap"} with only the keys OpenSea actually returned, or None on
    failure. Tries the documented {"total": {...}} shape first; falls back
    to a flat top-level shape the way agent.py's _server_floor() already
    hedges for, in case OpenSea's response shape varies by account/version."""
    data = _get(f"/collections/{slug}/stats")
    if data is None:
        return None
    total = data.get("total") if isinstance(data.get("total"), dict) else data

    out: dict = {}
    floor = total.get("floor_price")
    if isinstance(floor, (int, float)):
        out["floor_price"] = float(floor)
        out["floor_price_symbol"] = total.get("floor_price_symbol") or "ETH"
    owners = total.get("num_owners")
    if isinstance(owners, int):
        out["num_owners"] = owners
    market_cap = total.get("market_cap")
    if isinstance(market_cap, (int, float)):
        out["market_cap"] = float(market_cap)
    return out or None


def _extract_listing(raw: dict) -> dict | None:
    """One Seaport listing -> {"token_id": int, "price": float, "currency": str},
    or None if the shape doesn't parse (skipped, not fabricated)."""
    try:
        current = raw["price"]["current"]
        value = int(current["value"])
        decimals = int(current["decimals"])
        currency = current.get("currency") or "ETH"
        price = value / (10 ** decimals)
    except (KeyError, TypeError, ValueError):
        return None

    offer = (raw.get("protocol_data") or {}).get("parameters", {}).get("offer") or []
    token_id = None
    for item in offer:
        if item.get("itemType") in _NFT_ITEM_TYPES:
            try:
                token_id = int(item["identifierOrCriteria"])
            except (KeyError, TypeError, ValueError):
                continue
            break
    if token_id is None:
        return None

    return {"token_id": token_id, "price": price, "currency": currency}


def get_listings(slug: str, max_pages: int = config.MAX_LISTINGS_PAGES) -> PaginatedResult:
    """Active listings, paginated, capped at max_pages * LISTINGS_PAGE_SIZE
    so a huge collection can't turn one scan into an unbounded crawl.
    Individual listings that don't parse are skipped, not fabricated.
    complete=False whenever there were more listings than we fetched (page
    cap hit or a request failed mid-pagination) — see PaginatedResult."""
    listings: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"limit": config.LISTINGS_PAGE_SIZE}
        if cursor:
            params["next"] = cursor
        data = _get(f"/listings/collection/{slug}/all", params=params)
        if data is None:
            return PaginatedResult(items=listings, complete=False)
        for raw in data.get("listings") or []:
            parsed = _extract_listing(raw)
            if parsed:
                listings.append(parsed)
        cursor = data.get("next")
        if not cursor:
            return PaginatedResult(items=listings, complete=True)
    # Exhausted max_pages while OpenSea still had a "next" cursor -> capped,
    # not complete.
    return PaginatedResult(items=listings, complete=False)


def _extract_nft(raw: dict) -> dict | None:
    try:
        token_id = int(raw["identifier"])
    except (KeyError, TypeError, ValueError):
        return None
    attrs = {}
    for trait in raw.get("traits") or []:
        trait_type = _clean_text(trait.get("trait_type"))
        value = trait.get("value")
        if trait_type is not None and "value" in trait:
            # Numeric/bool trait values (e.g. a "Level" score) pass through
            # unchanged — _clean_text only applies to strings, since it's
            # the string case that can carry hostile control/bidi chars.
            attrs[trait_type] = _clean_text(value) if isinstance(value, str) else value
    return {
        "token_id": token_id,
        "name": _clean_text(raw.get("name")) or f"#{token_id}",
        "image_url": _validate_image_url(raw.get("image_url")),
        "attrs": attrs,
    }


def get_collection_nfts(slug: str, max_pages: int = config.MAX_NFT_PAGES) -> PaginatedResult:
    """NFTs (id, name, image, traits) for the whole collection, paginated and
    capped the same way as get_listings. This is the source for trait-rarity
    scoring — callers must check .complete (not just the item count against
    config.MAX_SUPPLY_FOR_RARITY) before trusting this as "the whole
    collection" rather than a truncated sample."""
    nfts: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        params = {"limit": config.NFTS_PAGE_SIZE}
        if cursor:
            params["next"] = cursor
        data = _get(f"/collection/{slug}/nfts", params=params)
        if data is None:
            return PaginatedResult(items=nfts, complete=False)
        for raw in data.get("nfts") or []:
            parsed = _extract_nft(raw)
            if parsed:
                nfts.append(parsed)
        cursor = data.get("next")
        if not cursor:
            return PaginatedResult(items=nfts, complete=True)
    return PaginatedResult(items=nfts, complete=False)
