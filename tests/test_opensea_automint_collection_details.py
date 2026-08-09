import sys

import pytest
import requests

from opensea_automint import collection_details


class _FakeResponse:
    """Minimal stand-in for requests.Response, matching this test suite's mocking style."""

    def __init__(self, status_code: int = 200, json_data=None, raise_json_error: bool = False) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("response body is not valid JSON")
        return self._json_data


_CHEAP_SHOT_API_RESPONSE = {
    "description": "Who said this was a fair fight? ...",
    "project_url": "",
    "wiki_url": "",
    "discord_url": "https://discord.gg/AfB8EYDVbB",
    "telegram_url": "",
    "twitter_username": "OrangeHare_io",
    "instagram_username": "",
    "contracts": [
        {"address": "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23", "chain": "ethereum"},
    ],
}


# ── classify_external_link ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "href,expected",
    [
        ("https://x.com/foo", "twitter"),
        ("https://www.twitter.com/foo", "twitter"),
        ("https://discord.gg/abc", "discord"),
        ("https://discord.com/invite/abc", "discord"),
        ("https://instagram.com/foo", "instagram"),
        ("https://www.instagram.com/foo", "instagram"),
        ("https://foo-project.xyz", "website"),
    ],
)
def test_classify_external_link(href: str, expected: str) -> None:
    assert collection_details.classify_external_link(href) == expected


def test_classify_external_link_unparseable_defaults_to_website() -> None:
    # A string urlparse chokes on (invalid port) rather than raising outright
    # is still handled defensively — classify_external_link must never raise.
    result = collection_details.classify_external_link("http://[::1")

    assert result == "website"


# ── validate_external_link ───────────────────────────────────────────────

def test_validate_external_link_accepts_https_url() -> None:
    assert collection_details.validate_external_link("https://x.com/foo") == "https://x.com/foo"


def test_validate_external_link_accepts_http_url() -> None:
    assert collection_details.validate_external_link("http://example.com") == "http://example.com"


def test_validate_external_link_rejects_javascript_scheme() -> None:
    assert collection_details.validate_external_link("javascript:alert(1)") is None


def test_validate_external_link_rejects_data_scheme() -> None:
    assert collection_details.validate_external_link("data:text/html,<script>alert(1)</script>") is None


def test_validate_external_link_rejects_protocol_relative_url() -> None:
    assert collection_details.validate_external_link("//evil.com") is None


def test_validate_external_link_rejects_empty_string() -> None:
    assert collection_details.validate_external_link("") is None


def test_validate_external_link_rejects_none() -> None:
    assert collection_details.validate_external_link(None) is None


def test_validate_external_link_rejects_overlong_url() -> None:
    overlong = "https://example.com/" + ("a" * 3000)

    assert collection_details.validate_external_link(overlong) is None


# ── fetch_collection_details_via_api ─────────────────────────────────────

def test_fetch_collection_details_via_api_parses_real_example_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENSEA_API_KEY", raising=False)

    def fake_get(url: str, timeout=None, headers=None) -> _FakeResponse:
        assert url == "https://api.opensea.io/api/v2/collections/cheap-shot"
        return _FakeResponse(200, _CHEAP_SHOT_API_RESPONSE)

    monkeypatch.setattr(collection_details._req, "get", fake_get)

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is not None
    assert result["description"] == _CHEAP_SHOT_API_RESPONSE["description"]
    assert result["links"] == {
        "discord": "https://discord.gg/AfB8EYDVbB",
        "twitter": "https://x.com/OrangeHare_io",
    }
    assert result["contract_address"] == "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23"


def test_fetch_collection_details_via_api_no_contracts_yields_none_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {**_CHEAP_SHOT_API_RESPONSE, "contracts": []}
    monkeypatch.setattr(collection_details._req, "get", lambda *a, **k: _FakeResponse(200, response))

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is not None
    assert result["contract_address"] is None


def test_fetch_collection_details_via_api_malformed_address_yields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        **_CHEAP_SHOT_API_RESPONSE,
        "contracts": [{"address": "0xnothexchars", "chain": "ethereum"}],
    }
    monkeypatch.setattr(collection_details._req, "get", lambda *a, **k: _FakeResponse(200, response))

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is not None
    assert result["contract_address"] is None


