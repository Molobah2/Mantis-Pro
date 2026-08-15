import io

import pytest
from PIL import Image

from nft_insights import card_renderer
from nft_insights.insights import Insight


def _insight(type_: str, data: dict, nft_token_ids=(), hero_token_id=None) -> Insight:
    return Insight(
        id=type_, type=type_, data=data, nft_token_ids=nft_token_ids, score=0.5, hero_token_id=hero_token_id,
    )


def _tiny_png_bytes() -> bytes:
    img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── justified_row_counts ─────────────────────────────────────────────────

def test_justified_row_counts_empty_for_zero_or_negative() -> None:
    assert card_renderer.justified_row_counts(0) == []
    assert card_renderer.justified_row_counts(-1) == []


@pytest.mark.parametrize("n,expected", [
    (1, [1]), (2, [2]), (4, [2, 2]), (6, [3, 3]), (9, [3, 3, 3]),
])
def test_justified_row_counts_small_n(n, expected) -> None:
    assert card_renderer.justified_row_counts(n) == expected


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 20, 25, 26, 28, 50, 60])
def test_justified_row_counts_always_sums_to_n_with_no_leftover_cells(n) -> None:
    """This is the whole point: every row is exactly full, so summing the
    per-row counts must land exactly on n — never more (empty trailing
    cells) or less (dropped items)."""
    counts = card_renderer.justified_row_counts(n)
    assert sum(counts) == n
    assert all(c > 0 for c in counts)


@pytest.mark.parametrize("n", [13, 20, 25, 26, 28, 50, 60])
def test_justified_row_counts_never_exceeds_max_rows(n) -> None:
    counts = card_renderer.justified_row_counts(n)
    assert len(counts) <= card_renderer._MAX_GRID_ROWS


def test_justified_row_counts_front_loads_remainder() -> None:
    # 28 over 3 rows: base 9, remainder 1 -> first row gets the extra item.
    assert card_renderer.justified_row_counts(28) == [10, 9, 9]


# ── bento_layout ──────────────────────────────────────────────────────────

def test_bento_layout_hero_block_never_overlaps_others() -> None:
    for other_count in [0, 1, 3, 5, 8, 11]:
        cols, rows, positions = card_renderer.bento_layout(other_count)
        hero_cells = {(r, c) for r in range(card_renderer._BENTO_HERO_SPAN) for c in range(card_renderer._BENTO_HERO_SPAN)}
        assert not (set(positions) & hero_cells)
        assert len(positions) == other_count
        assert rows >= card_renderer._BENTO_HERO_SPAN


def test_bento_layout_zero_others_still_fits_hero() -> None:
    cols, rows, positions = card_renderer.bento_layout(0)
    assert positions == []
    assert rows >= card_renderer._BENTO_HERO_SPAN
    assert cols >= card_renderer._BENTO_HERO_SPAN


def test_bento_layout_positions_are_unique_and_within_grid_bounds() -> None:
    cols, rows, positions = card_renderer.bento_layout(11)
    assert len(set(positions)) == len(positions)
    for r, c in positions:
        assert 0 <= r < rows
        assert 0 <= c < cols


def test_bento_layout_never_exceeds_max_rows() -> None:
    cols, rows, positions = card_renderer.bento_layout(11)  # max backdrop size
    assert rows <= card_renderer._MAX_GRID_ROWS
    assert len(positions) == 11


# ── render with a hero (bento layout) ────────────────────────────────────

def test_render_with_hero_produces_valid_png() -> None:
    hero_img = Image.new("RGBA", (300, 300), (200, 50, 50, 255))
    insight = _insight(
        "rarest_listed_nft",
        {"token_id": 1, "rank": 1, "total": 100, "price": 1.0, "currency": "ETH"},
        nft_token_ids=(1, 2, 3, 4),
        hero_token_id=1,
    )
    png_bytes = card_renderer.render(insight, "Boonies", {1: hero_img}, format_key="16:9")
    img = Image.open(io.BytesIO(png_bytes))
    assert img.format == "PNG"
    assert img.size == (1600, 900)


def test_render_with_hero_and_no_backdrop_still_succeeds() -> None:
    insight = _insight(
        "rarest_listed_nft",
        {"token_id": 1, "rank": 1, "total": 100, "price": 1.0, "currency": "ETH"},
        nft_token_ids=(1,),
        hero_token_id=1,
    )
    png_bytes = card_renderer.render(insight, "Boonies", {})
    assert Image.open(io.BytesIO(png_bytes)).format == "PNG"


def test_render_hero_not_in_token_ids_falls_back_to_uniform_grid() -> None:
    """Defensive: if hero_token_id somehow isn't among nft_token_ids, don't
    crash — just render the uniform grid instead."""
    insight = _insight(
        "rarest_listed_nft",
        {"token_id": 1, "rank": 1, "total": 100, "price": 1.0, "currency": "ETH"},
        nft_token_ids=(2, 3),
        hero_token_id=999,
    )
    png_bytes = card_renderer.render(insight, "Boonies", {})
    assert Image.open(io.BytesIO(png_bytes)).format == "PNG"


# ── render (pure composition, no network) ────────────────────────────────

