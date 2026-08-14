from nft_insights import config, insights


def _listing(token_id: int, price: float, currency: str = "ETH") -> dict:
    return {"token_id": token_id, "price": price, "currency": currency}


def _nft(token_id: int, **attrs) -> dict:
    return {"token_id": token_id, "attrs": attrs}


# ── market snapshot ──────────────────────────────────────────────────────

def test_market_snapshot_always_present_with_available_fields() -> None:
    scan = {
        "collection": {"total_supply": 10000},
        "stats": {"floor_price": 0.05, "floor_price_symbol": "ETH"},
        "listings": [_listing(1, 0.05), _listing(2, 0.06)],
        "nfts": [],
    }
    result = insights.generate(scan)
    snapshot = next(i for i in result if i.type == "market_snapshot")
    assert snapshot.data["listed"] == 2
    assert snapshot.data["supply"] == 10000
    assert snapshot.data["listed_pct"] == 0.02
    assert snapshot.data["floor_price"] == 0.05


def test_market_snapshot_omits_supply_pct_when_supply_unknown() -> None:
    scan = {"collection": {}, "stats": {}, "listings": [_listing(1, 0.05)], "nfts": []}
    result = insights.generate(scan)
    snapshot = next(i for i in result if i.type == "market_snapshot")
    assert "supply" not in snapshot.data
    assert "listed_pct" not in snapshot.data
    assert "floor_price" not in snapshot.data


# ── listing scarcity ─────────────────────────────────────────────────────

def test_listing_scarcity_emitted_when_below_threshold() -> None:
    scan = {
        "collection": {"total_supply": 10000},
        "stats": {},
        "listings": [_listing(i, 0.05) for i in range(50)],  # 0.5% listed
        "nfts": [],
    }
    result = insights.generate(scan)
    scarcity = next((i for i in result if i.type == "listing_scarcity"), None)
    assert scarcity is not None
    assert scarcity.data["listed_pct"] == 0.5


def test_listing_scarcity_not_emitted_when_supply_unknown() -> None:
    scan = {"collection": {}, "stats": {}, "listings": [_listing(1, 0.05)], "nfts": []}
    result = insights.generate(scan)
    assert all(i.type != "listing_scarcity" for i in result)


def test_listing_scarcity_not_emitted_when_not_scarce() -> None:
    scan = {
        "collection": {"total_supply": 100},
        "stats": {},
        "listings": [_listing(i, 0.05) for i in range(50)],  # 50% listed
        "nfts": [],
    }
    result = insights.generate(scan)
    assert all(i.type != "listing_scarcity" for i in result)


# ── rarest listed nft ────────────────────────────────────────────────────

def test_rarest_listed_nft_picks_lowest_rank_among_listed() -> None:
    nfts = [
        _nft(1, Eyes="Normal"),  # freq 1, rarest
        _nft(2, Eyes="Common"),
        _nft(3, Eyes="Common"),
    ]
    scan = {
        "collection": {"total_supply": 3},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(2, 0.12), _listing(3, 0.11)],  # token 1 not listed
        "nfts": nfts,
    }
    result = insights.generate(scan)
    rarest = next((i for i in result if i.type == "rarest_listed_nft"), None)
    assert rarest is not None
    # token 1 is rarest overall but unlisted; among listed (2,3) they tie -> either could be picked deterministically (min by rank)
    assert rarest.data["token_id"] in (2, 3)
    assert rarest.nft_token_ids == (rarest.data["token_id"],)
    assert rarest.data["price_vs_floor_multiple"] is not None


def test_rarest_listed_nft_none_when_no_nft_data() -> None:
    scan = {"collection": {}, "stats": {}, "listings": [_listing(1, 0.1)], "nfts": []}
    result = insights.generate(scan)
    assert all(i.type != "rarest_listed_nft" for i in result)


def test_rarity_insights_skipped_when_supply_exceeds_max_scan_cap(monkeypatch) -> None:
    monkeypatch.setattr(config, "MAX_SUPPLY_FOR_RARITY", 10)
    nfts = [_nft(i, Eyes="X") for i in range(5)]
    scan = {
        "collection": {"total_supply": 999999},
        "stats": {},
        "listings": [_listing(0, 0.1)],
        "nfts": nfts,
    }
    result = insights.generate(scan)
    assert all(i.type not in ("rarest_listed_nft", "rarest_listed_trait") for i in result)


# ── rarest listed trait ──────────────────────────────────────────────────

def test_rarest_listed_trait_finds_lowest_frequency_listed_trait() -> None:
    nfts = [
        _nft(1, Hat="Crown"),
        _nft(2, Hat="Cap"),
        _nft(3, Hat="Cap"),
        _nft(4, Hat="Cap"),
    ]
    scan = {
        "collection": {"total_supply": 4},
        "stats": {},
        "listings": [_listing(1, 0.5), _listing(2, 0.1)],
        "nfts": nfts,
    }
    result = insights.generate(scan)
    trait = next((i for i in result if i.type == "rarest_listed_trait"), None)
    assert trait is not None
    assert trait.data["trait_type"] == "Hat"
    assert trait.data["value"] == "Crown"
    assert trait.data["count"] == 1
    assert trait.data["listed_count"] == 1
    assert trait.nft_token_ids == (1,)


# ── cheap listings ───────────────────────────────────────────────────────

