"""
Pillow-based PNG compositor: turns an Insight (see insights.py) + already-
fetched NFT thumbnail images into a downloadable social card.

Split into I/O (fetch_nft_image) and pure composition (render) so render()
can be unit-tested with synthetic images and no network, matching this
package's other modules' testing style.
"""
import io
import math

import requests
from PIL import Image, ImageDraw, ImageFont

from opensea_automint.drops import _TRUSTED_IMAGE_URL_PREFIXES

from . import captions, config
from .insights import Insight

FORMATS = {
    "16:9": (1600, 900),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
}
DEFAULT_FORMAT = "16:9"

_IMAGE_FETCH_TIMEOUT_S = 8
_MAX_IMAGE_BYTES = 8 * 1024 * 1024  # defensive cap against a huge/streamed response

_BG_TOP = (13, 14, 18)
_BG_BOTTOM = (22, 18, 32)
_TEXT_PRIMARY = (245, 245, 250)
_TEXT_MUTED = (150, 150, 165)
_ACCENT = (124, 140, 255)
_PANEL = (255, 255, 255, 15)

# Pure flat grid, no rounded corners — a plain tiled photo-grid, not a
# "designed" card element.
_GRID_RADIUS = 0

# n -> (cols, rows), matching the product spec's adaptive grid (4->2x2,
# 6->3x2, 9->3x3, 12->4x3); other counts fall back to a near-square grid.
_GRID_TABLE = {
    1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2),
    7: (3, 3), 8: (3, 3), 9: (3, 3), 10: (4, 3), 11: (4, 3), 12: (4, 3),
}


def grid_layout(n: int) -> tuple[int, int]:
    """Returns (cols, rows) for n images. (0, 0) for n <= 0."""
    if n <= 0:
        return (0, 0)
    if n in _GRID_TABLE:
        return _GRID_TABLE[n]
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return (cols, rows)


# "Bento" layout for a single-subject insight (e.g. rarest_listed_nft): one
# hero tile spans a HERO_SPAN x HERO_SPAN block of a COLS-wide grid, and the
# backdrop NFTs fill the remaining cells at 1x1 in reading order.
_BENTO_COLS = 4
_BENTO_HERO_SPAN = 2


def bento_layout(other_count: int) -> tuple[int, int, list[tuple[int, int]]]:
    """Returns (cols, rows, other_cell_positions) — other_cell_positions is
    a list of (row, col) in reading order, skipping the hero's block at the
    top-left. Always at least HERO_SPAN rows tall, even with zero others,
    so the hero block always fits."""
    cols = _BENTO_COLS
    hero_cells = _BENTO_HERO_SPAN * _BENTO_HERO_SPAN
    rows = max(_BENTO_HERO_SPAN, math.ceil((hero_cells + other_count) / cols))
    occupied = {(r, c) for r in range(_BENTO_HERO_SPAN) for c in range(_BENTO_HERO_SPAN)}
    positions = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in occupied]
    return cols, rows, positions[:other_count]


def fetch_nft_image(url: str | None) -> Image.Image | None:
    """Fetches and decodes one NFT thumbnail. Returns None on any failure —
    untrusted host, network error, non-200, oversized body, undecodable
    image — never raises; the renderer falls back to a placeholder tile.

    Re-checks the trusted-host allowlist here too (defense in depth): this
    function does the actual outbound fetch, so it shouldn't rely solely on
    opensea_client having validated the URL earlier."""
    if not url or not url.startswith(_TRUSTED_IMAGE_URL_PREFIXES):
        return None
    try:
        # allow_redirects=False: the prefix check above only validated the
        # original URL. If the allowlisted host ever redirected (misconfig,
        # open-redirect), following it would fetch from wherever the
        # redirect points, entirely outside the allowlist.
        r = requests.get(url, timeout=_IMAGE_FETCH_TIMEOUT_S, stream=True, allow_redirects=False)
        if r.status_code != 200:
            return None
        content_length = r.headers.get("content-length")
        if content_length and int(content_length) > _MAX_IMAGE_BYTES:
            return None
        raw = r.raw.read(_MAX_IMAGE_BYTES + 1, decode_content=True)
        if len(raw) > _MAX_IMAGE_BYTES:
            return None
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    except (requests.exceptions.RequestException, OSError, ValueError, Image.DecompressionBombError):
        # DecompressionBombError isn't an OSError/ValueError subclass — a
        # small file that decodes to an enormous pixel grid (e.g. a
        # maliciously crafted PNG) would otherwise raise here uncaught,
        # 500ing the route instead of degrading to a placeholder tile like
        # every other failure mode above.
        return None


