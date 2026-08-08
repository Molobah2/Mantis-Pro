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

    assert result == {"description": None, "links": {}, "contract_address": None}


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

    assert result == {"description": None, "links": {}, "contract_address": None}


def test_fetch_collection_details_live_uses_api_result_without_launching_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        lambda slug: playwright_calls.append(slug) or {"description": None, "links": {}},
    )

    result = collection_details.fetch_collection_details_live("some-collection")

    assert playwright_calls == []
    assert result == api_result


def test_fetch_collection_details_live_falls_back_to_playwright_when_api_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collection_details, "fetch_collection_details_via_api", lambda slug: None)
    monkeypatch.setattr(
        collection_details,
        "_fetch_via_playwright",
        lambda slug: {"description": "scraped description", "links": {"website": "https://foo.xyz"}},
    )

    result = collection_details.fetch_collection_details_live("some-collection")

    assert result == {
        "description": "scraped description",
        "links": {"website": "https://foo.xyz"},
        "contract_address": None,
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
        lambda slug: {"description": "scraped description", "links": {}},
    )

    result = collection_details.fetch_collection_details_live("some-collection")

    assert result == {
        "description": "scraped description",
        "links": {},
        "contract_address": "0x009efe3f8e50bc67831d6fc2edfaf46c8b8ada23",
    }


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
