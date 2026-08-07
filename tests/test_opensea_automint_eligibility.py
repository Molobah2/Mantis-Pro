from opensea_automint import eligibility


def test_is_publicly_mintable_true_when_status_is_minting_now() -> None:
    drop = {"stage_data": '{"status": "minting_now", "status_detail": null}'}

    assert eligibility.is_publicly_mintable(drop) is True


def test_is_publicly_mintable_false_when_status_is_upcoming() -> None:
    drop = {"stage_data": '{"status": "upcoming", "status_detail": "MINT STARTS IN"}'}

    assert eligibility.is_publicly_mintable(drop) is False


def test_is_publicly_mintable_false_when_status_is_not_minting() -> None:
    drop = {"stage_data": '{"status": "not_minting", "status_detail": null}'}

    assert eligibility.is_publicly_mintable(drop) is False


def test_is_publicly_mintable_false_when_stage_data_missing() -> None:
    drop = {}

    assert eligibility.is_publicly_mintable(drop) is False


def test_is_publicly_mintable_false_when_stage_data_is_none() -> None:
    drop = {"stage_data": None}

    assert eligibility.is_publicly_mintable(drop) is False


def test_is_publicly_mintable_false_when_stage_data_is_malformed_json() -> None:
    drop = {"stage_data": "not valid json{{{"}

    assert eligibility.is_publicly_mintable(drop) is False


def test_is_publicly_mintable_false_when_stage_data_is_not_a_json_object() -> None:
    drop = {"stage_data": '"just a string"'}

    assert eligibility.is_publicly_mintable(drop) is False
