"""
OpenSea drop discovery: scrapes https://opensea.io/drops for drop cards,
classifies each card's public mint status from its visible text, and caches
the result (mirrors portal_upvote/catalog.py's fetch + TTL-cache pattern).

v1 only classifies PUBLIC mint status — per-wallet Merkle allowlist proofs
are not confirmed reachable client-side (see RESEARCH_NOTES.md), so that is
deferred to a future step.

Note on shapes: a freshly-scraped drop dict (from parse_drop_card) has
top-level "status"/"status_detail" keys. A persisted DB row (from
store.get_tracked_drops) has those same two values packed as JSON inside a
"stage_data" string column instead — the two shapes are NOT interchangeable.
eligibility.is_publicly_mintable expects the persisted (stage_data) shape.
"""
import json
import logging
import re
import threading
import time

from . import store

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "drops": []}
_CACHE_TTL = 900  # 15 minutes — drops change status more often than portal apps

_DROPS_URL = "https://opensea.io/drops"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_PAGE_LOAD_TIMEOUT_MS = 30_000
_POST_LOAD_SETTLE_MS = 3_000
_CARD_TEXT_TIMEOUT_MS = 2_000
_MAX_DROP_CARDS = 200          # defensive cap — opensea.io/drops is external content we don't control
_MAX_DETAIL_LENGTH = 200       # truncate before this reaches an unbounded TEXT column
_SCROLL_ITERATIONS = 10        # opensea.io/drops lazy-loads more cards as you scroll
_SCROLL_WAIT_MS = 1200         # let each batch of lazy-loaded cards render before scrolling again

DISPLAYABLE_STATUSES = frozenset({"minting_now", "upcoming"})

# e.g. "August 14 at 1:00 PM GMT"
_FUTURE_DATE_RE = re.compile(r"[A-Z][a-z]+ \d{1,2} at \d{1,2}:\d{2} [AP]M [A-Z]{2,4}")
# Countdown segments beneath a "MINT STARTS IN" line render as separate lines
# of just digits/colons (e.g. "12", ":", "34").
_COUNTDOWN_SEGMENT_RE = re.compile(r"[:\d]+")


def classify_drop_status(visible_text: str) -> dict:
    """
    Classify a drop card's raw visible text (from Playwright .inner_text(),
    newline-separated segments) into a status.
    Returns {"status": "minting_now" | "upcoming" | "not_minting", "detail": str | None}.
    """
    if "MINTING NOW" in visible_text:
        return {"status": "minting_now", "detail": None}

    if "MINT STARTS IN" in visible_text:
        lines = [line.strip() for line in visible_text.split("\n") if line.strip()]
        countdown_idx = next(
            (i for i, line in enumerate(lines) if "MINT STARTS IN" in line), None
        )
        countdown_parts: list[str] = []
        if countdown_idx is not None:
            for line in lines[countdown_idx + 1:]:
                if _COUNTDOWN_SEGMENT_RE.fullmatch(line):
                    countdown_parts.append(line)
                else:
                    break
        detail = "MINT STARTS IN"
        if countdown_parts:
            detail = f"{detail} {' '.join(countdown_parts)}"
        return {"status": "upcoming", "detail": _truncate_detail(detail)}

    date_match = _FUTURE_DATE_RE.search(visible_text)
    if date_match:
        return {"status": "upcoming", "detail": _truncate_detail(date_match.group(0))}

    return {"status": "not_minting", "detail": None}


def _truncate_detail(detail: str) -> str:
    if len(detail) <= _MAX_DETAIL_LENGTH:
        return detail
    return detail[:_MAX_DETAIL_LENGTH]


def parse_drop_card(href: str, visible_text: str, image_url: str | None = None) -> dict:
    """
    Combine a scraped (href, visible_text, image_url) triple into a
    drop-shaped dict. Raises ValueError if href doesn't start with
    "/collection/". image_url is validated to actually be an OpenSea CDN
    URL (i2c.seadn.io) — anything else is dropped rather than trusted,
    since it ends up as an <img src> on the dashboard.
    """
    if not href.startswith("/collection/"):
        raise ValueError(f"Expected href to start with '/collection/', got: {href!r}")

    slug = href.removeprefix("/collection/").strip("/")
    slug = slug.removesuffix("/overview")

    lines = [line.strip() for line in visible_text.split("\n") if line.strip()]
    name = lines[0] if lines else ""

    classification = classify_drop_status(visible_text)

    return {
        "collection_slug": slug,
        "name": name,
        "mint_page_url": f"https://opensea.io{href}",
        "image_url": _validate_image_url(image_url),
        "status": classification["status"],
        "status_detail": classification["detail"],
    }