def test_fetch_collection_details_via_api_non_string_address_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Untrusted external JSON — the API could return a malformed shape.
    # This must degrade gracefully (contract_address=None), never raise.
    response = {
        **_CHEAP_SHOT_API_RESPONSE,
        "contracts": [{"address": 12345, "chain": "ethereum"}],
    }
    monkeypatch.setattr(collection_details._req, "get", lambda *a, **k: _FakeResponse(200, response))

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is not None
    assert result["contract_address"] is None


def test_fetch_collection_details_via_api_non_string_text_fields_do_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # description/twitter_username/instagram_username/project_url as
    # non-strings (a real API contract violation, not something we control)
    # must not raise — mirrors the documented "never raises" contract.
    response = {
        "description": 42,
        "project_url": ["not", "a", "string"],
        "wiki_url": "",
        "discord_url": None,
        "telegram_url": "",
        "twitter_username": 7,
        "instagram_username": {"nested": "object"},
        "contracts": [],
    }
    monkeypatch.setattr(collection_details._req, "get", lambda *a, **k: _FakeResponse(200, response))

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is not None
    assert result["description"] is None
    assert result["links"] == {}
    assert result["contract_address"] is None


def test_fetch_collection_details_via_api_non_ethereum_chain_yields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        **_CHEAP_SHOT_API_RESPONSE,
        "contracts": [
            {"address": "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23", "chain": "polygon"},
        ],
    }
    monkeypatch.setattr(collection_details._req, "get", lambda *a, **k: _FakeResponse(200, response))

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is not None
    assert result["contract_address"] is None


def test_fetch_collection_details_via_api_returns_none_on_non_200_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection_details._req, "get", lambda *a, **k: _FakeResponse(404, None))

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is None


def test_fetch_collection_details_via_api_returns_none_on_network_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_request_exception(*args, **kwargs):
        raise requests.exceptions.RequestException("connection failed")

    monkeypatch.setattr(collection_details._req, "get", raise_request_exception)

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is None


def test_fetch_collection_details_via_api_returns_none_on_non_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        collection_details._req,
        "get",
        lambda *a, **k: _FakeResponse(200, raise_json_error=True),
    )

    result = collection_details.fetch_collection_details_via_api("cheap-shot")

    assert result is None


def test_fetch_collection_details_via_api_sends_api_key_header_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_get(url: str, timeout=None, headers=None) -> _FakeResponse:
        captured["headers"] = headers
        return _FakeResponse(200, _CHEAP_SHOT_API_RESPONSE)

    monkeypatch.setenv("OPENSEA_API_KEY", "secret-key-123")
    monkeypatch.setattr(collection_details._req, "get", fake_get)

    collection_details.fetch_collection_details_via_api("cheap-shot")

    assert captured["headers"]["x-api-key"] == "secret-key-123"


def test_fetch_collection_details_via_api_omits_api_key_header_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_get(url: str, timeout=None, headers=None) -> _FakeResponse:
        captured["headers"] = headers
        return _FakeResponse(200, _CHEAP_SHOT_API_RESPONSE)

    monkeypatch.delenv("OPENSEA_API_KEY", raising=False)
    monkeypatch.setattr(collection_details._req, "get", fake_get)

    collection_details.fetch_collection_details_via_api("cheap-shot")

    assert "x-api-key" not in captured["headers"]


# ── fetch_collection_details_live ────────────────────────────────────────

@pytest.mark.parametrize(
    "bad_slug",
    ["../etc/passwd", "UPPERCASE", "", "has spaces"],
)
def test_fetch_collection_details_live_raises_value_error_for_invalid_slug(bad_slug: str) -> None:
    with pytest.raises(ValueError):
        collection_details.fetch_collection_details_live(bad_slug)


def test_fetch_collection_details_live_returns_empty_shape_when_playwright_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # API-first attempt is disabled here (returns None, as if the API failed
    # entirely) so this test exercises the Playwright fallback path in
    # isolation, without making a real network call.
    monkeypatch.setattr(collection_details, "fetch_collection_details_via_api", lambda slug: None)
    # Simulate Playwright not being installed: Python raises ImportError for
    # "from playwright.sync_api import sync_playwright" when the module is
    # blocked in sys.modules this way (mirrors how drops.py's equivalent
    # ImportError guard is triggered in real "not installed" environments).
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    result = collection_details.fetch_collection_details_live("some-collection")

    assert result == {"description": None, "links": {}, "contract_address": None, "mint_schedule": []}