def test_cheap_listings_picks_tightest_multiple_with_enough_matches() -> None:
    scan = {
        "collection": {},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(i, price) for i, price in enumerate([0.1, 0.1, 0.1, 5.0, 6.0])],
        "nfts": [],
    }
    result = insights.generate(scan)
    cheap = next((i for i in result if i.type == "cheap_listings"), None)
    assert cheap is not None
    assert cheap.data["matched_count"] == 3
    assert cheap.data["multiple"] == 1.1
    assert cheap.data["anchor_is_floor"] is True
    assert len(cheap.nft_token_ids) == 3


def test_cheap_listings_none_when_too_few_listings_match_any_threshold() -> None:
    scan = {
        "collection": {},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(1, 0.1), _listing(2, 5.0)],
        "nfts": [],
    }
    result = insights.generate(scan)
    assert all(i.type != "cheap_listings" for i in result)


def test_cheap_listings_falls_back_to_min_price_anchor_without_floor() -> None:
    scan = {
        "collection": {},
        "stats": {},
        "listings": [_listing(i, price) for i, price in enumerate([0.2, 0.2, 0.2])],
        "nfts": [],
    }
    result = insights.generate(scan)
    cheap = next((i for i in result if i.type == "cheap_listings"), None)
    assert cheap is not None
    assert cheap.data["anchor_is_floor"] is False


# ── ranking / best story ─────────────────────────────────────────────────

def test_generate_ranks_by_score_and_flags_top_as_best() -> None:
    scan = {
        "collection": {"total_supply": 10000},
        "stats": {"floor_price": 0.05},
        "listings": [_listing(i, 0.05) for i in range(10)],
        "nfts": [],
    }
    result = insights.generate(scan)
    assert result == sorted(result, key=lambda i: i.score, reverse=True)
    assert result[0].is_best is True
    assert all(not i.is_best for i in result[1:])


def test_generate_never_raises_on_minimal_scan() -> None:
    result = insights.generate({})
    assert len(result) == 1
    assert result[0].type == "market_snapshot"
    assert result[0].is_best is True


# ── truncated/incomplete data must not be presented as complete ──────────

def test_market_snapshot_omits_percentage_when_listings_incomplete() -> None:
    scan = {
        "collection": {"total_supply": 10000},
        "stats": {},
        "listings": [_listing(i, 0.05) for i in range(50)],
        "listings_complete": False,
        "nfts": [],
    }
    result = insights.generate(scan)
    snapshot = next(i for i in result if i.type == "market_snapshot")
    assert snapshot.data["listed"] == 50  # raw count still honest
    assert "listed_pct" not in snapshot.data
    assert "supply" not in snapshot.data


def test_listing_scarcity_not_emitted_when_listings_incomplete() -> None:
    scan = {
        "collection": {"total_supply": 10000},
        "stats": {},
        "listings": [_listing(i, 0.05) for i in range(50)],
        "listings_complete": False,
        "nfts": [],
    }
    result = insights.generate(scan)
    assert all(i.type != "listing_scarcity" for i in result)


def test_cheap_listings_not_emitted_when_listings_incomplete() -> None:
    scan = {
        "collection": {},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(i, price) for i, price in enumerate([0.1, 0.1, 0.1])],
        "listings_complete": False,
        "nfts": [],
    }
    result = insights.generate(scan)
    assert all(i.type != "cheap_listings" for i in result)


def test_rarity_insights_skipped_when_nfts_fetch_incomplete() -> None:
    nfts = [_nft(1, Eyes="Normal"), _nft(2, Eyes="Common"), _nft(3, Eyes="Common")]
    scan = {
        "collection": {"total_supply": 3},
        "stats": {},
        "listings": [_listing(2, 0.12)],
        "nfts": nfts,
        "nfts_complete": False,
    }
    result = insights.generate(scan)
    assert all(i.type not in ("rarest_listed_nft", "rarest_listed_trait") for i in result)


def test_rarity_insights_skipped_when_listings_fetch_incomplete() -> None:
    nfts = [_nft(1, Eyes="Normal"), _nft(2, Eyes="Common"), _nft(3, Eyes="Common")]
    scan = {
        "collection": {"total_supply": 3},
        "stats": {},
        "listings": [_listing(2, 0.12)],
        "listings_complete": False,
        "nfts": nfts,
    }
    result = insights.generate(scan)
    assert all(i.type not in ("rarest_listed_nft", "rarest_listed_trait") for i in result)


def test_complete_data_still_produces_full_insight_set() -> None:
    """Sanity check that the completeness gating doesn't over-trigger —
    a scan explicitly marked complete behaves exactly like the pre-existing
    (implicit-True) tests above."""
    nfts = [_nft(1, Hat="Crown"), _nft(2, Hat="Cap"), _nft(3, Hat="Cap"), _nft(4, Hat="Cap")]
    scan = {
        "collection": {"total_supply": 4},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(1, 0.5), _listing(2, 0.1), _listing(3, 0.1), _listing(4, 0.1)],
        "listings_complete": True,
        "nfts": nfts,
        "nfts_complete": True,
    }
    result = insights.generate(scan)
    types = {i.type for i in result}
    assert "rarest_listed_trait" in types
    assert "cheap_listings" in types
    snapshot = next(i for i in result if i.type == "market_snapshot")
    assert "listed_pct" in snapshot.data
