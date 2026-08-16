import pytest

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


def test_market_snapshot_includes_a_sample_of_listed_nfts_for_the_grid() -> None:
    scan = {
        "collection": {"total_supply": 10000},
        "stats": {},
        "listings": [_listing(i, 0.05) for i in range(200)],
        "nfts": [],
    }
    result = insights.generate(scan)
    snapshot = next(i for i in result if i.type == "market_snapshot")
    assert len(snapshot.nft_token_ids) == 50  # capped sample, not all 200
    assert snapshot.hero_token_id is None


def test_market_snapshot_grid_empty_when_nothing_is_listed() -> None:
    scan = {"collection": {"total_supply": 100}, "stats": {}, "listings": [], "nfts": []}
    result = insights.generate(scan)
    snapshot = next(i for i in result if i.type == "market_snapshot")
    assert snapshot.nft_token_ids == ()


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
    assert len(scarcity.nft_token_ids) == 50  # sample of the 50 listed, all shown since under the cap


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
    # hero (the rarest) plus the other currently-listed token as backdrop
    assert rarest.hero_token_id == rarest.data["token_id"]
    assert rarest.nft_token_ids[0] == rarest.hero_token_id
    assert set(rarest.nft_token_ids) == {2, 3}
    assert rarest.data["price_vs_floor_multiple"] is not None


def test_rarest_listed_nft_none_when_no_nft_data() -> None:
    scan = {"collection": {}, "stats": {}, "listings": [_listing(1, 0.1)], "nfts": []}
    result = insights.generate(scan)
    assert all(i.type != "rarest_listed_nft" for i in result)


def test_rarest_listed_nft_shows_backdrop_of_other_listed_nfts_rarest_first() -> None:
    nfts = [
        _nft(1, Eyes="Normal"),   # freq 1 -> rarest, will be hero
        _nft(2, Eyes="Uncommon"),  # freq 1 among the rest -> next rarest
        _nft(3, Eyes="Common"),
        _nft(4, Eyes="Common"),
        _nft(5, Eyes="Common"),
    ]
    scan = {
        "collection": {"total_supply": 5},
        "stats": {},
        "listings": [_listing(i, 0.1) for i in (1, 2, 3, 4, 5)],
        "nfts": nfts,
    }
    result = insights.generate(scan)
    rarest = next(i for i in result if i.type == "rarest_listed_nft")
    assert rarest.hero_token_id == 1
    assert rarest.nft_token_ids[0] == 1
    # backdrop excludes the hero itself and is ordered rarest-first
    assert 1 not in rarest.nft_token_ids[1:]
    assert rarest.nft_token_ids[1] == 2


def test_rarest_listed_nft_backdrop_capped_at_max_grid_tokens() -> None:
    nfts = [_nft(i, Eyes=f"trait-{i}") for i in range(1, 20)]  # every trait unique -> all rank ties broken by insertion
    scan = {
        "collection": {"total_supply": 19},
        "stats": {},
        "listings": [_listing(i, 0.1) for i in range(1, 20)],
        "nfts": nfts,
    }
    result = insights.generate(scan)
    rarest = next(i for i in result if i.type == "rarest_listed_nft")
    assert len(rarest.nft_token_ids) <= 12  # hero + up to 11 backdrop


def test_other_insight_types_have_no_hero() -> None:
    scan = {
        "collection": {"total_supply": 4},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(1, 0.5), _listing(2, 0.1), _listing(3, 0.1), _listing(4, 0.1)],
        "nfts": [_nft(1, Hat="Crown"), _nft(2, Hat="Cap"), _nft(3, Hat="Cap"), _nft(4, Hat="Cap")],
    }
    result = insights.generate(scan)
    for insight in result:
        if insight.type != "rarest_listed_nft":
            assert insight.hero_token_id is None


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


