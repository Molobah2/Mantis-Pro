"""
Flask Blueprint for the NFT Market Intelligence -> X Content Generator's
JSON API. Mirrors opensea_automint/routes.py's shape: a self-contained
package registered into agent.py via one register_blueprint call, so this
feature doesn't collide with concurrent edits to agent.py or the other
dashboards.
"""
import re

from flask import Blueprint, Response, jsonify, request

from portal_upvote import security as _sec

from . import captions, card_renderer, scan

nft_insights_bp = Blueprint("nft_insights", __name__)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")

# Scanning triggers real outbound OpenSea API calls (up to ~130 paginated
# requests for a large collection); card rendering additionally fetches
# every grid image and runs Pillow compositing. Both are rate-limited per
# IP so one client can't turn either into a resource-exhaustion vector —
# same posture as opensea_automint/routes.py's track-drop limit.
_SCAN_RATE_LIMIT = 30
_SCAN_RATE_WINDOW_SECONDS = 3600
_CARD_RATE_LIMIT = 60
_CARD_RATE_WINDOW_SECONDS = 3600


def _valid_slug(slug: str) -> bool:
    return bool(slug) and bool(_SLUG_RE.match(slug))


def _image_lookup(scan_data: dict) -> dict[int, dict]:
    return {nft["token_id"]: nft for nft in (scan_data.get("nfts") or [])}


def _serialize_insight(insight, collection_name: str, images: dict[int, dict]) -> dict:
    nfts = []
    for token_id in insight.nft_token_ids:
        nft = images.get(token_id) or {}
        nfts.append({"token_id": token_id, "image_url": nft.get("image_url"), "name": nft.get("name")})
    return {
        "id": insight.id,
        "type": insight.type,
        "score": insight.score,
        "is_best": insight.is_best,
        "hero_token_id": insight.hero_token_id,
        "data": insight.data,
        "headline": captions.headline(insight, collection_name),
        "captions": captions.caption_options(insight, collection_name),
        "nfts": nfts,
    }


@nft_insights_bp.route("/api/insights/scan")
def api_scan() -> Response:
    slug = (request.args.get("slug") or "").strip().lower()
    if not _valid_slug(slug):
        return jsonify({"error": "invalid or missing slug"}), 400

    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(ip, "insights-scan", limit=_SCAN_RATE_LIMIT, window=_SCAN_RATE_WINDOW_SECONDS):
        return jsonify({"error": "rate limited — try again later"}), 429

    result = scan.get_scan(slug)
    collection = result["scan"]["collection"]
    collection_name = collection.get("name") or slug
    images = _image_lookup(result["scan"])
    return jsonify({
        "collection": collection,
        "stats": result["scan"]["stats"],
        "fetched_at": result["fetched_at"],
        "insights": [_serialize_insight(i, collection_name, images) for i in result["insights"]],
    })


@nft_insights_bp.route("/api/insights/card")
def api_card() -> Response:
    slug = (request.args.get("slug") or "").strip().lower()
    insight_id = (request.args.get("insight_id") or "").strip()
    format_key = (request.args.get("format") or card_renderer.DEFAULT_FORMAT).strip()
    if not _valid_slug(slug) or not insight_id:
        return jsonify({"error": "invalid or missing slug/insight_id"}), 400

    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(ip, "insights-card", limit=_CARD_RATE_LIMIT, window=_CARD_RATE_WINDOW_SECONDS):
        return jsonify({"error": "rate limited — try again later"}), 429

    result, insight = scan.find_insight(slug, insight_id)
    if insight is None:
        return jsonify({"error": "insight not found for this collection — try re-scanning"}), 404

    collection_name = result["scan"]["collection"].get("name") or slug
    images_by_token = _image_lookup(result["scan"])
    nft_images = {
        token_id: card_renderer.fetch_nft_image((images_by_token.get(token_id) or {}).get("image_url"))
        for token_id in insight.nft_token_ids
    }

    png_bytes = card_renderer.render(insight, collection_name, nft_images, format_key=format_key)
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=600"
    return resp


@nft_insights_bp.route("/api/insights/caption")
def api_caption() -> Response:
    slug = (request.args.get("slug") or "").strip().lower()
    insight_id = (request.args.get("insight_id") or "").strip()
    if not _valid_slug(slug) or not insight_id:
        return jsonify({"error": "invalid or missing slug/insight_id"}), 400

    # Same cache-miss cost as api_scan (find_insight falls through to a full
    # scan.get_scan on a cold/expired slug) — rate-limited the same way so
    # this endpoint can't be used to bypass api_scan's budget.
    ip = _sec.get_client_ip(request)
    if not _sec.rate_limit(ip, "insights-caption", limit=_SCAN_RATE_LIMIT, window=_SCAN_RATE_WINDOW_SECONDS):
        return jsonify({"error": "rate limited — try again later"}), 429

    result, insight = scan.find_insight(slug, insight_id)
    if insight is None:
        return jsonify({"error": "insight not found for this collection — try re-scanning"}), 404

    collection_name = result["scan"]["collection"].get("name") or slug
    return jsonify({
        "headline": captions.headline(insight, collection_name),
        "captions": captions.caption_options(insight, collection_name),
    })