# Exact, anchored prefixes rather than urlparse().hostname — Python's URL
# parser disagrees with browsers on backslash handling (e.g.
# "https://evil.com\@i2c.seadn.io/x" parses as host="i2c.seadn.io" in
# Python's urlparse but as host="evil.com" in a browser), which would let a
# crafted URL slip past a hostname-equality check while a browser resolves
# it to a different origin entirely. A literal prefix match has no such gap.
_TRUSTED_IMAGE_URL_PREFIXES = (
    "https://i2c.seadn.io/",
    "https://openseauserdata.com/",
)


def _validate_image_url(image_url: str | None) -> str | None:
    if not image_url:
        return None
    if not image_url.startswith(_TRUSTED_IMAGE_URL_PREFIXES):
        return None
    return image_url


def _scroll_to_load_more(page) -> None:
    """opensea.io/drops lazy-loads additional cards as the page scrolls.
    Scrolls to the bottom a bounded number of times, stopping early once the
    page stops growing (nothing new left to load)."""
    try:
        last_height = page.evaluate("document.body.scrollHeight")
        for _ in range(_SCROLL_ITERATIONS):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(_SCROLL_WAIT_MS)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height <= last_height:
                break
            last_height = new_height
    except Exception as e:
        logger.debug("[drops] scroll-to-load-more failed, continuing with what's loaded: %s", e)


def _scrape_card(cards, index: int) -> tuple[str, str, str | None] | None:
    """Read one card's (href, visible_text, image_url). image_url is the
    card's first <img> src (OpenSea's hero/banner image for the collection)
    — best-effort, missing image doesn't fail the whole card. Returns None
    on any Playwright failure (timeout, detached element) — logged, not
    silently discarded."""
    try:
        card = cards.nth(index)
        href = card.get_attribute("href")
        text = card.inner_text(timeout=_CARD_TEXT_TIMEOUT_MS).strip()
    except Exception as e:
        logger.debug("[drops] card %d unreadable: %s", index, e)
        return None
    if not href or not text:
        return None

    image_url = None
    try:
        image_url = card.locator("img").first.get_attribute(
            "src", timeout=_CARD_TEXT_TIMEOUT_MS
        )
    except Exception as e:
        logger.debug("[drops] card %d image unreadable: %s", index, e)

    return href, text, image_url


def _scrape_all_cards(page) -> list[dict]:
    cards = page.locator("a[href^='/collection/']")
    count = min(cards.count(), _MAX_DROP_CARDS)

    drops: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for i in range(count):
        scraped = _scrape_card(cards, i)
        if scraped is None:
            skipped += 1
            continue
        href, text, image_url = scraped
        if href in seen:
            continue
        seen.add(href)
        try:
            drops.append(parse_drop_card(href, text, image_url))
        except ValueError as e:
            logger.debug("[drops] card skipped: %s", e)
            skipped += 1

    if skipped:
        logger.info("[drops] %d card(s) skipped while scraping", skipped)
    return drops


