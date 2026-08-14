"""
Turns a structured Insight (see insights.py) into human-readable X copy: one
headline plus three caption variants. Deterministic string templates, not an
LLM call — keeps Phase 1 simple, fast, and trivially testable. Every
template field comes straight from Insight.data, so captions can't say
anything the insight didn't actually find.
"""
from .insights import Insight


def _fmt_price(value: float, currency: str) -> str:
    text = f"{value:.4g}" if value < 1 else f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text} {currency}"


def _market_snapshot_lines(d: dict, name: str) -> tuple[str, list[str]]:
    if "listed_pct" in d:
        headline = f"Only {d['listed_pct']}% of {name} is currently listed"
    elif "floor_price" in d:
        headline = f"{name}: floor at {_fmt_price(d['floor_price'], d.get('floor_price_symbol', 'ETH'))}"
    else:
        headline = f"{name}: {d['listed']} listed right now"

    supply_bit = f" of {d['supply']}" if "supply" in d else ""
    floor_bit = (
        f" Floor: {_fmt_price(d['floor_price'], d.get('floor_price_symbol', 'ETH'))}."
        if "floor_price" in d else ""
    )
    captions = [
        f"{d['listed']} {name} listed{supply_bit} right now.{floor_bit}",
        f"{name} market check: {d['listed']} listed.{floor_bit}",
        f"Snapshot: {d['listed']} {name} for sale.{floor_bit} Would you buy in?",
    ]
    return headline, captions


def _listing_scarcity_lines(d: dict, name: str) -> tuple[str, list[str]]:
    headline = f"Only {d['listed_pct']}% of {name} is listed"
    captions = [
        f"Only {d['listed']}/{d['supply']} {name} are currently listed ({d['listed_pct']}%).",
        f"{100 - d['listed_pct']:.2f}% of {name} is off-market right now.",
        f"Just {d['listed']} {name} for sale out of {d['supply']}. Scarce.",
    ]
    return headline, captions


def _rarest_listed_nft_lines(d: dict, name: str) -> tuple[str, list[str]]:
    headline = f"The rarest {name} currently listed"
    price = _fmt_price(d["price"], d["currency"])
    rank_bit = f"Rank {d['rank']}/{d['total']}."
    multiple_bit = (
        f" Listed at {d['price_vs_floor_multiple']}x floor." if d.get("price_vs_floor_multiple") else ""
    )
    captions = [
        f"#{d['token_id']} — {rank_bit} Listed for {price}.{multiple_bit}",
        f"The rarest {name} on the market right now is #{d['token_id']} ({rank_bit}) for {price}.",
        f"Would you pay {price} for the #{d['rank']} rarest {name}?",
    ]
    return headline, captions


def _rarest_listed_trait_lines(d: dict, name: str) -> tuple[str, list[str]]:
    headline = f"Only {d['count']} {name} have {d['value']} {d['trait_type']}"
    price = _fmt_price(d["cheapest_price"], d["currency"])
    captions = [
        f"{d['count']}/{d['total']} {name} have {d['value']} {d['trait_type']}. "
        f"{d['listed_count']} listed — cheapest at {price}.",
        f"Rare trait alert: {d['value']} {d['trait_type']} ({d['count']}/{d['total']}). Only {d['listed_count']} for sale.",
        f"Which one are you taking? {d['listed_count']} {d['value']} {d['trait_type']} {name} listed from {price}.",
    ]
    return headline, captions


def _cheap_listings_lines(d: dict, name: str) -> tuple[str, list[str]]:
    price = _fmt_price(d["threshold_price"], d["currency"])
    anchor = "floor" if d["anchor_is_floor"] else "the cheapest listing"
    headline = f"Every {name} listed under {price}"
    captions = [
        f"Every {name} currently listed under {price} ({d['multiple']}x {anchor}). {d['matched_count']} of them.",
        f"{d['matched_count']} {name} listed under {price} right now.",
        f"These {d['matched_count']} {name} are all under {price}. Which one?",
    ]
    return headline, captions


_BUILDERS = {
    "market_snapshot": _market_snapshot_lines,
    "listing_scarcity": _listing_scarcity_lines,
    "rarest_listed_nft": _rarest_listed_nft_lines,
    "rarest_listed_trait": _rarest_listed_trait_lines,
    "cheap_listings": _cheap_listings_lines,
}


def headline(insight: Insight, collection_name: str) -> str:
    builder = _BUILDERS.get(insight.type)
    if builder is None:
        return collection_name
    text, _ = builder(insight.data, collection_name)
    return text


def caption_options(insight: Insight, collection_name: str) -> list[str]:
    builder = _BUILDERS.get(insight.type)
    if builder is None:
        return []
    _, captions = builder(insight.data, collection_name)
    return captions