def _font(path: str, size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    try:
        font.set_variation_by_name(weight)
    except Exception:
        pass
    return font


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _rounded_thumbnail(img: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    """Cover-fits img to completely fill size, no padding/letterbox bars —
    matches how OpenSea's own grid thumbnails fill their cells. Cells this
    is called with are always square (see _square_cell_size), and most NFT
    art is itself square-ish, so in practice little to nothing gets cropped;
    this only crops the excess on collections whose art genuinely isn't
    square."""
    img = img.convert("RGBA")
    src_w, src_h = img.size
    target_w, target_h = size
    scale = max(target_w / src_w, target_h / src_h)
    resized = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    cropped = resized.crop((left, top, left + target_w, top + target_h))

    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, target_w, target_h), radius=radius, fill=255)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(cropped, (0, 0), mask)
    return out


def _placeholder_tile(size: tuple[int, int], radius: int) -> Image.Image:
    tile = Image.new("RGBA", size, (255, 255, 255, 18))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *size), radius=radius, fill=255)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    return out


def _background(size: tuple[int, int]) -> Image.Image:
    w, h = size
    bg = Image.new("RGB", (1, h), color=0)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = round(_BG_TOP[0] + (_BG_BOTTOM[0] - _BG_TOP[0]) * t)
        g = round(_BG_TOP[1] + (_BG_BOTTOM[1] - _BG_TOP[1]) * t)
        b = round(_BG_TOP[2] + (_BG_BOTTOM[2] - _BG_TOP[2]) * t)
        bg.putpixel((0, y), (r, g, b))
    return bg.resize((w, h)).convert("RGBA")


def render(
    insight: Insight,
    collection_name: str,
    nft_images: dict[int, Image.Image | None],
    format_key: str = DEFAULT_FORMAT,
) -> bytes:
    """Composes the card and returns PNG bytes. format_key defaults to
    16:9 if unrecognized, rather than raising — an unsupported format
    string from a client shouldn't 500 the request."""
    size = FORMATS.get(format_key, FORMATS[DEFAULT_FORMAT])
    w, h = size
    margin = round(w * 0.06)

    canvas = _background(size)
    draw = ImageDraw.Draw(canvas)

    headline_text = captions.headline(insight, collection_name)
    stat_lines = _stat_lines(insight)

    headline_size = round(w * 0.045)
    stat_size = round(w * 0.02)
    headline_font = _font(config.FONT_SANS, headline_size, "Bold")
    stat_font = _font(config.FONT_MONO, stat_size, "Medium")

    cursor_y = margin
    for line in _wrap_text(draw, headline_text, headline_font, w - 2 * margin):
        draw.text((margin, cursor_y), line, font=headline_font, fill=_TEXT_PRIMARY)
        cursor_y += round(headline_size * 1.25)

    cursor_y += round(headline_size * 0.3)
    for line in stat_lines:
        draw.text((margin, cursor_y), line, font=stat_font, fill=_ACCENT)
        cursor_y += round(stat_size * 1.6)

    # Pure grid, edge-to-edge (no inset, no rounded corners) — only the
    # headline/stat text above gets the card's margin treatment.
    grid_top = cursor_y + round(headline_size * 0.4)
    if insight.hero_token_id is not None and insight.hero_token_id in insight.nft_token_ids:
        backdrop_ids = tuple(tid for tid in insight.nft_token_ids if tid != insight.hero_token_id)
        _draw_bento_grid(canvas, insight.hero_token_id, backdrop_ids, nft_images, 0, grid_top, w, h)
    else:
        _draw_grid(canvas, insight.nft_token_ids, nft_images, 0, grid_top, w, h)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _stat_lines(insight: Insight) -> list[str]:
    d = insight.data
    if insight.type == "market_snapshot":
        parts = [f"{d['listed']} listed"]
        if "supply" in d:
            parts.append(f"of {d['supply']}")
        if "floor_price" in d:
            parts.append(f"floor {d['floor_price']} {d.get('floor_price_symbol', 'ETH')}")
        return [" · ".join(parts)]
    if insight.type == "listing_scarcity":
        return [f"{d['listed']} / {d['supply']} listed  ·  {d['listed_pct']}%"]
    if insight.type == "rarest_listed_nft":
        return [f"rank {d['rank']} / {d['total']}  ·  {d['price']} {d['currency']}"]
    if insight.type == "rarest_listed_trait":
        return [f"{d['count']} / {d['total']} have this trait  ·  {d['listed_count']} listed"]
    if insight.type == "cheap_listings":
        return [f"{d['matched_count']} listed under {d['threshold_price']} {d['currency']}"]
    return []


