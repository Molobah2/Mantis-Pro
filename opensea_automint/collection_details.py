"""
On-demand collection detail lookup for the OpenSea Auto-Mint dashboard: when a
user clicks a drop card, fetches a collection's description, external
(social/website) links, mint contract address (where discoverable), and its
public "Mint Schedule" (stage names/types/start-end times/price+limit).

Description/links/contract_address: OpenSea's official public v2 REST
endpoint (GET https://api.opensea.io/api/v2/collections/<slug>) is tried
first — it works without an API key (verified live), is far cheaper than a
browser launch, and is our only source for a collection's actual contract
address.

Mint schedule has no REST API equivalent, so a Playwright page load
(https://opensea.io/collection/<slug>, which redirects to its /overview tab)
now ALWAYS runs, in parallel with the API call — even when the API alone
would have supplied a usable description/links, in which case only
mint_schedule from the Playwright result is kept (its description/links are
discarded in favor of the API's). See fetch_collection_details_live's
docstring for the full reasoning on this tradeoff.

Deliberately NOT eager — unlike drops.py's /drops-wide scrape, this is only
triggered per-slug on user request, and cached per-slug (not globally) since
different collections' details are independent and change far less often
than mint status.

NOTE ON <p>-BASED DESCRIPTION EXTRACTION: at the time this was first written,
the live collection page had exactly one <p> element and it was the
description. As of a later spot-check, the live page has ZERO <p> elements
(OpenSea's frontend markup has since changed) — _extract_description is
consequently unlikely to find anything today. Left as-is for now since the
REST API almost always supplies the description first anyway (this fallback
rarely executes in practice); flagged here rather than silently left
misdocumented.
"""
import calendar
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests as _req

from .drops import _USER_AGENT

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict = {}  # slug -> {"ts": float, "data": dict}
_CACHE_TTL = 1800  # 30 minutes — details change far less often than mint status

_PAGE_LOAD_TIMEOUT_MS = 30_000
_POST_LOAD_SETTLE_MS = 2_500
# Generous — this is a WAIT-FOR (resolves as soon as ready, see
# _fetch_via_playwright), not a blind sleep, so a high cap costs nothing in
# the common case. Verified necessary 2026-08-10: the same page render that
# completed comfortably within a few seconds locally took meaningfully
# longer on Railway's container, past the previous fixed-sleep budget.
_SCHEDULE_APPEAR_TIMEOUT_MS = 15_000
_LINK_TIMEOUT_MS = 2_000
_MAX_DESCRIPTION_LENGTH = 600  # truncate before this reaches any storage/display
_MAX_LINKS_TO_SCAN = 20        # bounded loop — collection page links are external content we don't control
_SCHEDULE_ITEM_TIMEOUT_MS = 2_000
_MAX_SCHEDULE_STAGES = 20      # bounded loop — same reasoning as _MAX_LINKS_TO_SCAN
_MAX_SCHEDULE_FIELD_LENGTH = 200  # truncate before this reaches any storage/display

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

# OpenSea's official public v2 REST endpoint — verified live to work without
# an API key, though one is sent when configured (more correct, future-proof
# against rate limiting). Tried FIRST in fetch_collection_details_live,
# before any Playwright browser launch: it's cheaper, faster, and — unlike
# the page scrape — is the only way we have to discover a drop's actual
# on-chain mint contract address.
_OPENSEA_API_BASE = "https://api.opensea.io/api/v2/collections"
_API_REQUEST_TIMEOUT_S = 10

ETH_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


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
    if not href or not isinstance(href, str):
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


_SCHEDULE_START_RE = re.compile(r"^Starts:\s*(.+)$")
_SCHEDULE_END_RE = re.compile(r"^Ends:\s*(.+)$")

_MONTH_NAMES = {name: i for i, name in enumerate(calendar.month_name) if name}

# e.g. "August 10 at 3:00 PM GMT" — every scraped schedule timestamp seen
# live so far uses this exact shape (see drops.py's _FUTURE_DATE_RE, the
# same pattern noticed there).
_SCHEDULE_DATETIME_RE = re.compile(
    r"^([A-Z][a-z]+)\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})\s*([AP]M)\s+GMT$"
)