def test_fetch_collection_details_live_returns_empty_shape_when_concurrency_limit_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # API-first attempt is disabled here (returns None) so this test
    # exercises the Playwright fallback path in isolation.
    monkeypatch.setattr(collection_details, "fetch_collection_details_via_api", lambda slug: None)
    # Simulates every concurrent-fetch slot already being in use (e.g. many
    # simultaneous requests for different slugs) — must degrade gracefully
    # rather than pile up threads waiting indefinitely on a real browser launch.
    monkeypatch.setattr(collection_details, "_SEMAPHORE_ACQUIRE_TIMEOUT_S", 0.05)
    for _ in range(collection_details._MAX_CONCURRENT_FETCHES):
        collection_details._launch_semaphore.acquire()
    try:
        result = collection_details.fetch_collection_details_live("some-collection")
    finally:
        for _ in range(collection_details._MAX_CONCURRENT_FETCHES):
            collection_details._launch_semaphore.release()

    assert result == {"description": None, "links": {}, "contract_address": None, "mint_schedule": []}


def test_fetch_collection_details_live_uses_api_description_but_still_calls_playwright_for_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mint schedule has no REST API equivalent, so Playwright now always
    # runs (for schedule data) even when the API already has a usable
    # description/links — but the API's description/links still win over
    # whatever Playwright's own (possibly stale/broken) extraction found.
    api_result = {
        "description": "an API-sourced description",
        "links": {"twitter": "https://x.com/foo"},
        "contract_address": "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23",
    }
    monkeypatch.setattr(
        collection_details, "fetch_collection_details_via_api", lambda slug: api_result
    )
    playwright_calls = []
    monkeypatch.setattr(
        collection_details,
        "_fetch_via_playwright",
        lambda slug: playwright_calls.append(slug) or {
            "description": "should be ignored",
            "links": {"website": "should be ignored"},
            "mint_schedule": [{"name": "Public stage", "stage_type": "Public",
                                "starts": "August 14 at 3:00 PM GMT", "ends": None, "detail": "Free"}],
        },
    )

    result = collection_details.fetch_collection_details_live("some-collection")

    assert playwright_calls == ["some-collection"]
    assert result == {**api_result, "mint_schedule": [
        {"name": "Public stage", "stage_type": "Public",
         "starts": "August 14 at 3:00 PM GMT", "ends": None, "detail": "Free"},
    ]}


def test_fetch_collection_details_live_falls_back_to_playwright_when_api_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection_details, "fetch_collection_details_via_api", lambda slug: None)
    monkeypatch.setattr(
        collection_details,
        "_fetch_via_playwright",
        lambda slug: {
            "description": "scraped description",
            "links": {"website": "https://foo.xyz"},
            "mint_schedule": [],
        },
    )

    result = collection_details.fetch_collection_details_live("some-collection")

    assert result == {
        "description": "scraped description",
        "links": {"website": "https://foo.xyz"},
        "contract_address": None,
        "mint_schedule": [],
    }


def test_fetch_collection_details_live_keeps_api_contract_address_when_falling_back_to_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # API found a contract address but no usable description/links — must
    # still fall through to Playwright to fill those in, while never letting
    # the Playwright fallback (which has no way to discover a contract) wipe
    # out the real contract_address the API already found.
    monkeypatch.setattr(
        collection_details,
        "fetch_collection_details_via_api",
        lambda slug: {
            "description": None,
            "links": {},
            "contract_address": "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23",
        },
    )
    monkeypatch.setattr(
        collection_details,
        "_fetch_via_playwright",
        lambda slug: {"description": "scraped description", "links": {}, "mint_schedule": []},
    )

    result = collection_details.fetch_collection_details_live("some-collection")

    assert result == {
        "description": "scraped description",
        "links": {},
        "contract_address": "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23",
        "mint_schedule": [],
    }


# ── _parse_schedule_stage / _extract_mint_schedule ───────────────────────

