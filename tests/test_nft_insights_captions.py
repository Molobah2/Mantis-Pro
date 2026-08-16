import pytest

from nft_insights import captions
from nft_insights.insights import Insight


def _insight(type_: str, data: dict, nft_token_ids=()) -> Insight:
    return Insight(id=type_, type=type_, data=data, nft_token_ids=nft_token_ids, score=0.5)


def test_market_snapshot_headline_uses_listed_pct_when_available() -> None:
    insight = _insight("market_snapshot", {"listed": 151, "supply": 10000, "listed_pct": 1.51})
    assert "1.51%" in captions.headline(insight, "Boonies")


def test_market_snapshot_headline_falls_back_to_listed_count() -> None:
    insight = _insight("market_snapshot", {"listed": 12})
    text = captions.headline(insight, "Boonies")
    assert "Boonies" in text
    assert "12" in text


def test_listing_scarcity_headline_and_captions_reference_percent() -> None:
    insight = _insight("listing_scarcity", {"listed": 4, "supply": 10000, "listed_pct": 0.04})
    headline = captions.headline(insight, "Boonies")
    caps = captions.caption_options(insight, "Boonies")
    assert "0.04%" in headline
    assert len(caps) == 3
    assert all("Boonies" in c or "4" in c for c in caps)


def test_rarest_listed_nft_captions_include_token_id_and_price() -> None:
    insight = _insight("rarest_listed_nft", {
        "token_id": 42, "rank": 3, "total": 10000, "price": 1.5, "currency": "ETH",
        "floor_price": 0.5, "price_vs_floor_multiple": 3.0,
    })
    caps = captions.caption_options(insight, "Boonies")
    assert any("#42" in c for c in caps)
    assert any("1.5 ETH" in c for c in caps)


def test_rarest_listed_trait_captions_include_count_and_price() -> None:
    insight = _insight("rarest_listed_trait", {
        "trait_type": "Hat", "value": "Crown", "count": 4, "total": 10000,
        "listed_count": 1, "cheapest_price": 0.31, "currency": "ETH",
    })
    headline = captions.headline(insight, "Boonies")
    caps = captions.caption_options(insight, "Boonies")
    assert "4" in headline and "Crown" in headline
    assert any("0.31 ETH" in c for c in caps)


def test_cheap_listings_captions_include_threshold_and_count() -> None:
    insight = _insight("cheap_listings", {
        "threshold_price": 0.055, "multiple": 1.1, "currency": "ETH",
        "matched_count": 5, "total_listed": 20, "anchor_is_floor": True,
    })
    headline = captions.headline(insight, "Boonies")
    caps = captions.caption_options(insight, "Boonies")
    assert "Boonies" in headline
    assert any("5" in c for c in caps)


def test_unknown_insight_type_never_raises() -> None:
    insight = _insight("something_new", {})
    assert captions.headline(insight, "Boonies") == "Boonies"
    assert captions.caption_options(insight, "Boonies") == []


@pytest.mark.parametrize("insight_type,data", [
    ("market_snapshot", {"listed": 5}),
    ("listing_scarcity", {"listed": 5, "supply": 100, "listed_pct": 5.0}),
    ("rarest_listed_nft", {"token_id": 1, "rank": 1, "total": 10, "price": 1.0, "currency": "ETH"}),
    ("rarest_listed_trait", {
        "trait_type": "Hat", "value": "Crown", "count": 1, "total": 10,
        "listed_count": 1, "cheapest_price": 1.0, "currency": "ETH",
    }),
    ("cheap_listings", {
        "threshold_price": 0.1, "multiple": 1.1, "currency": "ETH",
        "matched_count": 3, "total_listed": 10, "anchor_is_floor": True,
    }),
    ("period_performance", {"sales_this": 5, "sales_last": 3, "sales_change_pct": 66.67, "days_tracked": 30}),
])
def test_every_insight_type_produces_exactly_three_captions(insight_type, data) -> None:
    insight = _insight(insight_type, data)
    assert len(captions.caption_options(insight, "Boonies")) == 3


# ── period performance ────────────────────────────────────────────────

def test_period_performance_headline_prioritizes_floor_over_other_metrics() -> None:
    insight = _insight("period_performance", {
        "floor_change_pct": 31.0, "volume_change_pct": 84.0, "sales_change_pct": 57.0,
    })
    headline = captions.headline(insight, "GOD PULL")
    assert "floor" in headline
    assert "up 31.0%" in headline


def test_period_performance_headline_negative_change_says_down() -> None:
    insight = _insight("period_performance", {"floor_change_pct": -12.5})
    headline = captions.headline(insight, "Boonies")
    assert "down 12.5%" in headline


def test_period_performance_headline_falls_back_to_sales_count_when_no_pct_available() -> None:
    insight = _insight("period_performance", {"sales_this": 4, "sales_last": 0})
    headline = captions.headline(insight, "Boonies")
    assert "4 sales this week" in headline


def test_period_performance_captions_include_before_after_values() -> None:
    insight = _insight("period_performance", {
        "sales_this": 880, "sales_last": 620, "sales_change_pct": 41.94,
        "volume_this": 21.88, "volume_last": 15.2, "volume_change_pct": 44.0,
        "days_tracked": 45,
    })
    caps = captions.caption_options(insight, "GOD PULL")
    assert any("620" in c and "880" in c for c in caps)
    assert any("tracked history" in c for c in caps)


def test_period_performance_headline_and_captions_surface_avg_price() -> None:
    insight = _insight("period_performance", {
        "avg_price_this": 0.25, "avg_price_last": 0.1, "avg_price_change_pct": 150.0,
    })
    headline = captions.headline(insight, "GOD PULL")
    assert "average sale price" in headline
    caps = captions.caption_options(insight, "GOD PULL")
    assert any("Avg sale" in c for c in caps)


def test_period_performance_omits_confidence_note_when_days_tracked_below_one() -> None:
    insight = _insight("period_performance", {"sales_this": 2, "sales_last": 1, "days_tracked": 0.2})
    caps = captions.caption_options(insight, "Boonies")
    assert not any("tracked history" in c for c in caps)
