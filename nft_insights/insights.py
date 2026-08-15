"""
InsightEngine: turns a raw collection scan (stats + listings + NFT/trait
data from opensea_client) into a ranked list of Insight objects — structured
findings, not prose (captions.py turns an Insight into headline/caption
text).

North star (per the product spec this implements): every insight must be
traceable to data actually counted from the scan. An insight is only
emitted when its underlying condition genuinely holds; nothing here invents
or estimates a number that wasn't derived from real API data.
"""
from dataclasses import dataclass, field, replace

from . import config, rarity

# Below this listed-of-supply percentage, scarcity is worth calling out on
# its own; above it, the number isn't surprising enough to be a second
# insight beyond the market snapshot.
_SCARCITY_THRESHOLD_PCT = 25.0

# Candidate "every listing under N x floor" multiples to test, smallest
# first — the tightest (most striking) threshold that still clears
# _MIN_CHEAP_LISTINGS wins.
_CHEAP_LISTING_FLOOR_MULTIPLES = (1.1, 1.25, 1.5, 2.0, 3.0)
_MIN_CHEAP_LISTINGS = 3

# rarest_listed_nft's bento backdrop is a hero shown AMONGST a sample of
# other listings, not a claim about all of them — a small backdrop keeps
# the hero visually dominant, so this stays a tight cap.
_MAX_GRID_TOKENS = 12

# cheap_listings and rarest_listed_trait's headline ("every X under $Y" /
# "N have this trait") IS a claim about the full matched set — capping the
# grid well below the real count (e.g. showing 12 of 26 matches) makes the
# card look broken (unfilled space) and understates its own claim. Show
# everything that matched, up to a generous safety cap for the pathological
# case of a very loose threshold matching hundreds of listings.
_MAX_PROOF_TOKENS = 60

# market_snapshot/listing_scarcity aren't claiming "these specific N are the
# ones that matter" the way cheap_listings or rarest_listed_trait are —
# they're just illustrating "here's proof this collection has real, current
# listings." A collection can have hundreds of listings; showing all of them
# isn't useful, but showing none (the previous behavior) left the card
# looking broken. A larger, purely illustrative sample fits that role.
_SNAPSHOT_SAMPLE_SIZE = 50


@dataclass(frozen=True)
class Insight:
    id: str
    type: str
    data: dict
    nft_token_ids: tuple[int, ...]
    score: float
    is_best: bool = field(default=False, compare=False)
    # When set, this token id is one of nft_token_ids and should render
    # larger than the rest — e.g. rarest_listed_nft shown bigger amongst a
    # backdrop of other currently-listed NFTs, not just alone. None means
    # "uniform grid, no single subject" (e.g. cheap_listings).
    hero_token_id: int | None = None


def _cheapest_price_by_token(listings: list[dict]) -> dict[int, dict]:
    best: dict[int, dict] = {}
    for listing in listings:
        tid = listing["token_id"]
        if tid not in best or listing["price"] < best[tid]["price"]:
            best[tid] = listing
    return best


def _market_snapshot(collection: dict, listings: list[dict], stats: dict, listings_complete: bool) -> Insight:
    supply = collection.get("total_supply")
    listed = len(listings)
    data = {"listed": listed}
    # A percentage claims to cover the whole collection's listings — only
    # honest when the listings fetch actually reached its natural end
    # (see opensea_client.PaginatedResult). A capped/partial fetch still
    # gets an honest raw count, just not a percentage.
    if supply and listings_complete:
        data["supply"] = supply
        data["listed_pct"] = round(listed / supply * 100, 2)
    if "floor_price" in stats:
        data["floor_price"] = stats["floor_price"]
        data["floor_price_symbol"] = stats.get("floor_price_symbol", "ETH")
    # Always emitted as the baseline story — modest score so a sharper
    # insight (rarity, scarcity, a price threshold) outranks it when found.
    sample_ids = tuple(_cheapest_price_by_token(listings))[:_SNAPSHOT_SAMPLE_SIZE]
    return Insight(id="market_snapshot", type="market_snapshot", data=data, nft_token_ids=sample_ids, score=0.2)


def _listing_scarcity(collection: dict, listings: list[dict], listings_complete: bool) -> Insight | None:
    supply = collection.get("total_supply")
    if not supply or not listings_complete:
        return None
    listed = len(listings)
    listed_pct = listed / supply * 100
    if listed_pct > _SCARCITY_THRESHOLD_PCT:
        return None
    score = 0.4 + 0.4 * (1 - listed_pct / _SCARCITY_THRESHOLD_PCT)
    sample_ids = tuple(_cheapest_price_by_token(listings))[:_SNAPSHOT_SAMPLE_SIZE]
    return Insight(
        id="listing_scarcity",
        type="listing_scarcity",
        data={"listed": listed, "supply": supply, "listed_pct": round(listed_pct, 2)},
        nft_token_ids=sample_ids,
        score=score,
    )


