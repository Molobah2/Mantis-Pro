from nft_insights import rarity

_NFTS = [
    {"token_id": 1, "attrs": {"Background": "Blue", "Eyes": "Normal"}},
    {"token_id": 2, "attrs": {"Background": "Blue", "Eyes": "Laser"}},
    {"token_id": 3, "attrs": {"Background": "Gold", "Eyes": "Laser"}},
    {"token_id": 4, "attrs": {"Background": "Gold", "Eyes": "Laser"}},
]


def test_trait_frequencies_counts_each_value() -> None:
    freq = rarity.trait_frequencies(_NFTS)
    assert freq == {
        "Background": {"Blue": 2, "Gold": 2},
        "Eyes": {"Normal": 1, "Laser": 3},
    }


def test_trait_frequencies_ignores_nfts_without_attrs() -> None:
    freq = rarity.trait_frequencies([{"token_id": 9}])
    assert freq == {}


def test_rank_by_rarity_rarest_first() -> None:
    ranked = rarity.rank_by_rarity(_NFTS)
    # token 1 has the only "Normal" eyes (freq 1) -> rarest
    assert ranked[0].token_id == 1
    assert ranked[0].rank == 1
    # all ranks are unique and sequential
    assert [r.rank for r in ranked] == [1, 2, 3, 4]


def test_rank_by_rarity_score_matches_sum_of_inverse_frequencies() -> None:
    ranked = {r.token_id: r for r in rarity.rank_by_rarity(_NFTS)}
    # token 1: 1/2 (Background=Blue) + 1/1 (Eyes=Normal) = 1.5
    assert ranked[1].score == 1.5
    # token 3: 1/2 (Background=Gold) + 1/3 (Eyes=Laser) = 0.8333
    assert ranked[3].score == round(0.5 + 1 / 3, 4)


def test_rank_by_rarity_no_traits_scores_zero_and_ranks_last() -> None:
    nfts = _NFTS + [{"token_id": 5, "attrs": {}}]
    ranked = rarity.rank_by_rarity(nfts)
    assert ranked[-1].token_id == 5
    assert ranked[-1].score == 0.0


def test_rarest_trait_with_listing_picks_lowest_frequency_among_listed() -> None:
    # "Normal" eyes (freq 1) only exists on token 1, which is listed.
    result = rarity.rarest_trait_with_listing(_NFTS, listed_token_ids={1, 3})
    assert result["trait_type"] == "Eyes"
    assert result["value"] == "Normal"
    assert result["count"] == 1
    assert result["total"] == 4
    assert result["listed_token_ids"] == [1]


def test_rarest_trait_with_listing_ignores_traits_on_unlisted_nfts() -> None:
    # Only token 3 and 4 have "Gold"/"Laser" combo listed; token 2 (Blue/Laser) not listed.
    result = rarity.rarest_trait_with_listing(_NFTS, listed_token_ids={3, 4})
    # Among listed (3,4): Background=Gold (freq 2), Eyes=Laser (freq 3) -> Gold is rarer
    assert result["trait_type"] == "Background"
    assert result["value"] == "Gold"
    assert sorted(result["listed_token_ids"]) == [3, 4]


def test_rarest_trait_with_listing_none_when_no_listed_nfts_have_traits() -> None:
    result = rarity.rarest_trait_with_listing(_NFTS, listed_token_ids={999})
    assert result is None


def test_rarest_trait_with_listing_none_when_collection_empty() -> None:
    result = rarity.rarest_trait_with_listing([], listed_token_ids={1})
    assert result is None
