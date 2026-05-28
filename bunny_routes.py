"""
bunny_routes.py — Bunny Button Flask routes for Mantis Pro
-----------------------------------------------------------
Add to agent.py:
    from bunny_routes import register_bunny_routes
    register_bunny_routes(app)

Then set the env var:
    BUNNY_SESSION_COOKIE=<your __Host-bunny-button-auth cookie value>

How to get the cookie:
    1. Go to bunnybutton.xyz and sign in with your wallet
    2. Open DevTools → Application → Cookies → www.bunnybutton.xyz
    3. Copy the value of __Host-bunny-button-auth
    4. Paste it into Railway as the BUNNY_SESSION_COOKIE env var
"""

import os
import json
from flask import Blueprint, jsonify, request
from bunny_button import BunnyButton, RateLimitError, AuthError

bunny_bp = Blueprint("bunny", __name__, url_prefix="/bunny")

def _client(use_session=False):
    """Returns a BunnyButton client, optionally with session cookie from env."""
    cookie = os.environ.get("BUNNY_SESSION_COOKIE") if use_session else None
    return BunnyButton(session_cookie=cookie)

def _err(msg, status=400):
    return jsonify({"error": msg}), status

def _handle(fn):
    """Wraps a route handler with standard error handling."""
    try:
        return jsonify(fn())
    except RateLimitError as e:
        return _err(str(e), 429)
    except AuthError as e:
        return _err(str(e), 401)
    except Exception as e:
        return _err(str(e), 500)

# ── PUBLIC ROUTES ──────────────────────────────────────────────────────────

@bunny_bp.route("/leaderboard")
def leaderboard():
    """GET /bunny/leaderboard?limit=25"""
    limit = request.args.get("limit", 25, type=int)
    return _handle(lambda: {
        "leaderboard": _client().get_leaderboard(limit=limit)
    })

@bunny_bp.route("/eth-price")
def eth_price():
    """GET /bunny/eth-price"""
    return _handle(lambda: _client().get_eth_price())

@bunny_bp.route("/presale")
def presale():
    """GET /bunny/presale?wallet=0x..."""
    wallet = request.args.get("wallet")
    return _handle(lambda: _client().get_presale_status(wallet=wallet))

@bunny_bp.route("/parties")
def parties():
    """GET /bunny/parties"""
    return _handle(lambda: _client().get_party_list())

@bunny_bp.route("/leaderboard/summary")
def leaderboard_summary():
    """GET /bunny/leaderboard/summary?limit=10 — formatted text leaderboard"""
    limit = request.args.get("limit", 10, type=int)
    return _handle(lambda: {
        "summary": _client().leaderboard_summary(limit=limit)
    })

# ── SESSION ROUTES (require BUNNY_SESSION_COOKIE env var) ──────────────────

@bunny_bp.route("/player")
def player():
    """GET /bunny/player"""
    return _handle(lambda: _client(use_session=True).get_player())

@bunny_bp.route("/inventory")
def inventory():
    """GET /bunny/inventory"""
    return _handle(lambda: _client(use_session=True).get_inventory())

@bunny_bp.route("/quests")
def quests():
    """GET /bunny/quests"""
    return _handle(lambda: _client(use_session=True).get_quests())

@bunny_bp.route("/streaks")
def streaks():
    """GET /bunny/streaks"""
    return _handle(lambda: _client(use_session=True).get_streaks())

@bunny_bp.route("/farm")
def farm():
    """GET /bunny/farm"""
    return _handle(lambda: _client(use_session=True).get_farm())

@bunny_bp.route("/stake")
def stake():
    """GET /bunny/stake"""
    return _handle(lambda: _client(use_session=True).get_stake())

@bunny_bp.route("/referral")
def referral():
    """GET /bunny/referral"""
    return _handle(lambda: _client(use_session=True).get_referral())

@bunny_bp.route("/crates")
def crates():
    """GET /bunny/crates"""
    return _handle(lambda: _client(use_session=True).get_crate_history())

# ── COMPUTED / SMART ROUTES ────────────────────────────────────────────────

@bunny_bp.route("/energy")
def energy():
    """
    GET /bunny/energy
    Returns energy %, time to full, and whether to go play now.
    """
    return _handle(lambda: _client(use_session=True).energy_status())

@bunny_bp.route("/farm/warning")
def farm_warning():
    """
    GET /bunny/farm/warning
    Returns farm state + whether you're within 1hr of hitting the 8h offline cap.
    """
    return _handle(lambda: _client(use_session=True).farm_cap_warning())

@bunny_bp.route("/stats")
def stats():
    """
    GET /bunny/stats
    Combined breed + class + gear effective stats in one call.
    """
    return _handle(lambda: _client(use_session=True).effective_stats())

@bunny_bp.route("/report")
def report():
    """
    GET /bunny/report
    Full player snapshot: all endpoints merged into one response.
    Good for the agent session loop to read at startup.
    """
    return _handle(lambda: _client(use_session=True).full_report())


def register_bunny_routes(app):
    """Call this in agent.py: register_bunny_routes(app)"""
    app.register_blueprint(bunny_bp)
    print("[Mantis] Bunny Button routes registered at /bunny/*")
