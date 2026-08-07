import pytest

from opensea_automint import drops, store

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


def test_classify_long_countdown_detail_is_truncated() -> None:
    noisy = "Some Drop\nMINT STARTS IN\n" + "\n".join(["1234567890"] * 50)

    result = drops.classify_drop_status(noisy)

    assert len(result["detail"]) <= drops._MAX_DETAIL_LENGTH


# ── _scrape_all_cards (dedup, skip-on-error) ───────────────────────────

class _FakeCard:
    def __init__(self, href: str | None, text: str, raises: bool = False) -> None:
        self._href = href
        self._text = text
        self._raises = raises

    def get_attribute(self, _name: str) -> str | None:
        if self._raises:
            raise RuntimeError("detached element")
        return self._href

    def inner_text(self, timeout: int = 0) -> str:  # noqa: ARG002 - matches Playwright signature
        if self._raises:
            raise RuntimeError("detached element")
        return self._text


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
