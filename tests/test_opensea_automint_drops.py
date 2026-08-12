import json
import time

import pytest

from opensea_automint import collection_details as _collection_details
from opensea_automint import drops, node_client, store

# ── Real visible-text fixtures captured from opensea.io/drops (RESEARCH_NOTES.md) ──
# The prompt's "|" separators represent newlines as returned by Playwright's
# .inner_text() — tests below use "\n" to match that reality.

GOD_PULL = "GOD PULL\nBy DFZ_DEVELOPMENT\nFLOOR PRICE\nITEMS\nTOTAL VOLUME\nLISTED"
ODD_CATS = "ODD CATS\nBy 3c8a28\nFLOOR PRICE\nITEMS\nTOTAL VOLUME\nLISTED"
DOOM_SCROLL = (
    "Doom Scroll\nBy ThankYouX\nMINT PRICE\n—\nTOTAL ITEMS\n256\n"
    "MINT STARTS IN\n:\n:\n:"
)
COLLECTR = (
    "Collectr\nBy KusariTeam\nMINT PRICE\n$10.73\nTOTAL ITEMS\nOPEN EDITION\n"
    "ITEMS MINTED"
)
CHEAP_SHOT = "Cheap Shot\nMINTING NOW\n$2.88"
BONE_THEATER = "Bone Theater\nMINTING NOW\n$2.88"
OVERGROWTH = (
    "\"No Such Thing As Overgrowth\" by Amber Vittoria\nAugust 12 at 3:00 PM GMT"
)
DIVERGENTS = "DIVERGENTS\nAugust 14 at 1:00 PM GMT"


# ── classify_drop_status ─────────────────────────────────────────────

def test_classify_god_pull_is_not_minting() -> None:
    result = drops.classify_drop_status(GOD_PULL)

    assert result["status"] == "not_minting"
    assert result["detail"] is None


def test_classify_odd_cats_is_not_minting() -> None:
    result = drops.classify_drop_status(ODD_CATS)

    assert result["status"] == "not_minting"
    assert result["detail"] is None


def test_classify_doom_scroll_is_upcoming_with_countdown_detail() -> None:
    result = drops.classify_drop_status(DOOM_SCROLL)

    assert result["status"] == "upcoming"
    assert result["detail"] is not None
    assert "MINT STARTS IN" in result["detail"]


def test_classify_collectr_is_not_minting() -> None:
    result = drops.classify_drop_status(COLLECTR)

    assert result["status"] == "not_minting"
    assert result["detail"] is None


def test_classify_cheap_shot_is_minting_now_with_no_detail() -> None:
    result = drops.classify_drop_status(CHEAP_SHOT)

    assert result["status"] == "minting_now"
    assert result["detail"] is None


def test_classify_bone_theater_is_minting_now_with_no_detail() -> None:
    result = drops.classify_drop_status(BONE_THEATER)

    assert result["status"] == "minting_now"
    assert result["detail"] is None


def test_classify_overgrowth_is_upcoming_with_date_detail() -> None:
    result = drops.classify_drop_status(OVERGROWTH)

    assert result["status"] == "upcoming"
    assert result["detail"] == "August 12 at 3:00 PM GMT"


def test_classify_divergents_is_upcoming_with_date_detail() -> None:
    result = drops.classify_drop_status(DIVERGENTS)

    assert result["status"] == "upcoming"
    assert result["detail"] == "August 14 at 1:00 PM GMT"


def test_classify_empty_string_defaults_to_not_minting() -> None:
    result = drops.classify_drop_status("")

    assert result["status"] == "not_minting"
    assert result["detail"] is None


def test_classify_string_with_no_recognizable_markers_defaults_to_not_minting() -> None:
    result = drops.classify_drop_status("Some Random Collection\nJust some noise text")

    assert result["status"] == "not_minting"
    assert result["detail"] is None


# ── parse_drop_card ───────────────────────────────────────────────────

def test_parse_drop_card_strips_overview_suffix_from_slug() -> None:
    result = drops.parse_drop_card("/collection/cheap-shot/overview", CHEAP_SHOT)

    assert result["collection_slug"] == "cheap-shot"
    assert result["name"] == "Cheap Shot"
    assert result["mint_page_url"] == "https://opensea.io/collection/cheap-shot/overview"
    assert result["status"] == "minting_now"
    assert result["status_detail"] is None


