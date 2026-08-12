import pytest

from opensea_automint import opensea_session


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data=None, raise_json_error: bool = False) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("Expecting value")
        return self._json_data


OWNER = "0xf24ab4d6b6e151cc9097c82d2f53c5390ced2754"
SLUG = "gobbozhq"


# ── is_configured ─────────────────────────────────────────────────────────

def test_is_configured_true_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "some=cookie; other=value")
    assert opensea_session.is_configured() is True


def test_is_configured_false_when_env_var_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSEA_SESSION_COOKIE", raising=False)
    assert opensea_session.is_configured() is False


def test_is_configured_false_when_env_var_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "   ")
    assert opensea_session.is_configured() is False


# ── fetch_drop_eligibility ────────────────────────────────────────────────

def test_fetch_drop_eligibility_returns_none_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENSEA_SESSION_COOKIE", raising=False)
    calls = []
    monkeypatch.setattr(
        opensea_session._req, "get", lambda *a, **k: calls.append(1) or _FakeResponse()
    )

    result = opensea_session.fetch_drop_eligibility(SLUG, OWNER)

    assert result is None
    assert calls == []  # never even attempted a request


def test_fetch_drop_eligibility_returns_stages_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    stages = [
        {"stageType": "SIGNED_PRESALE", "stageIndex": 1, "isEligible": True},
        {"stageType": "PUBLIC_SALE", "stageIndex": 0, "isEligible": True},
    ]
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(200, {"data": {"dropBySlug": {"stages": stages}}})

    monkeypatch.setattr(opensea_session._req, "get", fake_get)

    result = opensea_session.fetch_drop_eligibility(SLUG, OWNER)

    assert result == stages
    assert captured["url"] == opensea_session._GRAPHQL_URL
    assert captured["params"]["operationName"] == "DropEligibilityQuery"
    assert OWNER.lower() in captured["params"]["variables"]
    assert SLUG in captured["params"]["variables"]
    assert captured["headers"]["cookie"] == "access_token=fake"


def test_fetch_drop_eligibility_lowercases_owner_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    captured = {}
    mixed_case_owner = "0xF24ab4d6b6e151cc9097c82d2f53c5390ced2754"

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(200, {"data": {"dropBySlug": {"stages": []}}})

    monkeypatch.setattr(opensea_session._req, "get", fake_get)

    opensea_session.fetch_drop_eligibility(SLUG, mixed_case_owner)

    assert OWNER.lower() in captured["params"]["variables"]
    assert mixed_case_owner not in captured["params"]["variables"]


def test_fetch_drop_eligibility_returns_none_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")

    def raise_error(*a, **k):
        raise opensea_session._req.exceptions.ConnectionError()

    monkeypatch.setattr(opensea_session._req, "get", raise_error)

    assert opensea_session.fetch_drop_eligibility(SLUG, OWNER) is None


def test_fetch_drop_eligibility_returns_none_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(401, {"error": "unauthorized"}),
    )

    assert opensea_session.fetch_drop_eligibility(SLUG, OWNER) is None


def test_fetch_drop_eligibility_returns_none_on_non_json_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, raise_json_error=True),
    )

    assert opensea_session.fetch_drop_eligibility(SLUG, OWNER) is None


def test_fetch_drop_eligibility_returns_none_on_graphql_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {"errors": [{"message": "not authenticated"}]}),
    )

    assert opensea_session.fetch_drop_eligibility(SLUG, OWNER) is None


def test_fetch_drop_eligibility_returns_none_when_drop_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {"data": {"dropBySlug": None}}),
    )

    assert opensea_session.fetch_drop_eligibility(SLUG, OWNER) is None


def test_fetch_drop_eligibility_returns_none_when_stages_not_a_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {"data": {"dropBySlug": {"stages": "not-a-list"}}}),
    )

    assert opensea_session.fetch_drop_eligibility(SLUG, OWNER) is None


# ── fetch_signed_mint_authorization (not yet implemented) ────────────────

def test_fetch_signed_mint_authorization_always_returns_none_for_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    result = opensea_session.fetch_signed_mint_authorization(SLUG, OWNER, 1)
    assert result is None


# ── fetch_mint_transaction_data ───────────────────────────────────────────

CONTRACT_ADDRESS = "0x7051bb35ceaa446ef4176544279c72b70c131ac4"

