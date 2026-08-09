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


# ── verify_session_grant ─────────────────────────────────────────────────

SERIALIZED_APPROVAL = "a" * 3300


def test_verify_session_grant_returns_true_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, {"valid": True})

    monkeypatch.setattr(node_client._req, "post", fake_post)

    valid, reason = node_client.verify_session_grant(SERIALIZED_APPROVAL, OWNER, SMART_ACCOUNT)

    assert valid is True
    assert reason is None
    assert len(calls) == 1
    url, body, _timeout = calls[0]
    assert url.endswith("/eth/verify-session-grant")
    assert body == {
        "serializedApproval": SERIALIZED_APPROVAL,
        "ownerAddress": OWNER,
        "smartAccountAddress": SMART_ACCOUNT,
    }


def test_verify_session_grant_returns_false_with_reason_on_legitimate_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        return _FakeResponse(
            400, {"error": "Approval does not resolve to the claimed smart account address"}
        )

    monkeypatch.setattr(node_client._req, "post", fake_post)

    valid, reason = node_client.verify_session_grant(SERIALIZED_APPROVAL, OWNER, SMART_ACCOUNT)

    assert valid is False
    assert reason == "Approval does not resolve to the claimed smart account address"


def test_verify_session_grant_raises_runtime_error_on_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        return _FakeResponse(500, {"error": "internal failure"})

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="internal failure"):
        node_client.verify_session_grant(SERIALIZED_APPROVAL, OWNER, SMART_ACCOUNT)


def test_verify_session_grant_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.verify_session_grant(SERIALIZED_APPROVAL, OWNER, SMART_ACCOUNT)


def test_verify_session_grant_raises_runtime_error_on_non_dict_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        return _FakeResponse(200, ["unexpected"])  # type: ignore[arg-type]

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.verify_session_grant(SERIALIZED_APPROVAL, OWNER, SMART_ACCOUNT)


# ── get_public_drop_window ───────────────────────────────────────────────

NFT_CONTRACT = "0x" + "c3" * 20


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
    assert body == {"nftContract": NFT_CONTRACT}


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

DECRYPTED_APPROVAL = "b" * 3300
VALUE_CAP_WEI = "50000000000000000"


def test_fire_mint_returns_success_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    success_result = {
        "success": True, "userOpHash": "0x" + "a" * 64, "txHash": "0x" + "b" * 64,
        "blockNumber": "12345", "gasUsed": "210000",
    }

    def fake_post(url: str, json: dict, timeout: int) -> _FakeResponse:
        calls.append((url, json, timeout))
        return _FakeResponse(200, success_result)

    monkeypatch.setattr(node_client._req, "post", fake_post)

    result = node_client.fire_mint(DECRYPTED_APPROVAL, NFT_CONTRACT, SMART_ACCOUNT, 1, VALUE_CAP_WEI)

    assert result == success_result
    url, body, timeout = calls[0]
    assert url.endswith("/eth/fire-mint")
    assert body == {
        "serializedApproval": DECRYPTED_APPROVAL,
        "nftContract": NFT_CONTRACT,
        "smartAccountAddress": SMART_ACCOUNT,
        "quantity": 1,
        "valueCapWei": VALUE_CAP_WEI,
    }
    assert timeout == 90


def test_fire_mint_returns_failure_result_as_a_normal_dict_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reverted/failed mint attempt is a real, expected outcome (sold out,
    # price changed) — must come back as data, not raise.
    failure_result = {
        "success": False, "userOpHash": "", "txHash": None, "blockNumber": None,
        "gasUsed": None, "error": "price exceeds cap",
    }
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, failure_result),
    )

    result = node_client.fire_mint(DECRYPTED_APPROVAL, NFT_CONTRACT, SMART_ACCOUNT, 1, VALUE_CAP_WEI)

    assert result == failure_result


def test_fire_mint_raises_runtime_error_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, json: dict, timeout: int):
        raise node_client._req.exceptions.ConnectionError()

    monkeypatch.setattr(node_client._req, "post", fake_post)

    with pytest.raises(RuntimeError, match="Node wallet-helper is not running on port 3456"):
        node_client.fire_mint(DECRYPTED_APPROVAL, NFT_CONTRACT, SMART_ACCOUNT, 1, VALUE_CAP_WEI)


def test_fire_mint_raises_runtime_error_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(500, {"error": "ZERODEV_PROJECT_ID is not configured"}),
    )

    with pytest.raises(RuntimeError, match="ZERODEV_PROJECT_ID is not configured"):
        node_client.fire_mint(DECRYPTED_APPROVAL, NFT_CONTRACT, SMART_ACCOUNT, 1, VALUE_CAP_WEI)


def test_fire_mint_raises_runtime_error_on_non_dict_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        node_client._req, "post",
        lambda url, json, timeout: _FakeResponse(200, ["unexpected"]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="unexpected response shape"):
        node_client.fire_mint(DECRYPTED_APPROVAL, NFT_CONTRACT, SMART_ACCOUNT, 1, VALUE_CAP_WEI)


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
