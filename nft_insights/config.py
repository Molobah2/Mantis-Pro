import os

OPENSEA_API_BASE = "https://api.opensea.io/api/v2"
API_REQUEST_TIMEOUT_S = 15

# Bounds how much of a collection a single scan will pull, so a request
# can't turn into an unbounded crawl. 100/page (OpenSea's max page size).
LISTINGS_PAGE_SIZE = 100
MAX_LISTINGS_PAGES = 30  # up to 3,000 active listings

NFTS_PAGE_SIZE = 100
MAX_NFT_PAGES = 100  # up to 10,000 NFTs — covers the standard 10k PFP-collection size

# Collections larger than this are skipped for trait-rarity/image data
# entirely rather than computed from a partial sample — a sampled rarity
# rank would be presented as fact when it isn't one (see insights.py's
# no-fabrication rule); a card for such a collection renders with
# placeholder tiles instead of the wrong images.
MAX_SUPPLY_FOR_RARITY = NFTS_PAGE_SIZE * MAX_NFT_PAGES

SCAN_CACHE_TTL_S = 20 * 60
CARD_CACHE_TTL_S = 10 * 60

FONTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
FONT_SANS = os.path.join(FONTS_DIR, "Inter-Variable.ttf")
FONT_MONO = os.path.join(FONTS_DIR, "JetBrainsMono-Variable.ttf")


def get_api_key() -> str:
    return os.getenv("OPENSEA_API_KEY", "").strip()