def _parse_schedule_datetime_to_epoch(text: str | None, now: float | None = None) -> float | None:
    """Parses a scraped schedule timestamp like "August 10 at 3:00 PM GMT"
    into a Unix epoch (GMT == UTC for this purpose) — used as the go-live
    reference for a signed-presale stage's countdown thread, since (unlike
    the public stage's getPublicDrop) there is no on-chain getter for a
    presale stage's timing.

    The scraped text never includes a year, so one is inferred: assume the
    current year, but roll forward to next year if that would put the date
    more than ~30 days in the past (handles being scraped right at a
    Dec/Jan boundary, when a stage dated e.g. "January 5" scraped in late
    December means next year, not this one).

    Returns None if the text doesn't match the expected shape — this is
    scraped third-party markup, never guessed at."""
    if not text:
        return None
    match = _SCHEDULE_DATETIME_RE.match(text.strip())
    if not match:
        return None
    month_name, day_str, hour_str, minute_str, meridiem = match.groups()
    month = _MONTH_NAMES.get(month_name)
    if not month:
        return None
    hour = int(hour_str) % 12
    if meridiem.upper() == "PM":
        hour += 12

    now_dt = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc)
    try:
        candidate = datetime(now_dt.year, month, int(day_str), hour, int(minute_str), tzinfo=timezone.utc)
    except ValueError:
        return None
    if (now_dt - candidate).days > 30:
        try:
            candidate = candidate.replace(year=now_dt.year + 1)
        except ValueError:
            return None
    return candidate.timestamp()


def _parse_schedule_stage(text: str, now: float | None = None) -> dict | None:
    """
    Parses one mint-schedule <li>'s plain visible text (verified live
    against a real upcoming drop) into a stage dict. Text renders as
    newline-separated lines, e.g.:
        Team
        Allowlist
        Starts: August 14 at 1:00 PM GMT
        Free | Limit 20 per wallet
    or, for a stage with an end date and a paid price:
        Public stage
        Public
        Starts: August 14 at 3:00 PM GMT
        Ends: September 13 at 3:00 PM GMT
        $21.09 | Limit 1,000 per wallet

    Returns None if the text doesn't have at least a name/type/one detail
    line — this is third-party markup we don't control, so a stage that
    doesn't match the expected shape is skipped rather than guessed at.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if len(lines) < 3:
        return None

    name = lines[0]
    stage_type = lines[1]
    starts = None
    ends = None
    detail_parts: list[str] = []

    for line in lines[2:]:
        start_match = _SCHEDULE_START_RE.match(line)
        end_match = _SCHEDULE_END_RE.match(line)
        if start_match:
            starts = start_match.group(1)
        elif end_match:
            ends = end_match.group(1)
        else:
            # Everything else is price/limit text, e.g. "Free | Limit 20 per
            # wallet" — collected rather than overwritten, since some real
            # stages render that as more than one line.
            detail_parts.append(line)

    detail = " ".join(detail_parts) if detail_parts else None

    return {
        "name": name[:_MAX_SCHEDULE_FIELD_LENGTH],
        "stage_type": stage_type[:_MAX_SCHEDULE_FIELD_LENGTH],
        "starts": starts[:_MAX_SCHEDULE_FIELD_LENGTH] if starts else None,
        "ends": ends[:_MAX_SCHEDULE_FIELD_LENGTH] if ends else None,
        "starts_epoch": _parse_schedule_datetime_to_epoch(starts, now),
        "ends_epoch": _parse_schedule_datetime_to_epoch(ends, now),
        "detail": detail[:_MAX_SCHEDULE_FIELD_LENGTH] if detail else None,
    }


def _extract_mint_schedule(page) -> list[dict]:
    """
    Extracts the drop's "Mint Schedule" section — stage name (e.g. "Team",
    "GTD", "Public stage"), stage type ("Allowlist" or "Public"), start/end
    time, and price+limit — all plain visible text requiring NO wallet
    connection. Verified live against a real upcoming drop (DIVERGENTS: two
    allowlist stages + a public stage, each with real start times).

    This does NOT reveal whether any specific wallet is eligible for an
    allowlist stage — only that the stage exists and when it runs. Per-
    wallet eligibility requires an authenticated wallet connection (verified
    separately to require more than just eth_requestAccounts — OpenSea's
    real MetaMask SDK does genuine extension detection) and is not handled
    here.

    Anchored on the "Mint schedule" label text via XPath (not a Tailwind
    utility class, which is more likely to be reused/renamed elsewhere)
    to find the following <ol>, so this only ever reads the real schedule
    list rather than some unrelated element that happens to share a class.
    The leading node-set is wrapped in parens with a [1] index — without
    it, if the page ever renders more than one element whose text matches
    (e.g. a hidden duplicate, a responsive mobile/desktop copy), XPath 1.0
    would evaluate "following::ol[1]" per matching node and union the
    results, silently mixing stages from unrelated <ol> elements.
    """
    stages: list[dict] = []
    try:
        items = page.locator(
            "xpath=(//*[contains(text(), 'Mint schedule') or contains(text(), 'Mint Schedule')])[1]"
            "/following::ol[1]/li"
        )
        count = min(items.count(), _MAX_SCHEDULE_STAGES)
    except Exception as e:
        logger.debug("[collection_details] mint schedule unreadable: %s", e)
        return stages

    for i in range(count):
        try:
            text = items.nth(i).inner_text(timeout=_SCHEDULE_ITEM_TIMEOUT_MS).strip()
        except Exception as e:
            logger.debug("[collection_details] schedule stage %d unreadable: %s", i, e)
            continue
        parsed = _parse_schedule_stage(text)
        if parsed:
            stages.append(parsed)

    return stages


def _as_stripped_str(value: object) -> str:
    """Defensively coerce an untrusted JSON field to a stripped string —
    external API responses aren't guaranteed to match our assumed shape
    (e.g. a field we expect as a string could come back as a number or
    list), and this module's functions are documented to never raise."""
    return value.strip() if isinstance(value, str) else ""