def test_parse_drop_card_without_overview_suffix() -> None:
    result = drops.parse_drop_card("/collection/godpull", GOD_PULL)

    assert result["collection_slug"] == "godpull"
    assert result["name"] == "GOD PULL"
    assert result["mint_page_url"] == "https://opensea.io/collection/godpull"
    assert result["status"] == "not_minting"


def test_parse_drop_card_upcoming_date_drop() -> None:
    result = drops.parse_drop_card("/collection/divergents", DIVERGENTS)

    assert result["collection_slug"] == "divergents"
    assert result["name"] == "DIVERGENTS"
    assert result["status"] == "upcoming"
    assert result["status_detail"] == "August 14 at 1:00 PM GMT"


def test_parse_drop_card_raises_value_error_for_non_collection_href() -> None:
    with pytest.raises(ValueError):
        drops.parse_drop_card("/account/settings", CHEAP_SHOT)


def test_parse_drop_card_accepts_trusted_seadn_image_url() -> None:
    result = drops.parse_drop_card(
        "/collection/cheap-shot", CHEAP_SHOT,
        image_url="https://i2c.seadn.io/collection/cheap-shot/image.png?w=2000",
    )

    assert result["image_url"] == "https://i2c.seadn.io/collection/cheap-shot/image.png?w=2000"


def test_parse_drop_card_rejects_untrusted_image_host() -> None:
    result = drops.parse_drop_card(
        "/collection/cheap-shot", CHEAP_SHOT,
        image_url="https://evil.example.com/tracker.png",
    )

    assert result["image_url"] is None


def test_parse_drop_card_rejects_non_https_image_url() -> None:
    result = drops.parse_drop_card(
        "/collection/cheap-shot", CHEAP_SHOT,
        image_url="javascript:alert(1)",
    )

    assert result["image_url"] is None


def test_parse_drop_card_handles_missing_image_url() -> None:
    result = drops.parse_drop_card("/collection/cheap-shot", CHEAP_SHOT, image_url=None)

    assert result["image_url"] is None


def test_parse_drop_card_rejects_backslash_authority_confusion_trick() -> None:
    # Python's urlparse().hostname would read this as host="i2c.seadn.io"
    # (treating "evil.com\" as userinfo) while a real browser resolves the
    # authority as "evil.com" — a hostname-equality check alone would wrongly
    # trust this. The anchored-prefix check must reject it outright.
    result = drops.parse_drop_card(
        "/collection/cheap-shot", CHEAP_SHOT,
        image_url="https://evil.com\\@i2c.seadn.io/x.png",
    )

    assert result["image_url"] is None


def test_classify_long_countdown_detail_is_truncated() -> None:
    noisy = "Some Drop\nMINT STARTS IN\n" + "\n".join(["1234567890"] * 50)

    result = drops.classify_drop_status(noisy)

    assert len(result["detail"]) <= drops._MAX_DETAIL_LENGTH


# ── _scrape_all_cards (dedup, skip-on-error) ───────────────────────────

class _FakeCard:
    def __init__(
        self, href: str | None, text: str, raises: bool = False,
        image_url: str | None = None,
    ) -> None:
        self._href = href
        self._text = text
        self._raises = raises
        self.image_url = image_url

    def get_attribute(self, _name: str) -> str | None:
        if self._raises:
            raise RuntimeError("detached element")
        return self._href

    def inner_text(self, timeout: int = 0) -> str:  # noqa: ARG002 - matches Playwright signature
        if self._raises:
            raise RuntimeError("detached element")
        return self._text

    def locator(self, _selector: str) -> "_FakeImgLocator":
        return _FakeImgLocator(self.image_url)


class _FakeImgLocator:
    def __init__(self, image_url: str | None) -> None:
        self._image_url = image_url

    @property
    def first(self) -> "_FakeImgLocator":
        return self

    def get_attribute(self, _name: str, timeout: int = 0) -> str | None:  # noqa: ARG002
        return self._image_url


class _FakeLocator:
    def __init__(self, cards: list[_FakeCard]) -> None:
        self._cards = cards

    def count(self) -> int:
        return len(self._cards)

    def nth(self, index: int) -> _FakeCard:
        return self._cards[index]


class _FakePage:
    def __init__(self, cards: list[_FakeCard]) -> None:
        self._locator = _FakeLocator(cards)

    def locator(self, _selector: str) -> _FakeLocator:
        return self._locator


# ── _scroll_to_load_more ────────────────────────────────────────────────

