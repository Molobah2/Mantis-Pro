"""
Flask Blueprint for the OpenSea Auto-Mint Tool's dashboard + JSON API.

Deliberate one-off exception to the rest of the repo's flat @app.route(...)
convention in agent.py: this feature is self-contained in its own folder so
it doesn't collide with concurrent edits to agent.py or the other
dashboards. Wiring into agent.py (import + app.register_blueprint(...)) is
a later, separate step.
"""
import json
import os

from flask import Blueprint, Response, jsonify, request

from portal_upvote import security as _sec

from . import collection_details, drops, store

opensea_automint_bp = Blueprint("opensea_automint", __name__)

_DASHBOARD_HTML_FILENAME = "opensea_automint_dashboard.html"


def _displayable_drops() -> list[dict]:
    """Tracked drops shaped for the frontend, excluding ones that aren't
    currently or soon minting (already-minted / secondary-market-only
    collections) — the dashboard only shows actionable drops."""
    rows = store.get_tracked_drops()
    shaped = [drops.to_display_dict(row) for row in rows]
    return [d for d in shaped if d["status"] in drops.DISPLAYABLE_STATUSES]


@opensea_automint_bp.route("/api/opensea/drops")
def api_drops() -> Response:
    """Public read-only listing of tracked drops currently or soon minting,
    each shaped by drops.to_display_dict (flattened status/status_detail +
    is_publicly_mintable)."""
    return jsonify({"drops": _displayable_drops()})


@opensea_automint_bp.route("/api/opensea/drops/refresh", methods=["POST"])
@_sec.require_admin
def api_refresh_drops() -> Response:
    """Admin-gated: forces a real Playwright scrape via drops.get_drops(force_refresh=True).
    Not publicly spammable — protected by portal_upvote.security.require_admin.

    Deliberately fails CLOSED (unlike require_admin's default open-if-unset
    fallback) when ADMIN_SECRET isn't configured: this route triggers real
    outbound browser automation against a third party, a higher blast radius
    than most admin routes, so it doesn't inherit the backward-compat-open
    behavior other endpoints in this codebase rely on."""
    if not os.getenv("ADMIN_SECRET", "").strip():
        return jsonify({"error": "ADMIN_SECRET not configured — refresh disabled"}), 503

    drops.get_drops(force_refresh=True)
    shaped = _displayable_drops()
    return jsonify({"drops": shaped, "count": len(shaped)})


_COLLECTION_DETAILS_RATE_LIMIT = 30
_COLLECTION_DETAILS_RATE_WINDOW_SECONDS = 3600
_COLLECTION_DETAILS_RATE_KEY = "collection-details"


@opensea_automint_bp.route("/api/opensea/collection/<slug>")
def api_collection_details(slug: str) -> Response:
    """Public read-only collection detail lookup (description + external
    links), sourced on-demand via a real Playwright page load per unique
    slug — rate-limited per client IP even though it's read-only, since each
    request can trigger real outbound browser automation.

    30 requests/hour per IP is generous given get_collection_details's own
    30-minute per-slug cache already absorbs repeat requests for the SAME
    collection; this limit exists to bound requests across DIFFERENT slugs
    (e.g. someone trying to enumerate/spam many distinct collection pages).

    slug is validated against collection_details.SLUG_RE before ever
    reaching get_collection_details — defense in depth / a clean 400 rather
    than relying solely on the module's own ValueError guard.
    """
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(
        ip, _COLLECTION_DETAILS_RATE_KEY,
        limit=_COLLECTION_DETAILS_RATE_LIMIT,
        window=_COLLECTION_DETAILS_RATE_WINDOW_SECONDS,
    ):
        return jsonify({"error": "Rate limit exceeded — try again later"}), 429

    if not collection_details.SLUG_RE.match(slug):
        return jsonify({"error": "Invalid collection slug"}), 400

    details = collection_details.get_collection_details(slug)
    return jsonify(details)


@opensea_automint_bp.route("/opensea-automint")
def dashboard_page() -> Response:
    """Serves the read-only dashboard HTML. If the request already carries a
    valid admin session cookie, injects the admin secret as a JS global
    (mirrors agent.py's /portal-upvote page-serving pattern) so the page's
    admin-only Refresh button can sign its POST — never exposed to
    anonymous visitors."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html_path = os.path.join(repo_root, _DASHBOARD_HTML_FILENAME)
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    admin_secret = os.getenv("ADMIN_SECRET", "").strip()
    is_admin = _sec.verify_admin_cookie(request.cookies.get(_sec.ADMIN_COOKIE_NAME, ""))
    admin_key = admin_secret if is_admin else ""

    # json.dumps doesn't escape "</" — without this, a secret containing
    # "</script>" could prematurely close the tag and inject markup.
    admin_key_js = json.dumps(admin_key).replace("</", "<\\/")
    injection = (
        f"<script>window.__ADMIN_KEY__={admin_key_js};"
        f"window.__IS_ADMIN__={json.dumps(bool(is_admin))};</script>"
    )
    html = html.replace("</head>", injection + "\n</head>", 1)
    return Response(html, mimetype="text/html")