def fetch_collection_details_via_api(slug: str) -> dict | None:
    """
    Calls OpenSea's official public v2 REST endpoint
    (GET /api/v2/collections/<slug>) directly — no browser launch required.
    Works without an API key today (verified live), but sends one via the
    x-api-key header when OPENSEA_API_KEY is configured, since that's more
    correct and future-proofs against the key becoming required/rate-limited.

    Returns None on any failure — request exception, non-200 status,
    non-JSON body, or a JSON body that isn't a dict — never raises, mirroring
    the resilience style used throughout this module. On success returns
    {"description": ..., "links": {...}, "contract_address": ...}, omitting
    any link category whose source field was empty/missing/invalid, and
    contract_address only if a well-formed Ethereum contract address was
    found among the collection's contracts.
    """
    headers = {"Accept": "application/json"}
    api_key = os.getenv("OPENSEA_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    try:
        r = _req.get(f"{_OPENSEA_API_BASE}/{slug}", timeout=_API_REQUEST_TIMEOUT_S, headers=headers)
    except _req.exceptions.RequestException as e:
        logger.warning("[collection_details] OpenSea API request failed for %r: %s", slug, e)
        return None

    if r.status_code != 200:
        logger.warning(
            "[collection_details] OpenSea API returned HTTP %d for %r", r.status_code, slug
        )
        return None

    try:
        data = r.json()
    except ValueError as e:
        logger.warning("[collection_details] OpenSea API non-JSON response for %r: %s", slug, e)
        return None

    if not isinstance(data, dict):
        logger.warning("[collection_details] OpenSea API unexpected response shape for %r", slug)
        return None

    name = _as_stripped_str(data.get("name")) or None

    description = _as_stripped_str(data.get("description")) or None
    if description:
        description = description[:_MAX_DESCRIPTION_LENGTH]

    links: dict = {}

    discord_url = validate_external_link(data.get("discord_url"))
    if discord_url:
        links["discord"] = discord_url

    twitter_username = _as_stripped_str(data.get("twitter_username"))
    if twitter_username:
        twitter_url = validate_external_link(f"https://x.com/{twitter_username}")
        if twitter_url:
            links["twitter"] = twitter_url

    instagram_username = _as_stripped_str(data.get("instagram_username"))
    if instagram_username:
        instagram_url = validate_external_link(f"https://instagram.com/{instagram_username}")
        if instagram_url:
            links["instagram"] = instagram_url

    website_url = validate_external_link(data.get("project_url"))
    if website_url:
        links["website"] = website_url

    contract_address = None
    for contract in data.get("contracts") or []:
        if not isinstance(contract, dict) or contract.get("chain") != "ethereum":
            continue
        address = contract.get("address")
        if isinstance(address, str) and ETH_ADDR_RE.match(address):
            contract_address = address
            break

    return {
        "name": name,
        "description": description,
        "links": links,
        "contract_address": contract_address,
    }


def _fetch_via_playwright(slug: str) -> dict:
    """
    Playwright I/O: navigates to https://opensea.io/collection/<slug>
    (which redirects to its /overview page — verified live) and extracts
    description (first <p> element), external links, and the mint schedule.

    Returns {"description": None, "links": {}, "mint_schedule": []} on any
    Playwright/launch failure rather than raising, mirroring drops.py's
    fetch_drops_live() resilience pattern. Never discovers a
    contract_address — that's the OpenSea API's job (see
    fetch_collection_details_via_api); callers thread through whatever
    contract_address the API already found.
    """
    empty_result = {"description": None, "links": {}, "mint_schedule": []}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("[collection_details] Playwright not available")
        return empty_result

    if not _launch_semaphore.acquire(timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S):
        logger.warning(
            "[collection_details] too many concurrent fetches in flight, skipping %r", slug
        )
        return empty_result

    try:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(user_agent=_USER_AGENT)
                    # "networkidle" hangs (and times out) on collections with
                    # a live mint page — continuous background polling for
                    # countdown/mint-count updates means the network never
                    # goes idle, even though the actual page content (mint
                    # schedule included) has long since rendered. Verified
                    # live 2026-08-09 against a real drop: "networkidle"
                    # timed out after 30s with an empty result, while "load"
                    # (plus the existing settle wait below) rendered the full
                    # mint schedule correctly.
                    page.goto(
                        f"https://opensea.io/collection/{slug}",
                        wait_until="load",
                        timeout=_PAGE_LOAD_TIMEOUT_MS,
                    )
                    # The mint schedule (like the rest of this Next.js page)
                    # renders client-side, after "load" fires — waiting a
                    # FIXED duration for that to finish is exactly the kind
                    # of thing that works on a fast local machine and then
                    # comes up empty under real production resource
                    # constraints. Wait for the actual content instead;
                    # this resolves as soon as it's ready (typically well
                    # under the cap) and only times out if the page truly
                    # has no schedule section — a real, expected case for
                    # some collections, not an error.
                    try:
                        page.wait_for_selector(
                            "text=/mint schedule/i", timeout=_SCHEDULE_APPEAR_TIMEOUT_MS,
                        )
                    except Exception as e:
                        logger.debug(
                            "[collection_details] no mint-schedule section appeared for %r: %s", slug, e
                        )
                    page.wait_for_timeout(_POST_LOAD_SETTLE_MS)
                    description = _extract_description(page)
                    links = _extract_links(page)
                    mint_schedule = _extract_mint_schedule(page)
                finally:
                    browser.close()
        except Exception as e:
            logger.warning("[collection_details] Playwright fetch failed for %r: %s", slug, e)
            return empty_result
    finally:
        _launch_semaphore.release()

    return {"description": description, "links": links, "mint_schedule": mint_schedule}


def fetch_collection_details_live(slug: str) -> dict:
    """
    Tries OpenSea's public API first (fetch_collection_details_via_api) for
    description/links/contract_address — cheap, fast, and the only source
    we have for a mint contract address.

    Mint schedule data (stage names/types/start-end times/price+limit) has
    no REST API equivalent, so Playwright now ALWAYS runs to capture it —
    even when the API already supplied a usable description/links (in
    which case only mint_schedule from the Playwright result is used; its
    description/links are discarded in favor of the API's). This trades
    away the old "skip the browser entirely when the API is enough"
    optimization specifically for schedule data; the per-slug cache (30 min,
    see get_collection_details) and the concurrency cap
    (_MAX_CONCURRENT_FETCHES) bound REPEAT views of the same slug and
    concurrent launches globally, but every distinct new slug still pays
    the full Playwright cost on first view. The two I/O calls (API request,
    Playwright launch) are independent, so they run concurrently in a
    background thread rather than sequentially — total latency is close to
    max(api, playwright) instead of their sum.

    Raises ValueError if slug fails SLUG_RE — a programming-error guard,
    callers must validate before calling. The returned dict always has a
    "contract_address" key; Playwright can never discover one, so a
    contract_address found via the API is preserved regardless of which
    source's description/links end up being used.
    """
    if not SLUG_RE.match(slug):
        raise ValueError(f"Invalid collection slug: {slug!r}")

    playwright_holder: dict = {}

    def _run_playwright() -> None:
        playwright_holder["result"] = _fetch_via_playwright(slug)

    playwright_thread = threading.Thread(target=_run_playwright)
    playwright_thread.start()

    api_result = fetch_collection_details_via_api(slug)

    playwright_thread.join()
    playwright_result = playwright_holder["result"]
    mint_schedule = playwright_result.get("mint_schedule", [])

    contract_address = api_result.get("contract_address") if api_result else None
    # Playwright never discovers a collection's display name any more than
    # it discovers a contract_address — the API is the only source for
    # both, so name is preserved the same way regardless of which source's
    # description/links end up being used below.
    name = api_result.get("name") if api_result else None

    if api_result and (api_result.get("description") or api_result.get("links")):
        return {**api_result, "contract_address": contract_address, "name": name, "mint_schedule": mint_schedule}

    return {**playwright_result, "contract_address": contract_address, "name": name, "mint_schedule": mint_schedule}


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