@pytest.mark.parametrize("format_key", ["16:9", "4:5", "1:1"])
def test_render_produces_png_with_correct_dimensions(format_key) -> None:
    insight = _insight("market_snapshot", {"listed": 12, "supply": 10000, "listed_pct": 0.12})
    png_bytes = card_renderer.render(insight, "Boonies", {}, format_key=format_key)
    img = Image.open(io.BytesIO(png_bytes))
    assert img.format == "PNG"
    assert img.size == card_renderer.FORMATS[format_key]


def test_render_unknown_format_falls_back_to_default() -> None:
    insight = _insight("market_snapshot", {"listed": 1})
    png_bytes = card_renderer.render(insight, "Boonies", {}, format_key="not-a-real-format")
    img = Image.open(io.BytesIO(png_bytes))
    assert img.size == card_renderer.FORMATS[card_renderer.DEFAULT_FORMAT]


def test_render_with_real_images_and_missing_images_both_succeed() -> None:
    real_img = Image.new("RGBA", (200, 200), (10, 200, 100, 255))
    insight = _insight(
        "cheap_listings",
        {"threshold_price": 0.1, "multiple": 1.1, "currency": "ETH", "matched_count": 2, "total_listed": 5, "anchor_is_floor": True},
        nft_token_ids=(1, 2),
    )
    png_bytes = card_renderer.render(insight, "Boonies", {1: real_img, 2: None}, format_key="1:1")
    img = Image.open(io.BytesIO(png_bytes))
    assert img.size == (1080, 1080)


def test_render_never_raises_for_every_insight_type() -> None:
    cases = [
        _insight("market_snapshot", {"listed": 5}),
        _insight("listing_scarcity", {"listed": 5, "supply": 100, "listed_pct": 5.0}),
        _insight("rarest_listed_nft", {"token_id": 1, "rank": 1, "total": 10, "price": 1.0, "currency": "ETH"}, (1,)),
        _insight("rarest_listed_trait", {
            "trait_type": "Hat", "value": "Crown", "count": 1, "total": 10,
            "listed_count": 1, "cheapest_price": 1.0, "currency": "ETH",
        }, (1,)),
        _insight("cheap_listings", {
            "threshold_price": 0.1, "multiple": 1.1, "currency": "ETH",
            "matched_count": 3, "total_listed": 10, "anchor_is_floor": True,
        }, (1, 2, 3)),
    ]
    for insight in cases:
        png_bytes = card_renderer.render(insight, "Boonies", {})
        assert Image.open(io.BytesIO(png_bytes)).format == "PNG"


# ── fetch_nft_image ───────────────────────────────────────────────────────

def test_fetch_nft_image_rejects_untrusted_host() -> None:
    assert card_renderer.fetch_nft_image("https://evil.example.com/image.png") is None


def test_fetch_nft_image_rejects_none_url() -> None:
    assert card_renderer.fetch_nft_image(None) is None


class _FakeRaw:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, amt, decode_content=True):
        return self._data


class _FakeStreamResponse:
    def __init__(self, status_code: int, data: bytes, content_length: str | None = None) -> None:
        self.status_code = status_code
        self.raw = _FakeRaw(data)
        self.headers = {"content-length": content_length} if content_length else {}


def test_fetch_nft_image_returns_image_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _tiny_png_bytes()
    monkeypatch.setattr(
        card_renderer.requests, "get",
        lambda *a, **k: _FakeStreamResponse(200, data, content_length=str(len(data))),
    )
    result = card_renderer.fetch_nft_image("https://i2c.seadn.io/eth/abc/1.png")
    assert result is not None
    assert result.size == (32, 32)


def test_fetch_nft_image_none_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(card_renderer.requests, "get", lambda *a, **k: _FakeStreamResponse(404, b""))
    assert card_renderer.fetch_nft_image("https://i2c.seadn.io/eth/abc/1.png") is None


def test_fetch_nft_image_none_when_content_length_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = str(card_renderer._MAX_IMAGE_BYTES + 1)
    monkeypatch.setattr(
        card_renderer.requests, "get",
        lambda *a, **k: _FakeStreamResponse(200, b"x", content_length=huge),
    )
    assert card_renderer.fetch_nft_image("https://i2c.seadn.io/eth/abc/1.png") is None


def test_fetch_nft_image_none_on_undecodable_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        card_renderer.requests, "get",
        lambda *a, **k: _FakeStreamResponse(200, b"not an image", content_length="12"),
    )
    assert card_renderer.fetch_nft_image("https://i2c.seadn.io/eth/abc/1.png") is None


def test_fetch_nft_image_none_on_decompression_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    """A small file that decodes to an enormous pixel grid raises
    Image.DecompressionBombError, which is NOT an OSError/ValueError
    subclass — must be caught explicitly or it 500s the route instead of
    degrading to a placeholder like every other failure mode here."""
    data = _tiny_png_bytes()
    monkeypatch.setattr(
        card_renderer.requests, "get",
        lambda *a, **k: _FakeStreamResponse(200, data, content_length=str(len(data))),
    )

    def raise_bomb(*a, **k):
        raise Image.DecompressionBombError("image too large")

    monkeypatch.setattr(card_renderer.Image, "open", raise_bomb)
    assert card_renderer.fetch_nft_image("https://i2c.seadn.io/eth/abc/1.png") is None