def test_parse_schedule_stage_allowlist_with_no_end_time() -> None:
    text = "Team\nAllowlist\nStarts: August 14 at 1:00 PM GMT\nFree | Limit 20 per wallet"

    result = collection_details._parse_schedule_stage(text)

    assert result == {
        "name": "Team",
        "stage_type": "Allowlist",
        "starts": "August 14 at 1:00 PM GMT",
        "ends": None,
        "detail": "Free | Limit 20 per wallet",
    }


def test_parse_schedule_stage_public_with_end_time_and_price() -> None:
    text = (
        "Public stage\nPublic\nStarts: August 14 at 3:00 PM GMT\n"
        "Ends: September 13 at 3:00 PM GMT\n$21.09 | Limit 1,000 per wallet"
    )

    result = collection_details._parse_schedule_stage(text)

    assert result == {
        "name": "Public stage",
        "stage_type": "Public",
        "starts": "August 14 at 3:00 PM GMT",
        "ends": "September 13 at 3:00 PM GMT",
        "detail": "$21.09 | Limit 1,000 per wallet",
    }


def test_parse_schedule_stage_returns_none_for_too_few_lines() -> None:
    assert collection_details._parse_schedule_stage("Team\nAllowlist") is None


def test_parse_schedule_stage_ends_without_starts() -> None:
    # Not observed live, but the regex-scan logic should handle it correctly
    # regardless of which of Starts:/Ends: is present.
    text = "Public stage\nPublic\nEnds: September 13 at 3:00 PM GMT\nFree"

    result = collection_details._parse_schedule_stage(text)

    assert result["starts"] is None
    assert result["ends"] == "September 13 at 3:00 PM GMT"


def test_parse_schedule_stage_preserves_multiple_detail_lines() -> None:
    # Regression test: a stage rendering price and limit as two separate
    # non-"Starts:"/"Ends:" lines must keep both, not silently drop the
    # first one by overwriting a scalar.
    text = "Team\nAllowlist\nStarts: August 14 at 1:00 PM GMT\n$5.00\nLimit 20 per wallet"

    result = collection_details._parse_schedule_stage(text)

    assert result["detail"] == "$5.00 Limit 20 per wallet"


def test_parse_schedule_stage_ignores_blank_lines() -> None:
    text = "Team\n\nAllowlist\n\nStarts: August 14 at 1:00 PM GMT\n\nFree | Limit 20 per wallet\n\n"

    result = collection_details._parse_schedule_stage(text)

    assert result == {
        "name": "Team",
        "stage_type": "Allowlist",
        "starts": "August 14 at 1:00 PM GMT",
        "ends": None,
        "detail": "Free | Limit 20 per wallet",
    }


def test_parse_schedule_stage_truncates_overlong_fields() -> None:
    overlong = "x" * 500
    text = f"{overlong}\n{overlong}\nStarts: {overlong}\n{overlong}"

    result = collection_details._parse_schedule_stage(text)

    assert result is not None
    assert len(result["name"]) == collection_details._MAX_SCHEDULE_FIELD_LENGTH
    assert len(result["stage_type"]) == collection_details._MAX_SCHEDULE_FIELD_LENGTH
    assert len(result["starts"]) == collection_details._MAX_SCHEDULE_FIELD_LENGTH
    assert len(result["detail"]) == collection_details._MAX_SCHEDULE_FIELD_LENGTH


class _FakeLocator:
    def __init__(self, items: list[str | None] | None = None, raise_on_count: bool = False) -> None:
        # An item of None means "this element raises on inner_text()" —
        # simulates one stage detaching/failing mid-scrape while siblings
        # still succeed.
        self._items = items or []
        self._raise_on_count = raise_on_count

    def count(self) -> int:
        if self._raise_on_count:
            raise Exception("locator resolution failed")
        return len(self._items)

    def nth(self, i: int) -> "_FakeItem":
        item = self._items[i]
        return _FakeItem(item or "", raise_on_text=item is None)


class _FakeItem:
    def __init__(self, text: str, raise_on_text: bool = False) -> None:
        self._text = text
        self._raise_on_text = raise_on_text

    def inner_text(self, timeout: int) -> str:
        if self._raise_on_text:
            raise Exception("element detached")
        return self._text


class _FakePage:
    def __init__(self, locator: _FakeLocator) -> None:
        self._locator = locator

    def locator(self, selector: str) -> _FakeLocator:
        return self._locator


