import pytest

from opensea_automint import node_client


class _FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


OWNER = "0x" + "a1" * 20
SESSION_ADDRESS = "0x" + "b2" * 20
NFT_CONTRACT = "0x" + "c3" * 20


class _NonJsonResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self) -> dict:
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


# ── verify_owner_signature ────────────────────────────────────────────────

def test_verify_owner_signature_returns_true_for_valid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, {"valid": True})

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.verify_owner_signature(OWNER, "some message", "0x" + "ab" * 65)

    assert result is True
    url, body, _timeout = calls[0]
    assert url.endswith("/eth/verify-owner-signature")
    assert body == {"ownerAddress": OWNER, "message": "some message", "signature": "0x" + "ab" * 65}


def test_verify_owner_signature_returns_false_for_invalid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, {"valid": False}),
    )

    result = node_client.verify_owner_signature(OWNER, "some message", "0x" + "00" * 65)

    assert result is False


def test_verify_owner_signature_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.verify_owner_signature(OWNER, "msg", "0xsig")


def test_verify_owner_signature_raises_runtime_error_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "internal failure"}),
    )

    with pytest.raises(RuntimeError, match="internal failure"):
        node_client.verify_owner_signature(OWNER, "msg", "0xsig")


def test_verify_owner_signature_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.verify_owner_signature(OWNER, "msg", "0xsig")


# ── verify_session_key ───────────────────────────────────────────────────

SESSION_PRIVATE_KEY = "0x" + "cd" * 32


def test_verify_session_key_returns_true_when_key_matches_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, {"valid": True})

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.verify_session_key(SESSION_PRIVATE_KEY, SESSION_ADDRESS)

    assert result is True
    url, body, _timeout = calls[0]
    assert url.endswith("/eth/verify-session-key")
    assert body == {"sessionPrivateKey": SESSION_PRIVATE_KEY, "sessionAddress": SESSION_ADDRESS}


def test_verify_session_key_returns_false_when_key_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, {"valid": False}),
    )

    result = node_client.verify_session_key(SESSION_PRIVATE_KEY, SESSION_ADDRESS)

    assert result is False


def test_verify_session_key_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.verify_session_key(SESSION_PRIVATE_KEY, SESSION_ADDRESS)


def test_verify_session_key_raises_runtime_error_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "internal failure"}),
    )

    with pytest.raises(RuntimeError, match="internal failure"):
        node_client.verify_session_key(SESSION_PRIVATE_KEY, SESSION_ADDRESS)


def test_verify_session_key_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.verify_session_key(SESSION_PRIVATE_KEY, SESSION_ADDRESS)


# ── get_public_drop_window ───────────────────────────────────────────────

def test_get_public_drop_window_returns_window_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, {
            "available": True, "startTime": 1786028400, "endTime": 1787583600,
            "mintPriceWei": "1500000000000000",
        })

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.get_public_drop_window(NFT_CONTRACT)

    assert result == {
        "startTime": 1786028400, "endTime": 1787583600, "mintPriceWei": "1500000000000000",
    }
    url, body, _timeout = calls[0]
    assert url.endswith("/eth/public-drop-window")
    assert body == {"nftContract": NFT_CONTRACT, "chain": "ethereum"}


def test_get_public_drop_window_returns_none_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, {"available": False}),
    )

    result = node_client.get_public_drop_window(NFT_CONTRACT)

    assert result is None


def test_get_public_drop_window_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.get_public_drop_window(NFT_CONTRACT)


def test_get_public_drop_window_raises_runtime_error_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "RPC failure"}),
    )

    with pytest.raises(RuntimeError, match="RPC failure"):
        node_client.get_public_drop_window(NFT_CONTRACT)


def test_get_public_drop_window_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.get_public_drop_window(NFT_CONTRACT)


# ── fire_mint ─────────────────────────────────────────────────────────────

VALUE_CAP_WEI = "50000000000000000"


def test_fire_mint_returns_success_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    success_result = {
        "success": True, "txHash": "0x" + "b" * 64,
        "blockNumber": "12345", "gasUsed": "210000",
    }

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, success_result)

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.fire_mint(SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI)

    assert result == success_result
    url, body, timeout = calls[0]
    assert url.endswith("/eth/fire-mint")
    assert body == {
        "sessionPrivateKey": SESSION_PRIVATE_KEY,
        "nftContract": NFT_CONTRACT,
        "quantity": 1,
        "valueCapWei": VALUE_CAP_WEI,
        "chain": "ethereum",
    }
    assert timeout == 90