def _rarest_listed_nft(nfts: list[dict], listings: list[dict], stats: dict) -> Insight | None:
    if not nfts or not listings:
        return None
    by_token = _cheapest_price_by_token(listings)
    ranked = {r.token_id: r for r in rarity.rank_by_rarity(nfts)}
    listed_ranked = [ranked[tid] for tid in by_token if tid in ranked]
    if not listed_ranked:
        return None
    rarest = min(listed_ranked, key=lambda r: r.rank)
    total = len(nfts)
    listing = by_token[rarest.token_id]
    data = {
        "token_id": rarest.token_id,
        "rank": rarest.rank,
        "total": total,
        "price": listing["price"],
        "currency": listing["currency"],
    }
    floor = stats.get("floor_price")
    if floor:
        data["floor_price"] = floor
        data["price_vs_floor_multiple"] = round(listing["price"] / floor, 2) if floor else None
    # Rarer (lower rank / total) -> higher score.
    rarity_fraction = 1 - (rarest.rank - 1) / max(total - 1, 1)

    # Show the rarest listed NFT bigger amongst a backdrop of the OTHER
    # currently-listed NFTs (rarest-first), instead of alone — proves it's
    # rare relative to what's actually available right now, not just an
    # isolated claim. Backdrop count capped the same as every other grid.
    other_listed_ranked = sorted(
        (r for r in listed_ranked if r.token_id != rarest.token_id),
        key=lambda r: r.rank,
    )
    backdrop_ids = tuple(r.token_id for r in other_listed_ranked[: _MAX_GRID_TOKENS - 1])

    return Insight(
        id="rarest_listed_nft",
        type="rarest_listed_nft",
        data=data,
        nft_token_ids=(rarest.token_id, *backdrop_ids),
        score=0.5 + 0.4 * rarity_fraction,
        hero_token_id=rarest.token_id,
    )


def _rarest_listed_trait(nfts: list[dict], listings: list[dict]) -> Insight | None:
    if not nfts or not listings:
        return None
    by_token = _cheapest_price_by_token(listings)
    found = rarity.rarest_trait_with_listing(nfts, listed_token_ids=set(by_token))
    if not found:
        return None
    listed_ids = found["listed_token_ids"]
    cheapest = min((by_token[tid] for tid in listed_ids), key=lambda listing: listing["price"])
    data = {
        "trait_type": found["trait_type"],
        "value": found["value"],
        "count": found["count"],
        "total": found["total"],
        "listed_count": len(listed_ids),
        "cheapest_price": cheapest["price"],
        "currency": cheapest["currency"],
    }
    rarity_fraction = 1 - (found["count"] - 1) / max(found["total"] - 1, 1)
    return Insight(
        id="rarest_listed_trait",
        type="rarest_listed_trait",
        data=data,
        nft_token_ids=tuple(listed_ids[:_MAX_PROOF_TOKENS]),
        score=0.45 + 0.4 * rarity_fraction,
    )


def _cheap_listings(listings: list[dict], stats: dict, listings_complete: bool) -> Insight | None:
    # The headline claims "every {name} listed under {price}" — only true
    # if the listings fetch actually saw every active listing. A capped or
    # partial fetch could be missing an even-cheaper one outside what we
    # saw, which would make "every" false.
    if not listings or not listings_complete:
        return None
    floor = stats.get("floor_price")
    anchor = floor if floor else min(listing["price"] for listing in listings)
    by_token = _cheapest_price_by_token(listings)
    ordered = sorted(by_token.values(), key=lambda listing: listing["price"])

    for multiple in _CHEAP_LISTING_FLOOR_MULTIPLES:
        threshold = anchor * multiple
        matches = [listing for listing in ordered if listing["price"] <= threshold]
        if len(matches) >= _MIN_CHEAP_LISTINGS:
            currency = matches[0]["currency"]
            token_ids = tuple(listing["token_id"] for listing in matches[:_MAX_PROOF_TOKENS])
            # Tighter multiple (closer to floor) is the more striking claim.
            tightness = 1 - (multiple - _CHEAP_LISTING_FLOOR_MULTIPLES[0]) / (
                _CHEAP_LISTING_FLOOR_MULTIPLES[-1] - _CHEAP_LISTING_FLOOR_MULTIPLES[0]
            )
            return Insight(
                id="cheap_listings",
                type="cheap_listings",
                data={
                    "threshold_price": round(threshold, 4),
                    "multiple": multiple,
                    "currency": currency,
                    "matched_count": len(matches),
                    "total_listed": len(by_token),
                    "anchor_is_floor": bool(floor),
                },
                nft_token_ids=token_ids,
                score=0.4 + 0.3 * tightness + min(len(matches) / _MAX_GRID_TOKENS, 1) * 0.1,
            )
    return None


def generate(scan: dict) -> list[Insight]:
    """scan: {"collection": {...}, "stats": {...}, "listings": [...], "nfts": [...]}
    as produced by scan.run_scan. Returns insights ranked highest-score
    first, with the top one's is_best set to True. Never raises — a
    partially-populated scan (e.g. supply unknown, no NFT/trait data
    fetched) just yields fewer insights, not fabricated ones."""
    collection = scan.get("collection") or {}
    stats = scan.get("stats") or {}
    listings = scan.get("listings") or []
    nfts = scan.get("nfts") or []
    # Defaults to True (assume complete) when the scan dict doesn't specify
    # it — matches how a hand-built/synthetic scan is normally meant to
    # represent "this is exactly all the data".
    listings_complete = scan.get("listings_complete", True)
    nfts_complete = scan.get("nfts_complete", True)

    supply = collection.get("total_supply")
    rarity_usable = (
        bool(nfts) and nfts_complete and listings_complete
        and (supply is None or supply <= config.MAX_SUPPLY_FOR_RARITY)
    )

    candidates = [
        _market_snapshot(collection, listings, stats, listings_complete),
        _listing_scarcity(collection, listings, listings_complete),
        _cheap_listings(listings, stats, listings_complete),
    ]
    if rarity_usable:
        candidates.append(_rarest_listed_nft(nfts, listings, stats))
        candidates.append(_rarest_listed_trait(nfts, listings))

    insights = [insight for insight in candidates if insight is not None]
    insights.sort(key=lambda insight: insight.score, reverse=True)
    if insights:
        insights[0] = replace(insights[0], is_best=True)
    return insights
