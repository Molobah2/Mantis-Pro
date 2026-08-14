"""
Trait-frequency and rarity-score/rank computation, generalized from
build_rarity_index.py's on-chain Litany scoring (lines ~110-128) to work on
any collection's {token_id, attrs} data — no RPC/on-chain coupling, no
Litany-specific assumptions. Pure functions: same input always produces the
same output, so scan.py can call this synchronously on whatever
opensea_client.get_collection_nfts returned.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RankedNft:
    token_id: int
    score: float
    rank: int  # 1 = rarest


def trait_frequencies(nfts: list[dict]) -> dict[str, dict[str, int]]:
    """trait_type -> {value: count} across the given NFTs. NFTs with no
    "attrs" key contribute nothing (never invents a trait that wasn't
    reported)."""
    freq: dict[str, dict[str, int]] = {}
    for nft in nfts:
        for trait_type, value in (nft.get("attrs") or {}).items():
            bucket = freq.setdefault(trait_type, {})
            key = str(value)
            bucket[key] = bucket.get(key, 0) + 1
    return freq


def rank_by_rarity(nfts: list[dict]) -> list[RankedNft]:
    """Classic rarity score (sum of 1/frequency across an NFT's traits),
    ranked rarest-first (rank 1 = rarest). An NFT with no traits scores 0
    and ranks last, rather than being dropped."""
    freq = trait_frequencies(nfts)
    scored = []
    for nft in nfts:
        score = 0.0
        for trait_type, value in (nft.get("attrs") or {}).items():
            count = freq.get(trait_type, {}).get(str(value), 1)
            score += 1.0 / count
        scored.append((nft["token_id"], round(score, 4)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [RankedNft(token_id=tid, score=score, rank=i + 1) for i, (tid, score) in enumerate(scored)]


def rarest_trait_with_listing(
    nfts: list[dict], listed_token_ids: set[int],
) -> dict | None:
    """Among traits that have at least one currently-listed NFT, returns the
    single rarest (trait_type, value) pair: {"trait_type", "value", "count",
    "total", "listed_token_ids"}. None if no listed NFT has any trait data,
    or the collection has no NFTs."""
    freq = trait_frequencies(nfts)
    if not freq:
        return None

    total = len(nfts)
    listed_by_trait: dict[tuple[str, str], set[int]] = {}
    for nft in nfts:
        if nft["token_id"] not in listed_token_ids:
            continue
        for trait_type, value in (nft.get("attrs") or {}).items():
            listed_by_trait.setdefault((trait_type, str(value)), set()).add(nft["token_id"])

    if not listed_by_trait:
        return None

    (rarest_type, rarest_value), token_ids = min(
        listed_by_trait.items(),
        key=lambda item: freq[item[0][0]][item[0][1]],
    )
    return {
        "trait_type": rarest_type,
        "value": rarest_value,
        "count": freq[rarest_type][rarest_value],
        "total": total,
        "listed_token_ids": sorted(token_ids),
    }