class _FakeScrollPage:
    """Simulates document.body.scrollHeight growing for a few iterations
    then plateauing, like a real lazy-loading page running out of content."""

    def __init__(self, heights: list[int]) -> None:
        self._heights = heights
        self._call_index = 0
        self.scroll_calls = 0
        self.wait_calls = 0

    def evaluate(self, script: str) -> int:
        if "scrollTo" in script:
            self.scroll_calls += 1
            return None
        height = self._heights[min(self._call_index, len(self._heights) - 1)]
        self._call_index += 1
        return height

    def wait_for_timeout(self, _ms: int) -> None:
        self.wait_calls += 1


def test_scroll_to_load_more_stops_early_once_height_plateaus() -> None:
    # Height grows for 3 reads then stops changing — should stop scrolling
    # well before the _SCROLL_ITERATIONS cap once nothing new loads.
    page = _FakeScrollPage(heights=[1000, 2000, 3000, 3000, 3000, 3000, 3000, 3000, 3000, 3000, 3000])

    drops._scroll_to_load_more(page)

    assert page.scroll_calls < drops._SCROLL_ITERATIONS
    assert page.scroll_calls == 3  # grew at reads 2,3 then plateaued at read 4 -> stop


def test_scroll_to_load_more_caps_at_max_iterations_if_always_growing() -> None:
    ever_growing = [1000 * (i + 2) for i in range(drops._SCROLL_ITERATIONS + 5)]
    page = _FakeScrollPage(heights=ever_growing)

    drops._scroll_to_load_more(page)

    assert page.scroll_calls == drops._SCROLL_ITERATIONS


def test_scroll_to_load_more_swallows_errors_without_raising() -> None:
    class _BrokenPage:
        def evaluate(self, _script: str) -> int:
            raise RuntimeError("navigation happened mid-scroll")

    drops._scroll_to_load_more(_BrokenPage())  # must not raise


def test_scrape_all_cards_dedupes_repeated_hrefs() -> None:
    page = _FakePage([
        _FakeCard("/collection/cheap-shot", CHEAP_SHOT),
        _FakeCard("/collection/cheap-shot", CHEAP_SHOT),  # duplicate
    ])

    result = drops._scrape_all_cards(page)

    assert len(result) == 1
    assert result[0]["collection_slug"] == "cheap-shot"


def test_scrape_all_cards_carries_image_url_through() -> None:
    page = _FakePage([
        _FakeCard(
            "/collection/cheap-shot", CHEAP_SHOT,
            image_url="https://i2c.seadn.io/collection/cheap-shot/image.png?w=2000",
        ),
    ])

    result = drops._scrape_all_cards(page)

    assert result[0]["image_url"] == "https://i2c.seadn.io/collection/cheap-shot/image.png?w=2000"


def test_scrape_all_cards_skips_broken_card_without_raising() -> None:
    page = _FakePage([
        _FakeCard("/collection/cheap-shot", CHEAP_SHOT, raises=True),
        _FakeCard("/collection/godpull", GOD_PULL),
    ])

    result = drops._scrape_all_cards(page)

    assert len(result) == 1
    assert result[0]["collection_slug"] == "godpull"


def test_scrape_all_cards_skips_non_collection_href_without_raising() -> None:
    page = _FakePage([
        _FakeCard("/account/settings", "irrelevant"),
        _FakeCard("/collection/godpull", GOD_PULL),
    ])

    result = drops._scrape_all_cards(page)

    assert len(result) == 1
    assert result[0]["collection_slug"] == "godpull"


