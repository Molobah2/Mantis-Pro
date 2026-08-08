import pytest

from opensea_automint import node_client


class _FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


OWNER = "0x" + "a1" * 20
SMART_ACCOUNT = "0x" + "b2" * 20


def test_get_smart_account_address_returns_address_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, {"ownerAddress": OWNER, "smartAccountAddress": SMART_ACCOUNT})

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.get_smart_account_address(OWNER)

    assert result == SMART_ACCOUNT
    assert len(calls) == 1
    url, body, _timeout = calls[0]
    assert url.endswith("/eth/smart-account-address")
    assert body == {"ownerAddress": OWNER}


def test_get_smart_account_address_raises_runtime_error_on_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        return _FakeResponse(500, {"error": "derivation failed"})

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="derivation failed"):
        node_client.get_smart_account_address(OWNER)


def test_get_smart_account_address_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.get_smart_account_address(OWNER)


def test_get_smart_account_address_raises_runtime_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.Timeout()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="timed out"):
        node_client.get_smart_account_address(OWNER)


class _NonJsonResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self) -> dict:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def test_get_smart_account_address_raises_runtime_error_on_non_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int) -> _NonJsonResponse:
        return _NonJsonResponse(502)

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="non-JSON response"):
        node_client.get_smart_account_address(OWNER)


def test_get_smart_account_address_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        return _FakeResponse(200, ["unexpected", "list"])  # type: ignore[arg-type]

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.get_smart_account_address(OWNER)