def test_rarest_listed_trait_shows_every_listed_match_not_capped_at_twelve() -> None:
    nfts = [_nft(i, Hat="Common") for i in range(20)]
    scan = {
        "collection": {"total_supply": 20},
        "stats": {},
        "listings": [_listing(i, 0.1) for i in range(20)],
        "nfts": nfts,
    }
    result = insights.generate(scan)
    trait = next(i for i in result if i.type == "rarest_listed_trait")
    assert trait.data["listed_count"] == 20
    assert len(trait.nft_token_ids) == 20


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


def test_cheap_listings_shows_every_match_not_capped_at_twelve() -> None:
    """Regression: the grid used to hard-cap at 12 tiles even when the
    headline claimed a larger matched_count (e.g. 26), leaving the claim
    only partially illustrated and wasted space in the rendered card."""
    scan = {
        "collection": {},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(i, 0.1) for i in range(26)],
        "nfts": [],
    }
    result = insights.generate(scan)
    cheap = next(i for i in result if i.type == "cheap_listings")
    assert cheap.data["matched_count"] == 26
    assert len(cheap.nft_token_ids) == 26


def test_cheap_listings_caps_at_proof_grid_limit_for_very_loose_thresholds() -> None:
    scan = {
        "collection": {},
        "stats": {"floor_price": 0.1},
        "listings": [_listing(i, 0.1) for i in range(200)],
        "nfts": [],
    }
    result = insights.generate(scan)
    cheap = next(i for i in result if i.type == "cheap_listings")
    assert cheap.data["matched_count"] == 200  # headline/caption stay honest about the true count
    assert len(cheap.nft_token_ids) == 60  # grid still bounded for sanity


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


# ── period performance ────────────────────────────────────────────────

def _sale(token_id: int, price: float, symbol: str = "ETH") -> dict:
    return {"token_id": token_id, "price": price, "symbol": symbol, "timestamp": 0, "name": None, "image_url": None}


def test_period_performance_not_emitted_with_no_sales_and_no_history() -> None:
    scan = {"collection": {}, "stats": {}, "listings": [], "nfts": [],
            "sales_this_period": [], "sales_last_period": []}
    result = insights.generate(scan)
    assert all(i.type != "period_performance" for i in result)


def test_period_performance_emitted_from_sales_alone_no_snapshot_needed() -> None:
    """The whole point: this works retroactively via /events, no prior
    snapshot required."""
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(1, 0.1), _sale(2, 0.12)],
        "sales_last_period": [_sale(3, 0.08)],
    }
    result = insights.generate(scan)
    perf = next((i for i in result if i.type == "period_performance"), None)
    assert perf is not None
    assert perf.data["sales_this"] == 2
    assert perf.data["sales_last"] == 1
    assert perf.data["sales_change_pct"] == 100.0
    assert "floor_change_pct" not in perf.data
    assert "listed_change_pct" not in perf.data


def test_period_performance_not_emitted_when_sales_windows_incomplete() -> None:
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(1, 0.1)],
        "sales_this_period_complete": False,
        "sales_last_period": [_sale(2, 0.1)],
    }
    result = insights.generate(scan)
    assert all(i.type != "period_performance" for i in result)


