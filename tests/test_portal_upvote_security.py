from flask import Flask

from portal_upvote import security


def _app() -> Flask:
    app = Flask(__name__)
    return app


def test_get_client_ip_trusts_the_last_xff_entry_appended_by_railways_proxy() -> None:
    # Railway's edge proxy is the only hop between the internet and this
    # app, and it appends the real connecting client's IP as the LAST
    # entry — everything before that is client-supplied and spoofable.
    app = _app()
    with app.test_request_context(
        "/", headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.9.9.9"}
    ) as ctx:
        assert security.get_client_ip(ctx.request) == "9.9.9.9"


def test_get_client_ip_ignores_a_spoofed_first_entry() -> None:
    # A request can freely set its own X-Forwarded-For first hop — only the
    # entry Railway itself appended (the last one) is trustworthy.
    app = _app()
    with app.test_request_context(
        "/", headers={"X-Forwarded-For": "attacker-controlled, 203.0.113.7"}
    ) as ctx:
        assert security.get_client_ip(ctx.request) == "203.0.113.7"


def test_get_client_ip_handles_single_entry_xff() -> None:
    app = _app()
    with app.test_request_context("/", headers={"X-Forwarded-For": "203.0.113.7"}) as ctx:
        assert security.get_client_ip(ctx.request) == "203.0.113.7"


def test_get_client_ip_falls_back_to_remote_addr_when_no_xff() -> None:
    app = _app()
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "127.0.0.1"}) as ctx:
        assert security.get_client_ip(ctx.request) == "127.0.0.1"