def _thin_gap(span: int) -> int:
    """A hairline separator between grid tiles — present enough to read as
    intentional spacing, not so wide it reads as a gap."""
    return max(2, round(span * 0.003))


def _square_cell_size(available_w: int, available_h: int, cols: int, rows: int, gap: int) -> int:
    """Cells are always forced square (not just height-capped) — a
    non-square cell is what forced cover-fit to crop off large chunks of
    tall/wide NFT art. Square cells + typically-square NFT art means
    cover-fit rarely needs to crop anything at all."""
    w_limited = (available_w - gap * (cols - 1)) // cols
    h_limited = (available_h - gap * (rows - 1)) // rows
    return max(1, min(w_limited, h_limited))


def _draw_grid(
    canvas: Image.Image,
    token_ids: tuple[int, ...],
    nft_images: dict[int, Image.Image | None],
    left: int, top: int, right: int, bottom: int,
) -> None:
    n = len(token_ids)
    cols, rows = grid_layout(n)
    if cols == 0:
        return

    gap = _thin_gap(right - left)
    cell = _square_cell_size(right - left, bottom - top, cols, rows, gap)

    grid_w = cols * cell + gap * (cols - 1)
    left += (right - left - grid_w) // 2  # center horizontally in any leftover space

    for i, token_id in enumerate(token_ids):
        col, row = i % cols, i // cols
        x = left + col * (cell + gap)
        y = top + row * (cell + gap)
        img = nft_images.get(token_id)
        tile = _rounded_thumbnail(img, (cell, cell), _GRID_RADIUS) if img else _placeholder_tile((cell, cell), _GRID_RADIUS)
        canvas.alpha_composite(tile, (x, y))


def _draw_bento_grid(
    canvas: Image.Image,
    hero_token_id: int,
    backdrop_token_ids: tuple[int, ...],
    nft_images: dict[int, Image.Image | None],
    left: int, top: int, right: int, bottom: int,
) -> None:
    cols, rows, other_positions = bento_layout(len(backdrop_token_ids))

    gap = _thin_gap(right - left)
    cell = _square_cell_size(right - left, bottom - top, cols, rows, gap)

    grid_w = cols * cell + gap * (cols - 1)
    left += (right - left - grid_w) // 2  # center horizontally in any leftover space

    hero_size = cell * _BENTO_HERO_SPAN + gap * (_BENTO_HERO_SPAN - 1)
    hero_img = nft_images.get(hero_token_id)
    hero_tile = (
        _rounded_thumbnail(hero_img, (hero_size, hero_size), _GRID_RADIUS) if hero_img
        else _placeholder_tile((hero_size, hero_size), _GRID_RADIUS)
    )
    canvas.alpha_composite(hero_tile, (left, top))

    for (row, col), token_id in zip(other_positions, backdrop_token_ids):
        x = left + col * (cell + gap)
        y = top + row * (cell + gap)
        img = nft_images.get(token_id)
        tile = (
            _rounded_thumbnail(img, (cell, cell), _GRID_RADIUS) if img
            else _placeholder_tile((cell, cell), _GRID_RADIUS)
        )
        canvas.alpha_composite(tile, (x, y))
