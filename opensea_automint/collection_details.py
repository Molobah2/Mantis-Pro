"""
On-demand collection detail lookup for the OpenSea Auto-Mint dashboard: when a
user clicks a drop card, fetches the collection's own OpenSea page
(https://opensea.io/collection/<slug>) via Playwright and extracts its
description and external (social/website) links.

Deliberately NOT eager — unlike drops.py's /drops-wide scrape, this is only
triggered per-slug on user request, and cached per-slug (not globally) since
different collections' details are independent and change far less often
than mint status.

Verified against the live site: the collection page has exactly one <p>
element and it IS the description; external links carry no distinguishing
aria-label/title, so social vs. website links are classified by hostname
pattern-matching rather than any semantic HTML marker.
"""
import logging
import re
import threading
import time
from urllib.parse import urlparse

from .drops import _USER_AGENT

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict = {}  # slug -> {"ts": float, "data": dict}
_CACHE_TTL = 1800  # 30 minutes — details change far less often than mint status

_PAGE_LOAD_TIMEOUT_MS = 30_000
_POST_LOAD_SETTLE_MS = 2_500
_LINK_TIMEOUT_MS = 2_000
_MAX_DESCRIPTION_LENGTH = 600  # truncate before this reaches any storage/display
_MAX_LINKS_TO_SCAN = 20        # bounded loop — collection page links are external content we don't control

# Unlike drops.py's bulk /drops scrape (globally cached, force-refresh
# admin-gated), this endpoint is public and per-slug — the per-IP rate limit
# in routes.py bounds one client, but nothing bounds TOTAL concurrent
# Playwright launches across many slugs/IPs at once. A real browser launch
# per request is expensive; cap how many can run at the same time regardless
# of who's asking, rather than trusting client-identity-based limits alone.
_MAX_CONCURRENT_FETCHES = 2
_SEMAPHORE_ACQUIRE_TIMEOUT_S = 5.0
_launch_semaphore = threading.Semaphore(_MAX_CONCURRENT_FETCHES)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")

_MAX_LINK_LENGTH = 2000  # defensive cap on a URL we'd hand to a frontend as a clickable <a href>

_TWITTER_HOSTS = frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"})
_INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com"})


def classify_external_link(href: str) -> str:
    """
    Classify a URL by hostname into 'twitter' | 'discord' | 'instagram' | 'website'.
    Unparseable input (or anything that doesn't match a known pattern) safely
    defaults to 'website' rather than raising.
    """
    try:
        hostname = (urlparse(href).hostname or "").lower()
    except Exception as e:
        logger.debug("[collection_details] unparseable link for classification: %s", e)
        return "website"

    if hostname in _TWITTER_HOSTS:
        return "twitter"
    if "discord" in hostname:
        return "discord"
    if hostname in _INSTAGRAM_HOSTS:
        return "instagram"
    return "website"


def validate_external_link(href: str | None) -> str | None:
    """
    Returns href unchanged if it's a plausible safe external link to hand to a
    frontend as a clickable <a href>, else None.

    Unlike drops.py's _validate_image_url, this does NOT compare against a
    fixed trusted-hostname allowlist (there isn't one — arbitrary external
    sites are the whole point), so it checks *scheme* rather than matching a
    specific hostname string; parser disagreement about backslash-in-authority
    handling can't cause a false-trust bypass the way it could for a
    hostname-equality check.
    """
    if not href:
        return None
    if len(href) > _MAX_LINK_LENGTH:
        return None
    try:
        parsed = urlparse(href)
    except Exception as e:
        logger.debug("[collection_details] unparseable link for validation: %s", e)
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return href


def _extract_description(page) -> str | None:
    try:
        text = page.locator("p").first.inner_text(timeout=_LINK_TIMEOUT_MS).strip()
    except Exception as e:
        logger.debug("[collection_details] description unreadable: %s", e)
        return None
    if not text:
        return None
    return text[:_MAX_DESCRIPTION_LENGTH]


def _extract_links(page) -> dict:
    links: dict = {}
    try:
        anchors = page.locator("a[href^='http']")
        count = min(anchors.count(), _MAX_LINKS_TO_SCAN)
    except Exception as e:
        logger.debug("[collection_details] links unreadable: %s", e)
        return links

    for i in range(count):
        try:
            href = anchors.nth(i).get_attribute("href", timeout=_LINK_TIMEOUT_MS)
        except Exception as e:
            logger.debug("[collection_details] link %d unreadable: %s", i, e)
            continue
        if not href or "opensea.io" in href:
            continue
        validated = validate_external_link(href)
        if not validated:
            continue
        category = classify_external_link(validated)
        links.setdefault(category, validated)

    return links


def fetch_collection_details_live(slug: str) -> dict:
    """
    Playwright I/O: navigates to https://opensea.io/collection/<slug> and
    extracts its description (first <p> element) and external links.

    Raises ValueError if slug fails SLUG_RE — a programming-error guard,
    callers must validate before calling. Returns
    {"description": None, "links": {}} on any network/Playwright failure
    rather than raising, mirroring drops.py's fetch_drops_live() resilience
    pattern.
    """
    if not SLUG_RE.match(slug):
        raise ValueError(f"Invalid collection slug: {slug!r}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("[collection_details] Playwright not available")
        return {"description": None, "links": {}}

    if not _launch_semaphore.acquire(timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S):
        logger.warning(
            "[collection_details] too many concurrent fetches in flight, skipping %r", slug
        )
        return {"description": None, "links": {}}

    try:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(user_agent=_USER_AGENT)
                    page.goto(
                        f"https://opensea.io/collection/{slug}",
                        wait_until="networkidle",
                        timeout=_PAGE_LOAD_TIMEOUT_MS,
                    )
                    page.wait_for_timeout(_POST_LOAD_SETTLE_MS)
                    description = _extract_description(page)
                    links = _extract_links(page)
                finally:
                    browser.close()
        except Exception as e:
            logger.warning("[collection_details] Playwright fetch failed for %r: %s", slug, e)
            return {"description": None, "links": {}}
    finally:
        _launch_semaphore.release()

    return {"description": description, "links": links}


def get_collection_details(slug: str) -> dict:
    """
    Cached wrapper, cache keyed per-slug — different collections cache
    independently, unlike drops.py's single global cache.

    Raises ValueError if slug doesn't match SLUG_RE (a caller-contract
    violation the route layer catches and turns into a 400, not a network
    failure). Thread-safe via a module-level lock.
    """
    if not SLUG_RE.match(slug):
        raise ValueError(f"Invalid collection slug: {slug!r}")

    with _cache_lock:
        now = time.time()
        entry = _cache.get(slug)
        if entry and now - entry["ts"] < _CACHE_TTL:
            return entry["data"]

    data = fetch_collection_details_live(slug)

    with _cache_lock:
        _cache[slug] = {"ts": time.time(), "data": data}

    return data