# Shaped exactly like the real response captured live 2026-08-12 from a
# GTD allowlist mint (NUMBERS on Robinhood Chain) — trimmed to the fields
# fetch_mint_transaction_data actually reads.
_REAL_MINT_ACTION_RESPONSE = {
    "data": {
        "swap": {
            "actions": [
                {
                    "__typename": "MintAction",
                    "relayerFulfillment": None,
                    "transactionSubmissionData": {
                        "chain": {"networkId": 4663, "identifier": "robinhood"},
                        "to": "0x00005ea00ac477b1030ce78506496e8c2de24bf5",
                        "data": "0x4b61cd6f0000000000000000000000007051bb35ceaa446ef4176544279c72b70c131ac4",
                        "value": "0",
                    },
                }
            ],
            "errors": [],
        }
    }
}


def test_fetch_mint_transaction_data_returns_none_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENSEA_SESSION_COOKIE", raising=False)
    calls = []
    monkeypatch.setattr(
        opensea_session._req, "get", lambda *a, **k: calls.append(1) or _FakeResponse()
    )

    result = opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 2, "robinhood")

    assert result is None
    assert calls == []


def test_fetch_mint_transaction_data_returns_transaction_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(200, _REAL_MINT_ACTION_RESPONSE)

    monkeypatch.setattr(opensea_session._req, "get", fake_get)

    result = opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 2, "robinhood")

    assert result == {
        "to": "0x00005ea00ac477b1030ce78506496e8c2de24bf5",
        "data": "0x4b61cd6f0000000000000000000000007051bb35ceaa446ef4176544279c72b70c131ac4",
        "valueWei": "0",
    }
    assert captured["url"] == opensea_session._GRAPHQL_URL
    assert captured["params"]["operationName"] == "MintActionTimelineQuery"
    assert OWNER.lower() in captured["params"]["variables"]
    assert CONTRACT_ADDRESS in captured["params"]["variables"]
    assert '"quantity":"2"' in captured["params"]["variables"]
    assert '"chain":"robinhood"' in captured["params"]["variables"]
    assert captured["headers"]["cookie"] == "access_token=fake"


def test_fetch_mint_transaction_data_lowercases_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    captured = {}
    mixed_case_owner = "0xF24ab4d6b6e151cc9097c82d2f53c5390ced2754"
    mixed_case_contract = "0x7051BB35cEaa446eF4176544279C72B70c131ac4"

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse(200, _REAL_MINT_ACTION_RESPONSE)

    monkeypatch.setattr(opensea_session._req, "get", fake_get)

    opensea_session.fetch_mint_transaction_data(mixed_case_owner, mixed_case_contract, 1, "ethereum")

    assert mixed_case_owner not in captured["params"]["variables"]
    assert mixed_case_contract not in captured["params"]["variables"]
    assert OWNER.lower() in captured["params"]["variables"]
    assert CONTRACT_ADDRESS in captured["params"]["variables"]


def test_fetch_mint_transaction_data_returns_none_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")

    def raise_error(*a, **k):
        raise opensea_session._req.exceptions.ConnectionError()

    monkeypatch.setattr(opensea_session._req, "get", raise_error)

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None


def test_fetch_mint_transaction_data_returns_none_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(401, {"error": "unauthorized"}),
    )

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None


def test_fetch_mint_transaction_data_returns_none_when_swap_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {"data": {}}),
    )

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None


def test_fetch_mint_transaction_data_returns_none_when_swap_has_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {
            "data": {"swap": {"actions": [], "errors": [{"message": "not eligible"}]}},
        }),
    )

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None


def test_fetch_mint_transaction_data_returns_none_when_no_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {"data": {"swap": {"actions": [], "errors": []}}}),
    )

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None


def test_fetch_mint_transaction_data_returns_none_when_action_not_mint_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {
            "data": {"swap": {"actions": [{"__typename": "ApprovalAction"}], "errors": []}},
        }),
    )

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None


def test_fetch_mint_transaction_data_returns_none_when_transaction_data_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSEA_SESSION_COOKIE", "access_token=fake")
    monkeypatch.setattr(
        opensea_session._req, "get",
        lambda *a, **k: _FakeResponse(200, {
            "data": {"swap": {
                "actions": [{"__typename": "MintAction", "transactionSubmissionData": None}],
                "errors": [],
            }},
        }),
    )

    assert opensea_session.fetch_mint_transaction_data(OWNER, CONTRACT_ADDRESS, 1, "ethereum") is None
