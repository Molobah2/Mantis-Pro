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
])
def test_every_insight_type_produces_exactly_three_captions(insight_type, data) -> None:
    insight = _insight(insight_type, data)
    assert len(captions.caption_options(insight, "Boonies")) == 3