def test_period_performance_includes_floor_and_listed_when_snapshot_available() -> None:
    scan = {
        "collection": {}, "stats": {"floor_price": 0.12}, "listings": [_listing(1, 0.12)],
        "listings_complete": True, "nfts": [],
        "sales_this_period": [], "sales_last_period": [],
        "snapshot_week_ago": {"floor_price": 0.09, "listed_count": 3},
        "days_tracked": 8.2,
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert perf.data["floor_this"] == 0.12
    assert perf.data["floor_last"] == 0.09
    assert perf.data["floor_change_pct"] == pytest.approx(33.33, abs=0.01)
    assert perf.data["listed_this"] == 1
    assert perf.data["listed_last"] == 3
    assert perf.data["listed_change_pct"] == pytest.approx(-66.67, abs=0.01)
    assert perf.data["days_tracked"] == 8.2


def test_period_performance_omits_listed_change_when_listings_incomplete() -> None:
    scan = {
        "collection": {}, "stats": {"floor_price": 0.12}, "listings": [_listing(1, 0.12)],
        "listings_complete": False, "nfts": [],
        "sales_this_period": [], "sales_last_period": [],
        "snapshot_week_ago": {"floor_price": 0.09, "listed_count": 3},
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert "listed_change_pct" not in perf.data
    assert "floor_change_pct" in perf.data  # unaffected by listings completeness


def test_period_performance_keeps_raw_listed_values_when_baseline_is_zero() -> None:
    """Regression: going from 0 listed a week ago to N listed now is a real,
    non-fabricated finding — _pct_change can't express it as a %, but the
    raw before/after values must still surface rather than being dropped
    entirely (mirrors how the sales block already handles a 0 baseline)."""
    scan = {
        "collection": {}, "stats": {}, "listings": [_listing(1, 0.1), _listing(2, 0.2)],
        "listings_complete": True, "nfts": [],
        "sales_this_period": [], "sales_last_period": [],
        "snapshot_week_ago": {"floor_price": None, "listed_count": 0},
    }
    result = insights.generate(scan)
    perf = next((i for i in result if i.type == "period_performance"), None)
    assert perf is not None
    assert perf.data["listed_this"] == 2
    assert perf.data["listed_last"] == 0
    assert "listed_change_pct" not in perf.data  # no defined % from a zero baseline


def test_period_performance_includes_avg_price_when_both_windows_have_priced_sales() -> None:
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(1, 0.2), _sale(2, 0.3)],
        "sales_last_period": [_sale(3, 0.1)],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert perf.data["avg_price_this"] == pytest.approx(0.25)
    assert perf.data["avg_price_last"] == pytest.approx(0.1)
    assert perf.data["avg_price_change_pct"] == pytest.approx(150.0)


def test_period_performance_omits_avg_price_when_one_window_has_no_priced_sales() -> None:
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(1, 0.2)],
        "sales_last_period": [],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert "avg_price_this" not in perf.data
    assert "avg_price_change_pct" not in perf.data


def test_period_performance_uses_sold_nfts_as_proof_grid() -> None:
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(5, 0.1), _sale(6, 0.2)],
        "sales_last_period": [],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert perf.nft_token_ids == (5, 6)


def test_period_performance_falls_back_to_listings_when_nothing_sold_this_period() -> None:
    scan = {
        "collection": {}, "stats": {}, "listings": [_listing(1, 0.1), _listing(2, 0.2)],
        "listings_complete": True, "nfts": [],
        "sales_this_period": [], "sales_last_period": [_sale(9, 0.1)],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert set(perf.nft_token_ids) == {1, 2}


def test_period_performance_proof_grid_capped_at_thirteen() -> None:
    """The grid is illustrative context, not a claim about a matched set —
    kept small so images stay large, unlike cheap_listings/rarest_listed_trait
    which show every match."""
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(i, 0.1) for i in range(1, 51)],
        "sales_last_period": [],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert len(perf.nft_token_ids) == 13


def test_period_performance_listings_fallback_also_capped_at_thirteen() -> None:
    scan = {
        "collection": {}, "stats": {}, "listings": [_listing(i, 0.1) for i in range(1, 51)],
        "listings_complete": True, "nfts": [],
        "sales_this_period": [], "sales_last_period": [_sale(9, 0.1)],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert len(perf.nft_token_ids) == 13


def test_period_performance_zero_baseline_omits_pct_but_keeps_raw_values() -> None:
    """Going from 0 sales last period to N this period is a real story
    ("first sales in a week") but has no defined percentage change."""
    scan = {
        "collection": {}, "stats": {}, "listings": [], "nfts": [],
        "sales_this_period": [_sale(1, 0.1)],
        "sales_last_period": [],
    }
    result = insights.generate(scan)
    perf = next(i for i in result if i.type == "period_performance")
    assert perf.data["sales_this"] == 1
    assert perf.data["sales_last"] == 0
    assert "sales_change_pct" not in perf.data
