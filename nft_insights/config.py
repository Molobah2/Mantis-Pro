import os

# DATA_DIR: mount a Railway Volume here (e.g. /data) so the snapshot DB
# survives redeploys — same convention opensea_automint/config.py uses.
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.dirname(__file__)))

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

SALES_EVENT_PAGE_SIZE = 100
MAX_SALES_EVENT_PAGES = 30  # up to 3,000 sales per requested date window

# Weekly period-performance comparison windows.
PERIOD_PERFORMANCE_WINDOW_S = 7 * 86400
SNAPSHOT_STALENESS_TOLERANCE_S = 36 * 3600  # ~1.5x the 6h snapshot cadence

# Background snapshot job cadence (see agent.py's APScheduler wiring).
SNAPSHOT_INTERVAL_HOURS = 6

# Caps how many tracked collections one snapshot-job run processes — each
# costs up to ~32 outbound OpenSea calls (collection + stats + up to
# MAX_LISTINGS_PAGES listing pages), so without a cap this job's cost grows
# unboundedly as tracked_collections grows over the product's lifetime.
# history.get_stalest_tracked_collections prioritizes whichever slugs have
# gone longest without a snapshot, so this still rotates fairly through a
# tracked set larger than the cap across successive runs.
MAX_SNAPSHOT_COLLECTIONS_PER_TICK = 100

FONTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
FONT_SANS = os.path.join(FONTS_DIR, "Inter-Variable.ttf")
FONT_MONO = os.path.join(FONTS_DIR, "JetBrainsMono-Variable.ttf")


def get_api_key() -> str:
    return os.getenv("OPENSEA_API_KEY", "").strip()