def test_fire_mint_returns_failure_result_as_a_normal_dict_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reverted/failed mint attempt is a real, expected outcome (sold out,
    # price changed) — must come back as data, not raise.
    failure_result = {
        "success": False, "txHash": None, "blockNumber": None,
        "gasUsed": None, "error": "price exceeds cap",
    }
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, failure_result),
    )

    result = node_client.fire_mint(SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI)

    assert result == failure_result


def test_fire_mint_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.fire_mint(SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI)


def test_fire_mint_raises_runtime_error_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "gas estimation failed"}),
    )

    with pytest.raises(RuntimeError, match="gas estimation failed"):
        node_client.fire_mint(SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI)


def test_fire_mint_raises_runtime_error_on_non_dict_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.fire_mint(SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI)


# ── fire_signed_mint ──────────────────────────────────────────────────────

MINT_PARAMS = {
    "mintPrice": "1000000000000000", "maxTotalMintableByWallet": "2",
    "startTime": "1786100000", "endTime": "1786200000", "dropStageIndex": "1",
    "maxTokenSupplyForStage": "4696", "feeBps": "0", "restrictFeeRecipients": True,
}
SALT = "12345"
SIGNATURE = "0x" + "cd" * 65


def test_fire_signed_mint_returns_success_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    success_result = {
        "success": True, "txHash": "0x" + "b" * 64,
        "blockNumber": "12345", "gasUsed": "210000",
    }

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, success_result)

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.fire_signed_mint(
        SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI, MINT_PARAMS, SALT, SIGNATURE,
    )

    assert result == success_result
    url, body, timeout = calls[0]
    assert url.endswith("/eth/fire-signed-mint")
    assert body == {
        "sessionPrivateKey": SESSION_PRIVATE_KEY,
        "nftContract": NFT_CONTRACT,
        "quantity": 1,
        "valueCapWei": VALUE_CAP_WEI,
        "mintParams": MINT_PARAMS,
        "salt": SALT,
        "signature": SIGNATURE,
        "chain": "ethereum",
    }
    assert timeout == 90


def test_fire_signed_mint_returns_failure_result_as_a_normal_dict_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure_result = {
        "success": False, "txHash": None, "blockNumber": None,
        "gasUsed": None, "error": "Gas estimation failed (would revert), nothing sent: reverted",
    }
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, failure_result),
    )

    result = node_client.fire_signed_mint(
        SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI, MINT_PARAMS, SALT, SIGNATURE,
    )

    assert result == failure_result


def test_fire_signed_mint_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.fire_signed_mint(
            SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI, MINT_PARAMS, SALT, SIGNATURE,
        )


def test_fire_signed_mint_raises_runtime_error_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "invalid mintParams"}),
    )

    with pytest.raises(RuntimeError, match="invalid mintParams"):
        node_client.fire_signed_mint(
            SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI, MINT_PARAMS, SALT, SIGNATURE,
        )


def test_fire_signed_mint_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.fire_signed_mint(
            SESSION_PRIVATE_KEY, NFT_CONTRACT, 1, VALUE_CAP_WEI, MINT_PARAMS, SALT, SIGNATURE,
        )


# ── get_recent_public_drop_updates ───────────────────────────────────────

def test_get_recent_public_drop_updates_returns_result_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    success_result = {
        "updates": [{"nftContract": NFT_CONTRACT, "startTime": 1786374000, "endTime": 1786399200,
                      "mintPriceWei": "1000000000000000"}],
        "scannedToBlock": "25726400",
    }

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, success_result)

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.get_recent_public_drop_updates("25726300")

    assert result == success_result
    url, body, timeout = calls[0]
    assert url.endswith("/eth/recent-public-drop-updates")
    assert body == {"fromBlock": "25726300"}
    assert timeout == 30


def test_get_recent_public_drop_updates_passes_through_none_from_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: calls.append(json) or _FakeResponse(200, {"updates": [], "scannedToBlock": "1"}),
    )

    node_client.get_recent_public_drop_updates(None)

    assert calls[0] == {"fromBlock": None}


def test_get_recent_public_drop_updates_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.get_recent_public_drop_updates(None)


def test_get_recent_public_drop_updates_raises_runtime_error_on_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "RPC unavailable"}),
    )

    with pytest.raises(RuntimeError, match="RPC unavailable"):
        node_client.get_recent_public_drop_updates(None)


def test_get_recent_public_drop_updates_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.get_recent_public_drop_updates(None)