def fetch_drops_live() -> list[dict]:
    """
    Playwright I/O: load https://opensea.io/drops, locate a[href^='/collection/']
    cards, call parse_drop_card on each. Returns [] on any failure (network,
    timeout, Playwright not installed) rather than raising.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("[drops] Playwright not available")
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_USER_AGENT)
                page.goto(_DROPS_URL, wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT_MS)
                page.wait_for_timeout(_POST_LOAD_SETTLE_MS)
                _scroll_to_load_more(page)
                drops = _scrape_all_cards(page)
            finally:
                browser.close()
    except Exception as e:
        logger.warning("[drops] Playwright fetch failed: %s", e)
        return []

    logger.info("[drops] Playwright: %d drops", len(drops))
    return drops


def to_display_dict(drop_row: dict) -> dict:
    """
    Flatten a store.get_tracked_drops()-shaped row (which packs status into
    a "stage_data" JSON string) into an explicit, minimal frontend-facing
    dict — deliberately NOT a **drop_row spread, so internal-only columns
    (id, source, raw stage_data, contract_address, chain) never reach an
    unauthenticated API caller. Defensive against missing/malformed
    stage_data (falls back to an empty status rather than raising).
    """
    status = ""
    status_detail = None
    image_url = None
    stage_data = drop_row.get("stage_data")
    if stage_data:
        try:
            parsed = json.loads(stage_data)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            status = parsed.get("status") or ""
            status_detail = parsed.get("status_detail")
            image_url = _validate_image_url(parsed.get("image_url"))

    return {
        "collection_slug": drop_row.get("collection_slug"),
        "name": drop_row.get("name"),
        "mint_page_url": drop_row.get("mint_page_url"),
        "image_url": image_url,
        "status": status,
        "status_detail": status_detail,
        "is_publicly_mintable": status == "minting_now",
    }


def track_drop_by_slug(collection_slug: str, user_tracked: bool = True) -> dict | None:
    """Manually adds (or refreshes) ONE collection as a tracked drop by its
    known slug — bypasses the bulk /drops discovery scrape entirely, which
    only ever surfaces whatever OpenSea's own listing page happens to
    feature (a curated/trending subset, not every collection with a
    scheduled mint). Uses collection_details.get_collection_details for
    the real name/contract_address/mint_schedule.

    user_tracked defaults to True because the primary caller is the
    dashboard's own "track a collection" action — a real person choosing
    this slug. Pass False when calling this on a caller's behalf (e.g. a
    background discovery job resolving a contract to a slug) so it doesn't
    masquerade as something the owner asked for and show up in
    store.get_active_user_tracked_drops.

    Status is derived from the schedule's stages (the earliest one that's
    either currently running or hasn't started yet) rather than scraped
    listing-page countdown text, since there is none for a slug added this
    way — purely cosmetic for the dashboard grid; it plays no part in
    actual firing timing (see firing.py, which uses either the real
    on-chain public-drop window or the resolved presale stage's
    go_live_at, never this).

    Returns the upserted drop row (store.get_tracked_drop_by_slug shape),
    or None if no contract_address could be found at all — nothing useful
    to track without one. Raises ValueError if collection_slug fails
    SLUG_RE (a programming-error guard, callers must validate first)."""
    # Imported locally, not at module level — collection_details.py itself
    # imports FROM drops (_USER_AGENT), so a top-level import here would be
    # circular.
    from . import collection_details

    details = collection_details.get_collection_details(collection_slug)
    contract_address = details.get("contract_address") or ""
    if not contract_address:
        return None

    now = time.time()
    status = "not_minting"
    status_detail = None
    for stage in details.get("mint_schedule") or []:
        starts_epoch = stage.get("starts_epoch")
        ends_epoch = stage.get("ends_epoch")
        if starts_epoch and starts_epoch <= now and (not ends_epoch or ends_epoch > now):
            status, status_detail = "minting_now", None
            break
        if starts_epoch and starts_epoch > now:
            status, status_detail = "upcoming", stage.get("starts")
            break

    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug=collection_slug,
        name=details.get("name") or collection_slug,
        contract_address=contract_address,
        mint_page_url=f"https://opensea.io/collection/{collection_slug}",
        source="manual",
        stage_data=json.dumps({
            "status": status, "status_detail": status_detail,
            "image_url": details.get("image_url"),
        }),
        chain=details.get("chain") or "ethereum",
        user_tracked=user_tracked,
    ))
    return store.get_tracked_drop_by_slug(collection_slug)


_SEADROP_DISCOVERY_STATE_KEY = "seadrop_last_scanned_block"


def discover_new_seadrop_collections() -> int:
    """The actual fix for "so much coming up that's not on [OpenSea's
    /drops] site": polls the chain directly (via
    node_client.get_recent_public_drop_updates) for collections that have
    configured a SeaDrop public mint stage since the last successful scan,
    resolves each newly-seen contract to its OpenSea slug (see
    collection_details.resolve_slug_from_contract_address), and tracks it
    the same way track_drop_by_slug does. This reads from SeaDrop's own
    on-chain events — the real source of truth for which collections are
    using it — rather than OpenSea's own curated listing page, which only
    ever shows a subset.

    The scan position (last successfully-scanned block) is persisted via
    store.get_state/set_state and only advanced past what Node actually
    confirmed scanning — a failed or partial scan just means the next
    scheduled tick resumes from the same bookmark, never silently skipping
    a range of blocks.

    Self-healing: verified live 2026-08-10 that this RPC's eth_getLogs
    only reliably serves ranges near the current chain tip — a bookmark
    that falls too far behind (e.g. several ticks in a row failed, or the
    process was down for a while) can get permanently stuck retrying an
    unreachable historical range on every future tick otherwise. If a
    scan makes literally zero progress past a known bookmark, the
    bookmark is dropped so the NEXT call restarts from a fresh recent
    lookback instead — accepting a real gap in coverage (some collections
    that configured a drop during the gap won't be auto-discovered) rather
    than never making progress again.

    Returns the number of newly tracked collections. Never raises —
    mirrors this module's other functions' resilience style; intended to
    be called from a periodic background job (see agent.py), where an
    uncaught exception would otherwise kill the scheduler tick."""
    from . import collection_details, node_client

    last_scanned = store.get_state(_SEADROP_DISCOVERY_STATE_KEY)
    from_block = str(int(last_scanned) + 1) if last_scanned is not None else None

    try:
        result = node_client.get_recent_public_drop_updates(from_block)
    except RuntimeError as e:
        logger.warning("[drops] on-chain discovery scan failed: %s", e)
        return 0

    updates = result.get("updates") or []
    scanned_to_block = result.get("scannedToBlock")

    if from_block is not None and scanned_to_block is not None:
        if int(scanned_to_block) < int(from_block):
            logger.warning(
                "[drops] on-chain discovery made no progress past block %s — "
                "resetting scan bookmark to a fresh recent lookback", from_block,
            )
            store.delete_state(_SEADROP_DISCOVERY_STATE_KEY)
            return 0

    newly_tracked = 0
    seen_contracts: set[str] = set()
    for update in updates:
        contract_address = update.get("nftContract")
        if not contract_address or contract_address.lower() in seen_contracts:
            continue
        seen_contracts.add(contract_address.lower())

        if store.get_tracked_drop_by_contract_address(contract_address):
            continue  # already tracking this one — a later price/timing
            # update to the same contract doesn't need re-resolving.

        slug = collection_details.resolve_slug_from_contract_address(contract_address)
        if not slug:
            logger.debug(
                "[drops] on-chain discovery: no OpenSea slug found for %r", contract_address
            )
            continue

        try:
            tracked = track_drop_by_slug(slug, user_tracked=False)
        except ValueError:
            continue  # resolved slug somehow failed SLUG_RE — skip, don't crash the scan
        if tracked:
            newly_tracked += 1
            logger.info("[drops] on-chain discovery: newly tracked %r (%s)", slug, contract_address)

    if scanned_to_block is not None:
        store.set_state(_SEADROP_DISCOVERY_STATE_KEY, scanned_to_block)

    return newly_tracked


def get_drops(force_refresh: bool = False) -> list[dict]:
    """
    Cached wrapper: returns cached drops if fresh, else calls fetch_drops_live(),
    persists each result via store.upsert_tracked_drop, falls back to
    store.get_tracked_drops() if the live fetch is empty.
    """
    with _cache_lock:
        now = time.time()
        if not force_refresh and _cache["drops"] and now - _cache["ts"] < _CACHE_TTL:
            return _cache["drops"]

        drops = fetch_drops_live()
        if drops:
            for drop in drops:
                store.upsert_tracked_drop(store.TrackedDropInput(
                    collection_slug=drop["collection_slug"],
                    name=drop["name"],
                    contract_address="",
                    mint_page_url=drop["mint_page_url"],
                    source="playwright",
                    stage_data=json.dumps({
                        "status": drop["status"],
                        "status_detail": drop["status_detail"],
                        "image_url": drop.get("image_url"),
                    }),
                ))
            _cache["drops"] = drops
            _cache["ts"] = now
        else:
            stored = store.get_tracked_drops()
            if stored:
                _cache["drops"] = stored
                _cache["ts"] = now - _CACHE_TTL / 2   # retry sooner

        return _cache["drops"]
