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

from flask import Blueprint, Response, jsonify, request, send_file

from portal_upvote import security as _sec
from wallet_crypto import encrypt_secret

from . import collection_details, drops, firing, messages, node_client, security, store

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


_SESSION_GRANT_RATE_LIMIT = 10
_SESSION_GRANT_RATE_WINDOW_SECONDS = 3600
_SESSION_GRANT_RATE_KEY = "session-grant"


@opensea_automint_bp.route("/api/opensea/session-grant", methods=["POST"])
def api_session_grant() -> Response:
    """Stores an owner-authorized session key for auto-firing a specific
    collection. The session key is a plain, fundable EOA — this project
    fires a direct signed transaction from it (not through a sponsored
    smart account), so it needs to hold real ETH of its own. There is NO
    on-chain enforcement of quantity/price/target-contract limits in this
    model; this grant record (and firing.py's pre-fire checks) is the only
    safety net. Rate-limited per IP (this writes to the DB and does real
    encryption work, and each grant is a real cryptographic commitment even
    though nothing can fire yet)."""
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(
        ip, _SESSION_GRANT_RATE_KEY,
        limit=_SESSION_GRANT_RATE_LIMIT,
        window=_SESSION_GRANT_RATE_WINDOW_SECONDS,
    ):
        return jsonify({"error": "Rate limit exceeded — try again later"}), 429

    # silent=True avoids a raw Werkzeug 400 for a malformed/missing body
    # before our own validation (with its clearer error message) runs.
    body = request.get_json(silent=True) or {}

    validation_error = security.validate_session_grant_input(body)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    owner_address = body["ownerAddress"].lower()
    session_address = body["sessionAddress"].lower()
    nft_contract = body["nftContract"].lower()

    if not firing.is_signature_timestamp_fresh(body["timestamp"]):
        return jsonify({"error": "Signature has expired — please try again"}), 401

    # Proves the OWNER actually authorized this specific grant (address is
    # public, so format validation alone can't catch a spoofed claim).
    message = messages.build_grant_message(
        session_address, nft_contract, body["maxQuantity"], body["valueCapWei"],
        body["expiresAt"], body["timestamp"],
    )
    try:
        owner_signature_valid = node_client.verify_owner_signature(
            owner_address, message, body["signature"],
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    if not owner_signature_valid:
        return jsonify({"error": "Invalid signature — could not verify wallet ownership"}), 401

    # Proves the submitted sessionPrivateKey actually corresponds to the
    # claimed sessionAddress — without this, a malformed/mismatched client
    # could store a key that can never mint as the address the owner just
    # authorized (a functional bug, not an authorization bypass, but one
    # this check catches before it ever reaches encryption/DB either way).
    try:
        session_key_valid = node_client.verify_session_key(
            body["sessionPrivateKey"], session_address,
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    if not session_key_valid:
        return jsonify({"error": "sessionPrivateKey does not correspond to sessionAddress"}), 400

    permission_config = json.dumps({
        "functionName": "mintPublic",
        "maxQuantity": body["maxQuantity"],
    })
    allowed_targets = json.dumps([nft_contract])

    try:
        encrypted_session_key = encrypt_secret(body["sessionPrivateKey"])
    except ValueError:
        # encrypt_secret's ValueError is raised when SESSION_KEY_ENCRYPTION_KEY
        # is unset/malformed in this environment — an operator misconfiguration,
        # not something to leak details about to the caller.
        return jsonify({"error": "Session grant storage is temporarily unavailable"}), 500

    # Only one grant is meant to be "active" per owner at a time (see
    # store.get_active_session_grant) — revoke any prior ones at WRITE time
    # rather than relying solely on "newest row wins" at read time, so a
    # bulk/admin/reconciliation query can't mistake a superseded grant for
    # a live one.
    prior = store.get_active_session_grant(owner_address)
    if prior:
        store.revoke_session_grant(prior["id"])

    grant_id = store.insert_session_grant(store.SessionGrantInput(
        owner_address=owner_address,
        session_address=session_address,
        encrypted_session_key=encrypted_session_key,
        permission_config=permission_config,
        allowed_targets=allowed_targets,
        value_cap_wei=str(int(body["valueCapWei"])),
        expires_at=float(body["expiresAt"]),
    ))

    return jsonify({"grantId": grant_id, "sessionAddress": session_address}), 200


_REVOKE_GRANT_RATE_LIMIT = 20
_REVOKE_GRANT_RATE_WINDOW_SECONDS = 3600
_REVOKE_GRANT_RATE_KEY = "revoke-grant"


@opensea_automint_bp.route("/api/opensea/session-grant/<int:grant_id>/revoke", methods=["POST"])
def api_revoke_grant(grant_id: int) -> Response:
    """Revokes a session grant on demand — only for the owner who created
    it. This ONLY stops the app from using that key for future arm/fire
    actions (see firing.revoke_grant's docstring) — it has no on-chain
    effect and does not sweep any ETH still sitting at the session address.

    Requires the same signature-based proof of ownership as api_cancel_arm
    — a grant id is a small, sequential, otherwise-guessable integer, so
    without this, anyone could revoke any wallet's grant just by knowing
    its id."""
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(
        ip, _REVOKE_GRANT_RATE_KEY,
        limit=_REVOKE_GRANT_RATE_LIMIT, window=_REVOKE_GRANT_RATE_WINDOW_SECONDS,
    ):
        return jsonify({"error": "Rate limit exceeded — try again later"}), 429

    body = request.get_json(silent=True) or {}

    validation_error = security.validate_revoke_grant_input(body)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    if not firing.is_signature_timestamp_fresh(body["timestamp"]):
        return jsonify({"error": "Signature has expired — please try again"}), 401

    message = messages.build_revoke_grant_message(grant_id, body["timestamp"])
    try:
        signature_valid = node_client.verify_owner_signature(
            body["ownerAddress"], message, body["signature"],
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    if not signature_valid:
        return jsonify({"error": "Invalid signature — could not verify wallet ownership"}), 401

    result = firing.revoke_grant(grant_id, body["ownerAddress"])
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


_ARM_RATE_LIMIT = 10
_ARM_RATE_WINDOW_SECONDS = 3600
_ARM_RATE_KEY = "arm-drop"


@opensea_automint_bp.route("/api/opensea/arm", methods=["POST"])
def api_arm_drop() -> Response:
    """Arms a drop for auto-firing, using the caller's already-granted
    session-key permission (see api_session_grant above). This route only
    ever creates a DB row describing INTENT to fire later — it never
    touches node_client.fire_mint itself. The actual firing happens
    asynchronously from firing.check_and_fire_armed_requests, driven by a
    scheduled job (see agent.py), once the drop's real on-chain public
    stage goes live.

    ownerAddress alone is NOT proof of identity — Ethereum addresses are
    public — so this requires a real EOA signature over the exact arm
    parameters (see messages.build_arm_message), verified against the chain
    via node_client.verify_owner_signature, before ever touching
    firing.arm_drop. Without this, anyone who knew a victim's address could
    arm a drop on their behalf using the victim's already-granted permission."""
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(
        ip, _ARM_RATE_KEY, limit=_ARM_RATE_LIMIT, window=_ARM_RATE_WINDOW_SECONDS,
    ):
        return jsonify({"error": "Rate limit exceeded — try again later"}), 429

    body = request.get_json(silent=True) or {}

    validation_error = security.validate_arm_input(body)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    if not firing.is_signature_timestamp_fresh(body["timestamp"]):
        return jsonify({"error": "Signature has expired — please try again"}), 401

    message = messages.build_arm_message(
        body["collectionSlug"], body["quantity"], body["maxPriceWei"], body["timestamp"],
    )
    try:
        signature_valid = node_client.verify_owner_signature(
            body["ownerAddress"], message, body["signature"],
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    if not signature_valid:
        return jsonify({"error": "Invalid signature — could not verify wallet ownership"}), 401

    result = firing.arm_drop(
        body["ownerAddress"], body["collectionSlug"], body["quantity"], body["maxPriceWei"],
    )
    if "error" in result:
        # "already armed" still carries the existing armId — 409 Conflict
        # fits better than a flat 400 for that specific case.
        status = 409 if "armId" in result else 400
        return jsonify(result), status

    return jsonify(result), 200


_ARM_FOR_DROP_RATE_LIMIT = 60
_ARM_FOR_DROP_RATE_WINDOW_SECONDS = 3600
_ARM_FOR_DROP_RATE_KEY = "arm-for-drop"


@opensea_automint_bp.route("/api/opensea/arm/for-drop")
def api_get_arm_for_drop() -> Response:
    """Whether the caller already has an active arm request for a given
    drop, plus its mint attempts so far — lets the dashboard show live
    status without needing to know an arm request's internal id.

    Deliberately NOT signature-gated like api_arm_drop/api_cancel_arm:
    this is polled repeatedly while a modal is open, and requiring a wallet
    signature prompt on every poll would be unusable. This does mean a
    caller who knows a wallet's address can see that wallet's arm
    status/attempt history (quantity, price, tx hashes) — an accepted,
    lower-severity read-only information exposure (no funds or actions are
    at risk), bounded by rate limiting rather than eliminated."""
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(
        ip, _ARM_FOR_DROP_RATE_KEY, limit=_ARM_FOR_DROP_RATE_LIMIT, window=_ARM_FOR_DROP_RATE_WINDOW_SECONDS,
    ):
        return jsonify({"error": "Rate limit exceeded — try again later"}), 429

    owner = request.args.get("owner", "")
    if not security.ETH_ADDR_RE.match(owner):
        return jsonify({"error": "Invalid owner address"}), 400

    collection_slug = request.args.get("collectionSlug", "")
    if not collection_details.SLUG_RE.match(collection_slug):
        return jsonify({"error": "Invalid collection slug"}), 400

    result = firing.get_arm_status_for_drop(owner, collection_slug)
    return jsonify(result if result else {"arm": None, "attempts": []})


_CANCEL_ARM_RATE_LIMIT = 20
_CANCEL_ARM_RATE_WINDOW_SECONDS = 3600
_CANCEL_ARM_RATE_KEY = "cancel-arm"


@opensea_automint_bp.route("/api/opensea/arm/<int:arm_id>/cancel", methods=["POST"])
def api_cancel_arm(arm_id: int) -> Response:
    """Cancels an arm request before it fires. Only the owner who created
    it can cancel it; already-fired/succeeded/failed requests can't be
    un-fired and are rejected by firing.cancel_arm.

    Requires the same signature-based proof of ownership as api_arm_drop
    (see its docstring) — arm_id is a small, sequential, otherwise-guessable
    integer, so without this, anyone could cancel any wallet's armed
    request (e.g. seconds before a hyped drop opens) just by knowing its id."""
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(
        ip, _CANCEL_ARM_RATE_KEY, limit=_CANCEL_ARM_RATE_LIMIT, window=_CANCEL_ARM_RATE_WINDOW_SECONDS,
    ):
        return jsonify({"error": "Rate limit exceeded — try again later"}), 429

    body = request.get_json(silent=True) or {}

    validation_error = security.validate_cancel_input(body)
    if validation_error:
        return jsonify({"error": validation_error}), 400

    if not firing.is_signature_timestamp_fresh(body["timestamp"]):
        return jsonify({"error": "Signature has expired — please try again"}), 401

    message = messages.build_cancel_message(arm_id, body["timestamp"])
    try:
        signature_valid = node_client.verify_owner_signature(
            body["ownerAddress"], message, body["signature"],
        )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 502
    if not signature_valid:
        return jsonify({"error": "Invalid signature — could not verify wallet ownership"}), 401

    result = firing.cancel_arm(arm_id, body["ownerAddress"])
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result), 200


@opensea_automint_bp.route("/opensea-automint/eth-connect.bundle.js")
def eth_connect_bundle() -> Response:
    """Serve the pre-built Ethereum wallet-connect React bundle (built by
    wallet-helper/connect-src's build.mjs) — mirrors agent.py's existing
    /portal-upvote/connect.bundle.js route for the AGW bundle."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundle_path = os.path.join(repo_root, "wallet-helper", "dist", "eth-connect.bundle.js")
    if not os.path.exists(bundle_path):
        return Response(
            "/* eth-connect bundle not built */", status=404,
            mimetype="application/javascript",
        )
    return send_file(bundle_path, mimetype="application/javascript", max_age=0)


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