def test_extract_mint_schedule_returns_parsed_stages() -> None:
    page = _FakePage(_FakeLocator([
        "Team\nAllowlist\nStarts: August 14 at 1:00 PM GMT\nFree | Limit 20 per wallet",
        "Public stage\nPublic\nStarts: August 14 at 3:00 PM GMT\n$21.09 | Limit 1,000 per wallet",
    ]))

    result = collection_details._extract_mint_schedule(page)

    assert len(result) == 2
    assert result[0]["name"] == "Team"
    assert result[1]["name"] == "Public stage"


def test_extract_mint_schedule_returns_empty_list_when_locator_fails() -> None:
    page = _FakePage(_FakeLocator(raise_on_count=True))

    result = collection_details._extract_mint_schedule(page)

    assert result == []


def test_extract_mint_schedule_skips_unparseable_stages_without_raising() -> None:
    page = _FakePage(_FakeLocator(["Team\nAllowlist"]))  # too few lines, unparseable

    result = collection_details._extract_mint_schedule(page)

    assert result == []


def test_extract_mint_schedule_skips_one_failing_stage_but_keeps_the_rest() -> None:
    page = _FakePage(_FakeLocator([
        "Team\nAllowlist\nStarts: August 14 at 1:00 PM GMT\nFree | Limit 20 per wallet",
        None,  # this element raises on inner_text() — e.g. detached mid-scrape
        "Public stage\nPublic\nStarts: August 14 at 3:00 PM GMT\n$21.09 | Limit 1,000 per wallet",
    ]))

    result = collection_details._extract_mint_schedule(page)

    assert len(result) == 2
    assert result[0]["name"] == "Team"
    assert result[1]["name"] == "Public stage"


def test_extract_mint_schedule_anchors_on_the_first_matching_label_only() -> None:
    """
    Real-browser regression test (not a pure-Python mock, since the bug this
    guards against is in the actual XPath expression's semantics, which
    Python mocks can't exercise): if the page renders the "Mint schedule"
    label text more than once (e.g. a duplicate section — plausible on a
    real React site), the extractor must still read exactly one <ol>'s
    stages, not a union of stages from every <ol> that follows any matching
    label. Both duplicates here are fully visible (NOT display:none) —
    inner_text() returns "" for hidden elements regardless of which <ol>
    the XPath resolves to, which would mask this exact bug rather than
    exercise it.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    html = """
    <html><body>
      <div>
        <span>Mint schedule</span>
        <ol>
          <li>Decoy<br>Allowlist<br>Starts: January 1 at 1:00 PM GMT<br>Free</li>
        </ol>
      </div>
      <div>
        <span>Mint schedule</span>
        <ol>
          <li>Team<br>Allowlist<br>Starts: August 14 at 1:00 PM GMT<br>Free | Limit 20 per wallet</li>
          <li>Public stage<br>Public<br>Starts: August 14 at 3:00 PM GMT<br>$21.09 | Limit 1,000 per wallet</li>
        </ol>
      </div>
    </body></html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html)
            result = collection_details._extract_mint_schedule(page)
        finally:
            browser.close()

    # Must come from exactly ONE <ol> (the first matching label's), not a
    # union of both — i.e. exactly 1 stage (the Decoy), not 3.
    assert len(result) == 1
    assert result[0]["name"] == "Decoy"


# ── get_collection_details (caching) ─────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_collection_details_cache():
    """Ensure each test starts with an empty per-slug cache."""
    collection_details._cache.clear()
    yield
    collection_details._cache.clear()


def test_get_collection_details_raises_value_error_for_invalid_slug() -> None:
    with pytest.raises(ValueError):
        collection_details.get_collection_details("../etc/passwd")


def test_get_collection_details_caches_per_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_calls = []

    def fake_fetch(slug: str) -> dict:
        fetch_calls.append(slug)
        return {"description": f"desc for {slug}", "links": {}}

    monkeypatch.setattr(collection_details, "fetch_collection_details_live", fake_fetch)

    collection_details.get_collection_details("cheap-shot")
    collection_details.get_collection_details("cheap-shot")

    assert fetch_calls == ["cheap-shot"]

    collection_details.get_collection_details("other-collection")

    assert fetch_calls == ["cheap-shot", "other-collection"]