# ── get_drops caching ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Point the store module at a fresh temp DB file for every test so tests
    never touch a real/shared DB file."""
    db_path = tmp_path / "opensea_automint_drops_test.db"
    monkeypatch.setattr(store, "_DB", str(db_path))
    yield db_path


@pytest.fixture(autouse=True)
def reset_drops_cache(monkeypatch: pytest.MonkeyPatch):
    """Ensure each test starts with an empty, stale module-level cache."""
    monkeypatch.setitem(drops._cache, "ts", 0.0)
    monkeypatch.setitem(drops._cache, "drops", [])
    yield


def _fake_live_drops() -> list[dict]:
    return [
        {
            "collection_slug": "cheap-shot",
            "name": "Cheap Shot",
            "mint_page_url": "https://opensea.io/collection/cheap-shot",
            "status": "minting_now",
            "status_detail": None,
        }
    ]


def test_get_drops_fetches_and_persists_on_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_calls = []
    upsert_calls = []

    def fake_fetch():
        fetch_calls.append(1)
        return _fake_live_drops()

    def fake_upsert(drop_input):
        upsert_calls.append(drop_input)
        return 1

    monkeypatch.setattr(drops, "fetch_drops_live", fake_fetch)
    monkeypatch.setattr(store, "upsert_tracked_drop", fake_upsert)

    result = drops.get_drops()

    assert len(fetch_calls) == 1
    assert len(upsert_calls) == 1
    assert upsert_calls[0].collection_slug == "cheap-shot"
    assert upsert_calls[0].source == "playwright"
    assert result == _fake_live_drops()


def test_get_drops_uses_cache_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_calls = []

    def fake_fetch():
        fetch_calls.append(1)
        return _fake_live_drops()

    monkeypatch.setattr(drops, "fetch_drops_live", fake_fetch)
    monkeypatch.setattr(store, "upsert_tracked_drop", lambda drop_input: 1)

    drops.get_drops()
    drops.get_drops()

    assert len(fetch_calls) == 1


def test_get_drops_force_refresh_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_calls = []

    def fake_fetch():
        fetch_calls.append(1)
        return _fake_live_drops()

    monkeypatch.setattr(drops, "fetch_drops_live", fake_fetch)
    monkeypatch.setattr(store, "upsert_tracked_drop", lambda drop_input: 1)

    drops.get_drops()
    drops.get_drops(force_refresh=True)

    assert len(fetch_calls) == 2


def test_get_drops_falls_back_to_store_when_live_fetch_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="stored-drop",
        name="Stored Drop",
        contract_address="",
        mint_page_url="https://opensea.io/collection/stored-drop",
        source="playwright",
        stage_data='{"status": "not_minting", "status_detail": null}',
    ))

    monkeypatch.setattr(drops, "fetch_drops_live", lambda: [])

    result = drops.get_drops()

    assert len(result) == 1
    assert result[0]["collection_slug"] == "stored-drop"


# ── to_display_dict ─────────────────────────────────────────────────────

def test_to_display_dict_flattens_minting_now_status() -> None:
    row = {
        "id": 1,
        "collection_slug": "cheap-shot",
        "stage_data": '{"status": "minting_now", "status_detail": null}',
    }

    result = drops.to_display_dict(row)

    assert result["status"] == "minting_now"
    assert result["status_detail"] is None
    assert result["is_publicly_mintable"] is True
    assert result["collection_slug"] == "cheap-shot"


def test_to_display_dict_flattens_upcoming_status_with_detail() -> None:
    row = {
        "id": 2,
        "stage_data": '{"status": "upcoming", "status_detail": "MINT STARTS IN"}',
    }

    result = drops.to_display_dict(row)

    assert result["status"] == "upcoming"
    assert result["status_detail"] == "MINT STARTS IN"
    assert result["is_publicly_mintable"] is False


# ── track_drop_by_slug ────────────────────────────────────────────────────

CONTRACT_ADDRESS = "0x30243a8fa62a7236d897bce6a3a98e8d8cc81db8"


def _mock_details(monkeypatch: pytest.MonkeyPatch, details: dict) -> None:
    monkeypatch.setattr(_collection_details, "get_collection_details", lambda slug: details)


def test_track_drop_by_slug_returns_none_when_no_contract_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_details(monkeypatch, {"name": "GOBBOZ", "contract_address": None, "mint_schedule": []})

    result = drops.track_drop_by_slug("gobbozhq")

    assert result is None


def test_track_drop_by_slug_persists_with_real_name_and_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS, "mint_schedule": [],
    })

    result = drops.track_drop_by_slug("gobbozhq")

    assert result is not None
    assert result["collection_slug"] == "gobbozhq"
    assert result["name"] == "GOBBOZ"
    assert result["contract_address"] == CONTRACT_ADDRESS


def test_track_drop_by_slug_persists_real_image_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression test: stage_data's image_url used to be hardcoded to None
    # regardless of what collection_details actually found, so every
    # manually-tracked (or on-chain-discovered) drop showed no thumbnail on
    # the dashboard grid even when OpenSea's own API had a real image.
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS, "mint_schedule": [],
        "image_url": "https://i2c.seadn.io/collection/gobbozhq/image.png",
    })

    drops.track_drop_by_slug("gobbozhq")
    displayed = drops.to_display_dict(store.get_tracked_drop_by_slug("gobbozhq"))

    assert displayed["image_url"] == "https://i2c.seadn.io/collection/gobbozhq/image.png"


def test_track_drop_by_slug_falls_back_to_slug_when_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_details(monkeypatch, {"name": None, "contract_address": CONTRACT_ADDRESS, "mint_schedule": []})

    result = drops.track_drop_by_slug("gobbozhq")

    assert result["name"] == "gobbozhq"


def test_track_drop_by_slug_status_upcoming_when_stage_has_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future = time.time() + 3600
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS,
        "mint_schedule": [{"name": "GTD", "starts_epoch": future, "starts": "in 1 hour", "ends_epoch": None}],
    })

    result = drops.track_drop_by_slug("gobbozhq")

    stage_data = json.loads(result["stage_data"])
    assert stage_data["status"] == "upcoming"
    assert stage_data["status_detail"] == "in 1 hour"


def test_track_drop_by_slug_status_minting_now_when_stage_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS,
        "mint_schedule": [{
            "name": "Public", "starts_epoch": time.time() - 100,
            "ends_epoch": time.time() + 3600, "starts": "an hour ago",
        }],
    })

    result = drops.track_drop_by_slug("gobbozhq")

    stage_data = json.loads(result["stage_data"])
    assert stage_data["status"] == "minting_now"


def test_track_drop_by_slug_status_not_minting_when_all_stages_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS,
        "mint_schedule": [{
            "name": "Public", "starts_epoch": time.time() - 7200,
            "ends_epoch": time.time() - 3600, "starts": "2 hours ago",
        }],
    })

    result = drops.track_drop_by_slug("gobbozhq")

    stage_data = json.loads(result["stage_data"])
    assert stage_data["status"] == "not_minting"


def test_track_drop_by_slug_raises_value_error_for_invalid_slug() -> None:
    with pytest.raises(ValueError):
        drops.track_drop_by_slug("UPPERCASE")


# ── discover_new_seadrop_collections ─────────────────────────────────────

def test_discover_new_seadrop_collections_tracks_a_newly_seen_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: {
            "updates": [{"nftContract": CONTRACT_ADDRESS, "startTime": 1786374000,
                         "endTime": 1786399200, "mintPriceWei": "1000000000000000"}],
            "scannedToBlock": "25726400",
        },
    )
    monkeypatch.setattr(
        _collection_details, "resolve_slug_from_contract_address", lambda addr: "gobbozhq",
    )
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS, "mint_schedule": [],
    })

    count = drops.discover_new_seadrop_collections()

    assert count == 1
    tracked = store.get_tracked_drop_by_slug("gobbozhq")
    assert tracked is not None
    assert store.get_state("seadrop_last_scanned_block") == "25726400"


def test_discover_new_seadrop_collections_skips_already_tracked_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.upsert_tracked_drop(store.TrackedDropInput(
        collection_slug="gobbozhq", name="GOBBOZ", contract_address=CONTRACT_ADDRESS,
        mint_page_url="https://opensea.io/collection/gobbozhq", source="manual", stage_data="{}",
    ))
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: {
            "updates": [{"nftContract": CONTRACT_ADDRESS, "startTime": 1786374000,
                         "endTime": 1786399200, "mintPriceWei": "1000000000000000"}],
            "scannedToBlock": "25726400",
        },
    )
    resolve_calls = []
    monkeypatch.setattr(
        _collection_details, "resolve_slug_from_contract_address",
        lambda addr: resolve_calls.append(addr) or "gobbozhq",
    )

    count = drops.discover_new_seadrop_collections()

    assert count == 0
    assert resolve_calls == []  # never even attempted to resolve — already tracked


def test_discover_new_seadrop_collections_skips_contract_with_no_resolvable_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: {
            "updates": [{"nftContract": CONTRACT_ADDRESS, "startTime": 1786374000,
                         "endTime": 1786399200, "mintPriceWei": "1000000000000000"}],
            "scannedToBlock": "25726400",
        },
    )
    monkeypatch.setattr(_collection_details, "resolve_slug_from_contract_address", lambda addr: None)

    count = drops.discover_new_seadrop_collections()

    assert count == 0
    assert store.get_tracked_drop_by_contract_address(CONTRACT_ADDRESS) is None


def test_discover_new_seadrop_collections_dedupes_repeated_contract_in_same_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: {
            "updates": [
                {"nftContract": CONTRACT_ADDRESS, "startTime": 1, "endTime": 2, "mintPriceWei": "1"},
                {"nftContract": CONTRACT_ADDRESS.upper().replace("0X", "0x"),
                 "startTime": 3, "endTime": 4, "mintPriceWei": "2"},
            ],
            "scannedToBlock": "25726400",
        },
    )
    resolve_calls = []
    monkeypatch.setattr(
        _collection_details, "resolve_slug_from_contract_address",
        lambda addr: resolve_calls.append(addr) or "gobbozhq",
    )
    _mock_details(monkeypatch, {
        "name": "GOBBOZ", "contract_address": CONTRACT_ADDRESS, "mint_schedule": [],
    })

    count = drops.discover_new_seadrop_collections()

    assert count == 1
    assert len(resolve_calls) == 1  # only resolved once despite two log entries


def test_discover_new_seadrop_collections_uses_stored_scan_bookmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.set_state("seadrop_last_scanned_block", "25726300")
    captured_from_block = []
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: captured_from_block.append(from_block) or {"updates": [], "scannedToBlock": "25726400"},
    )

    drops.discover_new_seadrop_collections()

    assert captured_from_block == ["25726301"]  # one past the last scanned block


def test_discover_new_seadrop_collections_passes_none_from_block_on_first_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_from_block = []
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: captured_from_block.append(from_block) or {"updates": [], "scannedToBlock": "1"},
    )

    drops.discover_new_seadrop_collections()

    assert captured_from_block == [None]


def test_discover_new_seadrop_collections_resets_bookmark_when_no_progress_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bookmark that's fallen too far behind the chain's current tip can
    # hit an unreachable ("archive") range on the free RPC and make zero
    # progress forever otherwise — verified live 2026-08-10. Detected by
    # scannedToBlock coming back BELOW the requested fromBlock; must drop
    # the bookmark rather than get permanently stuck retrying it.
    store.set_state("seadrop_last_scanned_block", "100")
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: {"updates": [], "scannedToBlock": "100"},  # < fromBlock (101)
    )

    count = drops.discover_new_seadrop_collections()

    assert count == 0
    assert store.get_state("seadrop_last_scanned_block") is None


def test_discover_new_seadrop_collections_does_not_reset_bookmark_on_real_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.set_state("seadrop_last_scanned_block", "100")
    monkeypatch.setattr(
        node_client, "get_recent_public_drop_updates",
        lambda from_block: {"updates": [], "scannedToBlock": "150"},  # > fromBlock (101)
    )

    drops.discover_new_seadrop_collections()

    assert store.get_state("seadrop_last_scanned_block") == "150"


def test_discover_new_seadrop_collections_returns_zero_and_does_not_raise_on_node_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_error(from_block):
        raise RuntimeError("Node wallet-helper is not running on port 3456")

    monkeypatch.setattr(node_client, "get_recent_public_drop_updates", raise_error)

    count = drops.discover_new_seadrop_collections()

    assert count == 0
    # Bookmark must NOT advance on a failed scan — nothing was actually
    # confirmed scanned, so the next tick must retry the same range.
    assert store.get_state("seadrop_last_scanned_block") is None


def test_to_display_dict_handles_missing_stage_data() -> None:
    row = {"id": 3, "collection_slug": "no-stage-data"}

    result = drops.to_display_dict(row)

    assert result["status"] == ""
    assert result["status_detail"] is None
    assert result["is_publicly_mintable"] is False


def test_to_display_dict_handles_malformed_stage_data_without_crashing() -> None:
    row = {"id": 4, "stage_data": "not valid json{{{"}

    result = drops.to_display_dict(row)

    assert result["status"] == ""
    assert result["status_detail"] is None
    assert result["is_publicly_mintable"] is False


def test_to_display_dict_does_not_mutate_input_row() -> None:
    row = {"id": 5, "stage_data": '{"status": "minting_now", "status_detail": null}'}
    original = dict(row)

    drops.to_display_dict(row)

    assert row == original


def test_to_display_dict_passes_through_trusted_image_url() -> None:
    row = {
        "id": 6,
        "stage_data": json.dumps({
            "status": "minting_now", "status_detail": None,
            "image_url": "https://i2c.seadn.io/collection/cheap-shot/image.png?w=2000",
        }),
    }

    result = drops.to_display_dict(row)

    assert result["image_url"] == "https://i2c.seadn.io/collection/cheap-shot/image.png?w=2000"


def test_to_display_dict_rejects_untrusted_image_url() -> None:
    row = {
        "id": 7,
        "stage_data": json.dumps({
            "status": "minting_now", "status_detail": None,
            "image_url": "https://evil.example.com/tracker.png",
        }),
    }

    result = drops.to_display_dict(row)

    assert result["image_url"] is None
