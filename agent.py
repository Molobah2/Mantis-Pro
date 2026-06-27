import os
import json
import requests
import subprocess
import threading
import time
import sqlite3
import io
import re
import base64
from web3 import Web3
from dotenv import load_dotenv
import anthropic
from flask import Flask, jsonify

load_dotenv()

# ── MOODY MADNESS ───────────────────────────
from moody_agent import moody_check, record_woke, get_status, send_woke_confirmation

# ── FLASK HEALTH SERVER ─────────────────────
app = Flask(__name__)

@app.route("/")
def root():
    import os
    path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

@app.route("/static/abstract-bg.png")
def serve_bg():
    import os
    from flask import send_from_directory
    return send_from_directory(os.path.dirname(__file__), "abstract-bg.png")

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "agent": "Mantis Pro"})

@app.route("/mcp")
def mcp():
    return jsonify({
        "name": "Mantis Pro",
        "version": "1.0.0",
        "description": "Autonomous Litany battle and trading agent on Abstract Chain",
        "tools": [
            {"name": "scan_market", "description": "Scan Litany card listings on OpenSea"},
            {"name": "get_floor_price", "description": "Get current Litany card floor price"},
            {"name": "get_wallet_status", "description": "Get wallet balance and card count"}
        ]
    })

@app.route("/metadata")
def metadata():
    return jsonify(AGENT_METADATA)

@app.route("/litany")
def litany_dashboard():
    import os
    path = os.path.join(os.path.dirname(__file__), "litany.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

@app.route("/litany/scanner")
def litany_scanner():
    import os
    path = os.path.join(os.path.dirname(__file__), "litany_scanner.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

@app.route("/litany/rarity")
def litany_rarity():
    import os
    path = os.path.join(os.path.dirname(__file__), "litany_rarity.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

@app.route("/rarity_index.json")
def rarity_index_json():
    import os
    from flask import Response, jsonify
    path = os.path.join(os.path.dirname(__file__), "rarity_index.json")
    if not os.path.exists(path):
        return jsonify({"error": "not built"}), 404
    with open(path, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="application/json")

@app.route("/activity")
def activity_explorer():
    import os
    path = os.path.join(os.path.dirname(__file__), "activity.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

@app.route("/api/card-image/<int:token_id>")
def card_image(token_id):
    from flask import Response
    if not (1 <= token_id <= 8000):
        return Response(status=404)
    svg = _get_card_svg(token_id)
    if not svg:
        return Response(status=404)
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp

@app.route("/api/card-meta/<int:token_id>")
def card_meta(token_id):
    from flask import Response
    if not (1 <= token_id <= 8000):
        return jsonify({"error": "invalid"}), 404
    try:
        uri = _cards().functions.tokenURI(token_id).call()
        b64_json = uri.split(",", 1)[1] if "," in uri else uri
        b64_json += "=" * ((4 - len(b64_json) % 4) % 4)
        meta = json.loads(base64.b64decode(b64_json))
        meta.pop("image", None)   # strip the giant SVG before sending
        resp = jsonify(meta)
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/operator/<address>")
def operator_profile(address):
    import os
    path = os.path.join(os.path.dirname(__file__), "operator.html")
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

_rarity_idx = None
def _load_rarity_idx():
    global _rarity_idx
    if _rarity_idx is not None:
        return _rarity_idx
    try:
        path = os.path.join(os.path.dirname(__file__), "rarity_index.json")
        with open(path, "r") as f:
            data = json.load(f)
        _rarity_idx = {int(c["tokenId"]): c for c in data.get("cards", [])}
    except Exception as e:
        print(f"rarity idx: {e}")
        _rarity_idx = {}
    return _rarity_idx

@app.route("/api/card-stats")
def card_stats():
    from flask import request
    raw = (request.args.get("ids") or "").split(",")
    ids = [int(x.strip()) for x in raw if x.strip().isdigit()][:100]
    idx = _load_rarity_idx()
    return jsonify({i: idx[i] for i in ids if i in idx})

@app.route("/api/operator-search")
def operator_search():
    from flask import request
    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify([])
    names = _load_litany_names() or {}
    fmap = _mesh_faction_map()
    seen, out = set(), []
    for addr, info in names.items():
        nm = info.get("name") or ""
        if nm and q in nm.lower():
            out.append({"address": addr, "name": nm, "faction": (fmap.get(addr) or {}).get("faction")})
            seen.add(addr)
    if q.startswith("0x") and len(q) >= 4:
        for addr in set(list(names.keys()) + list(fmap.keys())):
            if addr.startswith(q) and addr not in seen:
                out.append({"address": addr, "name": (names.get(addr) or {}).get("name"),
                            "faction": (fmap.get(addr) or {}).get("faction")})
                seen.add(addr)
    out.sort(key=lambda r: (r["name"] or "").lower())
    return jsonify(out[:8])

@app.route("/api/abstract-rpc", methods=["POST"])
def abstract_rpc():
    import requests
    from flask import request as _rpc_req, Response, jsonify
    try:
        payload = _rpc_req.get_json(force=True)
        r = requests.post("https://api.mainnet.abs.xyz", json=payload, timeout=20)
        return Response(r.text, mimetype="application/json")
    except Exception as e:
        return jsonify({"error": {"message": str(e)}}), 502

@app.route("/api/opensea")
def opensea_proxy():
    import os, requests
    from flask import request as _os_req, Response, jsonify
    key = os.environ.get("OPENSEA_API_KEY")
    if not key:
        return jsonify({"error": "OPENSEA_API_KEY not set in environment"}), 503
    path = _os_req.args.get("path", "")
    if not path.startswith("/api/v2/"):
        return jsonify({"error": "invalid path"}), 400
    try:
        r = requests.get("https://api.opensea.io" + path,
                         headers={"X-API-KEY": key, "accept": "application/json"}, timeout=20)
        return Response(r.text, status=r.status_code, mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 502

# ── IDENTITY RESOLUTION (ANS .abs + optional AGW/Portal) ─────────
# Resolves an Abstract address -> {name, twitter, avatar}, in priority order:
#   1. AGW/Portal profile  — only if a confirmed endpoint is set via AGW_PROFILE_URL
#   2. ANS .abs (+ twitter) — real on-chain reverse lookup (no config, no auth)
#   3. unresolved           — UI shows a generated identicon
# Multi-layer cache: SQLite (persistent) + in-memory hot mirror + cached avatar bytes.
# Feed payloads are served from cache; a background thread refreshes stale entries.
_ID_TTL = 48 * 3600        # identity freshness window
AGW_PROFILE_URL = os.environ.get("AGW_PROFILE_URL", "https://backend.portal.abs.xyz/api/user/address/{address}").strip()
_ID_DB = os.path.join(os.path.dirname(__file__), "identities.db")
_id_lock = threading.Lock()
_id_mem = {}               # wallet -> row dict (hot mirror)
_img_cache = {}            # avatar url -> (content_type, bytes)

def _id_db():
    conn = sqlite3.connect(_ID_DB, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS identities(
        wallet TEXT PRIMARY KEY, name TEXT, twitter TEXT, avatar TEXT,
        source TEXT, updated REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS profiles(
        wallet TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL)""")
    return conn

try:
    _id_db().close()
except Exception as _e:
    print(f"identity db init: {_e}")

def id_get(wallet):
    wallet = wallet.lower()
    if wallet in _id_mem:
        return _id_mem[wallet]
    try:
        with _id_lock:
            conn = _id_db()
            cur = conn.execute("SELECT wallet,name,twitter,avatar,source,updated FROM identities WHERE wallet=?", (wallet,))
            r = cur.fetchone()
            conn.close()
        if r:
            row = {"wallet": r[0], "name": r[1], "twitter": r[2], "avatar": r[3], "source": r[4], "updated": r[5]}
            _id_mem[wallet] = row
            return row
    except Exception as e:
        print(f"id_get {wallet}: {e}")
    return None

def id_put(wallet, name, twitter, avatar, source):
    wallet = wallet.lower()
    row = {"wallet": wallet, "name": name, "twitter": twitter, "avatar": avatar,
           "source": source, "updated": time.time()}
    _id_mem[wallet] = row
    try:
        with _id_lock:
            conn = _id_db()
            conn.execute("INSERT OR REPLACE INTO identities VALUES (?,?,?,?,?,?)",
                         (wallet, name, twitter, avatar, source, row["updated"]))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"id_put {wallet}: {e}")
    return row

# Abstract Name Service (ANS) V2 — independent naming protocol on Abstract.
ANS_V2_ADDRESS = "0x86a282845a61302Ba4735d111b1a1417f6e617Ad"
ANS_ABI = [
    {"name": "getNameByAddress", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "addr", "type": "address"}], "outputs": [{"name": "", "type": "string"}]},
    {"name": "textRecords", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "name", "type": "string"}, {"name": "key", "type": "string"}],
     "outputs": [{"name": "", "type": "string"}]},
]
_ans_contract = None
def _ans():
    global _ans_contract
    if _ans_contract is None:
        _ans_contract = w3.eth.contract(address=Web3.to_checksum_address(ANS_V2_ADDRESS), abi=ANS_ABI)
    return _ans_contract

def _resolve_ans(addr):
    """Address -> {.abs name, avatar, twitter} via ANS V2 on-chain reads. None if no name."""
    try:
        name = _ans().functions.getNameByAddress(Web3.to_checksum_address(addr)).call()
    except Exception:
        return None
    if not name:
        return None
    def _rec(key):
        try:
            return (_ans().functions.textRecords(name, key).call() or "").strip()
        except Exception:
            return ""
    avatar = _rec("avatar") or None
    twitter = _rec("twitter") or None
    if twitter:
        twitter = twitter.lstrip("@")
        if "/" in twitter:                      # tolerate a full URL in the record
            twitter = twitter.rstrip("/").split("/")[-1]
        twitter = twitter or None
    # If they linked X but set no explicit avatar, use their X profile picture.
    if not avatar and twitter:
        avatar = f"https://unavatar.io/x/{twitter}"
    return {"resolved": True, "address": addr.lower(), "username": name,
            "avatar": avatar, "twitter": twitter, "source": "ANS"}

def _resolve_portal(addr):
    """Optional AGW/Portal profile via a confirmed endpoint (set AGW_PROFILE_URL with {address})."""
    if not AGW_PROFILE_URL:
        return None
    try:
        resp = requests.get(AGW_PROFILE_URL.replace("{address}", addr),
                            headers={"Accept": "application/json"}, timeout=6)
        if resp.status_code != 200:
            return None
        j = resp.json()
        user = j.get("user") if isinstance(j.get("user"), dict) else j
        username = user.get("username") or user.get("name") or user.get("handle")
        raw_av = user.get("overrideProfilePictureUrl") or user.get("pfp") or user.get("profilePicture") or user.get("image")
        if isinstance(raw_av, str) and raw_av.startswith("http"):
            avatar = raw_av
        else:
            av_obj = user.get("avatar") if isinstance(user.get("avatar"), dict) else None
            if av_obj and av_obj.get("assetType") == "avatar":
                s, t, k = av_obj.get("season", 1), av_obj.get("tier", 1), av_obj.get("key", 1)
                avatar = f"https://abstract-assets.abs.xyz/avatars/{s}-{t}-{k}.png"
            else:
                avatar = None
        twitter = user.get("twitter") or user.get("x") or user.get("xHandle")
        if isinstance(twitter, str):
            twitter = twitter.lstrip("@").rstrip("/").split("/")[-1] or None
        if username or avatar or twitter:
            if not avatar and twitter:
                avatar = f"https://unavatar.io/x/{twitter}"
            return {"resolved": True, "address": addr.lower(),
                    "username": username, "avatar": avatar, "twitter": twitter, "source": "portal"}
    except Exception:
        return None
    return None

_litany_cache = {}        # addr -> {"name":..., "avatar":...}
_litany_ts = 0.0
_LITANY_TTL = 3600        # refresh the leaderboard hourly
_LITANY_LB = "https://litany.gg/market/leaderboard"

def _load_litany_names():
    """Scrape the public, server-rendered Litany market leaderboard for address -> {name, avatar}."""
    global _litany_cache, _litany_ts
    if _litany_cache and (time.time() - _litany_ts < _LITANY_TTL):
        return _litany_cache
    try:
        html = requests.get(_LITANY_LB, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                            timeout=10).text
        found = {}
        for m in re.finditer(r'href="[^"]*?market/profile/(0x[0-9a-fA-F]{40})"[^>]*>(.*?)</a>', html, re.S | re.I):
            addr = m.group(1).lower()
            raw = m.group(2)
            img = re.search(r'<img[^>]+src="([^"]+)"', raw, re.I)
            avatar = img.group(1) if img else None
            if avatar and avatar.startswith("/"):
                avatar = "https://litany.gg" + avatar
            name = re.sub(r'<[^>]+>', ' ', raw)
            name = re.sub(r'0x[0-9a-fA-F]{2,8}\s*[…\.]{1,3}\s*[0-9a-fA-F]{2,8}', ' ', name)
            name = re.sub(r'\s+', ' ', name).strip()
            if name.lower() == "unknown operator":
                name = None
            if name or avatar:
                found[addr] = {"name": name, "avatar": avatar}
        if found:
            _litany_cache, _litany_ts = found, time.time()
    except Exception as e:
        print(f"litany names: {e}")
    return _litany_cache

def resolve_identity(wallet, network=True):
    """Cache-first identity. Resolves over the network only on miss/stale when allowed."""
    wallet = wallet.lower()
    row = id_get(wallet)
    if row and (time.time() - (row.get("updated") or 0) < _ID_TTL):
        return row
    if not network:
        return row  # stale or None — caller renders identicon
    prof = _resolve_portal(wallet) or _resolve_ans(wallet)
    if prof:
        return id_put(wallet, prof.get("username"), prof.get("twitter"), prof.get("avatar"), prof.get("source"))
    lit = _load_litany_names().get(wallet)
    if lit and (lit.get("name") or lit.get("avatar")):
        return id_put(wallet, lit.get("name"), None, lit.get("avatar"), "litany")
    return id_put(wallet, None, None, None, None)  # cache the negative result too

# ── LITANY MESH API (public, no auth — verified endpoints) ───────
_MESH_BASE = "https://litany.gg/api/mesh"
_mesh_lb = {"data": None, "ts": 0.0}
_MESH_TTL = 120

def _mesh_get(path):
    try:
        r = requests.get(_MESH_BASE + path, headers={"Accept": "application/json",
                         "User-Agent": "Mozilla/5.0"}, timeout=10)
        j = r.json()
        if isinstance(j, dict) and j.get("ok"):
            return j.get("data")
    except Exception as e:
        print(f"mesh {path}: {e}")
    return None

def _mesh_leaderboard():
    now = time.time()
    if _mesh_lb["data"] and now - _mesh_lb["ts"] < _MESH_TTL:
        return _mesh_lb["data"]
    d = _mesh_get("/leaderboards")
    if d:
        _mesh_lb["data"], _mesh_lb["ts"] = d, now
    return _mesh_lb["data"]

def _mesh_faction_map():
    """wallet -> {faction, claims, rank} derived from the verified mesh leaderboard."""
    d = _mesh_leaderboard()
    out = {}
    if d and isinstance(d.get("entries"), list):
        for e in d["entries"]:
            w = (e.get("wallet") or "").lower()
            if w:
                out[w] = {"faction": e.get("faction"), "claims": e.get("value"), "rank": e.get("rank")}
    return out

# ── LITANY PROTOCOL CONTRACTS (Abstract mainnet, chainId 2741) ───
CARDS_ADDR        = "0xd44abe71c312FCAf73cC20f7DF61C39A89C203eB"  # LitanyCards ERC-721
HOLLOW_NFT_ADDR   = "0xf315f88969982d10eafFB93249C00d5BA47C2A28"  # Hollow ERC-721 (live)
GENESIS_CLAIM_ADDR= "0x9cC639C9855cF9e959C47A7bDF1c3C14468A270f"  # Genesis claim gate

_HOLLOW_NFT_ABI = [
    {"name": "balanceOf",  "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "tokenURI",   "type": "function", "stateMutability": "view",
     "inputs": [{"name": "tokenId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "string"}]},
    {"name": "getPublicGeneData", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "hollowId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]
_GENESIS_CLAIM_ABI = [
    {"name": "claimsOpen",          "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "bool"}]},
    {"name": "totalGenesisClaimed", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "GENESIS_SUPPLY_CAP",  "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "litanyClaimed",       "type": "function", "stateMutability": "view",
     "inputs": [{"name": "cardId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "cardToHollow",        "type": "function", "stateMutability": "view",
     "inputs": [{"name": "cardId", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]
_hollow_contract = None
_genesis_contract = None

def _hollows():
    global _hollow_contract
    if _hollow_contract is None:
        _hollow_contract = w3.eth.contract(
            address=Web3.to_checksum_address(HOLLOW_NFT_ADDR), abi=_HOLLOW_NFT_ABI)
    return _hollow_contract

def _genesis():
    global _genesis_contract
    if _genesis_contract is None:
        _genesis_contract = w3.eth.contract(
            address=Web3.to_checksum_address(GENESIS_CLAIM_ADDR), abi=_GENESIS_CLAIM_ABI)
    return _genesis_contract

def _hollow_balance(addr):
    """balanceOf on the Hollow NFT contract — single eth_call, very fast."""
    try:
        return _hollows().functions.balanceOf(Web3.to_checksum_address(addr)).call()
    except Exception as e:
        print(f"hollow balance {addr}: {e}")
        return 0

_genesis_stats_cache = {"data": None, "ts": 0.0}
_GENESIS_STATS_TTL  = 120  # 2-minute cache (global stat, rarely changes fast)

def _genesis_global_stats():
    """totalGenesisClaimed + claimsOpen — 2-min cache."""
    if time.time() - _genesis_stats_cache["ts"] < _GENESIS_STATS_TTL and _genesis_stats_cache["data"]:
        return _genesis_stats_cache["data"]
    try:
        c = _genesis()
        data = {
            "total_claimed": c.functions.totalGenesisClaimed().call(),
            "supply_cap":    c.functions.GENESIS_SUPPLY_CAP().call(),
            "claims_open":   c.functions.claimsOpen().call(),
        }
    except Exception as e:
        print(f"genesis stats: {e}")
        data = {"total_claimed": 0, "supply_cap": 5000, "claims_open": False}
    _genesis_stats_cache["data"] = data
    _genesis_stats_cache["ts"]   = time.time()
    return data

# ── LITANY CARD IMAGE CACHE (real on-chain SVG via tokenURI) ─────
_CARD_ABI = [{"name": "tokenURI", "type": "function", "stateMutability": "view",
              "inputs": [{"name": "tokenId", "type": "uint256"}],
              "outputs": [{"name": "", "type": "string"}]}]
_card_contract = None
_card_img_cache = {}   # tokenId -> svg bytes

def _cards():
    global _card_contract
    if _card_contract is None:
        _card_contract = w3.eth.contract(address=Web3.to_checksum_address(CARDS_ADDR), abi=_CARD_ABI)
    return _card_contract

def _get_card_svg(token_id):
    if token_id in _card_img_cache:
        return _card_img_cache[token_id]
    try:
        uri = _cards().functions.tokenURI(token_id).call()
        b64_json = uri.split(",", 1)[1] if "," in uri else uri
        b64_json += "=" * ((4 - len(b64_json) % 4) % 4)
        meta = json.loads(base64.b64decode(b64_json))
        img = meta.get("image", "")
        if "base64," in img:
            b64_img = img.split("base64,", 1)[1]
            b64_img += "=" * ((4 - len(b64_img) % 4) % 4)
            svg = base64.b64decode(b64_img)
        elif img.startswith("data:image/svg+xml,"):
            svg = img.split(",", 1)[1].encode()
        else:
            return None
        _card_img_cache[token_id] = svg
        return svg
    except Exception as e:
        print(f"card img {token_id}: {e}")
        return None

def _identicon_svg(addr):
    h = 2166136261
    for ch in addr[2:]:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    hue = h % 360
    fg, bg = f"hsl({hue},58%,50%)", f"hsl({hue},32%,92%)"
    cells = ""
    for y in range(5):
        for x in range(3):
            if (h >> ((y * 3 + x) % 30)) & 1:
                cells += f'<rect x="{x}" y="{y}" width="1" height="1"/>'
                if 4 - x != x:
                    cells += f'<rect x="{4-x}" y="{y}" width="1" height="1"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 5 5" width="80" height="80">'
            f'<rect width="5" height="5" fill="{bg}"/><g fill="{fg}">{cells}</g></svg>')

def _img_fetch(url):
    if url in _img_cache:
        return _img_cache[url]
    try:
        r = requests.get(url, timeout=6)
        if r.status_code == 200 and r.content:
            ct = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
            if len(_img_cache) > 800:
                _img_cache.clear()
            _img_cache[url] = (ct, r.content)
            return _img_cache[url]
    except Exception:
        pass
    return None

@app.route("/api/agw-profile")
def agw_profile():
    from flask import request
    addr = (request.args.get("address") or "").strip().lower()
    if not (addr.startswith("0x") and len(addr) == 42):
        return jsonify({"resolved": False, "error": "bad address"}), 400
    row = resolve_identity(addr, network=True) or {}
    return jsonify({"resolved": bool(row.get("name") or row.get("avatar") or row.get("twitter")),
                    "address": addr, "username": row.get("name"),
                    "avatar": row.get("avatar"), "twitter": row.get("twitter"),
                    "source": row.get("source")})

@app.route("/api/identities")
def identities_batch():
    """Batch identity lookup for the feed. Cache-first; bounded network resolves per call."""
    from flask import request
    raw = (request.args.get("addrs") or "").strip()
    addrs = [a.lower() for a in raw.split(",") if a.startswith("0x") and len(a) == 42][:40]
    fmap = _mesh_faction_map()
    out, budget = {}, 24
    for a in addrs:
        cached = id_get(a)
        fresh = cached and (time.time() - (cached.get("updated") or 0) < _ID_TTL)
        if fresh or budget <= 0:
            row = cached
        else:
            budget -= 1
            row = resolve_identity(a, network=True)
        mesh = fmap.get(a) or {}
        entry = ({"name": row.get("name"), "twitter": row.get("twitter"),
                  "avatar": bool(row.get("avatar")),
                  "resolved": bool(row.get("name") or row.get("avatar") or row.get("twitter"))}
                 if row else {"resolved": False})
        entry["faction"] = mesh.get("faction")
        entry["claims"] = mesh.get("claims")
        out[a] = entry
    return jsonify(out)

@app.route("/api/avatar")
def avatar_proxy():
    """Always returns a valid, cacheable image: proxied X/ANS avatar (cached bytes) or identicon."""
    from flask import request, Response
    addr = (request.args.get("addr") or "").strip().lower()
    if not (addr.startswith("0x") and len(addr) == 42):
        return Response(status=400)
    row = id_get(addr)               # cache-only — never resolves in the image path
    if row and row.get("avatar"):
        got = _img_fetch(row["avatar"])
        if got:
            resp = Response(got[1], content_type=got[0])
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
    resp = Response(_identicon_svg(addr), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.route("/api/litany-mesh")
def litany_mesh_proxy():
    from flask import request, Response
    path = (request.args.get("path") or "").strip()
    allowed = path in {"/leaderboards", "/faction-stats", "/events", "/map", "/overlay"} \
        or bool(re.match(r'^/wallet/0x[0-9a-fA-F]{40}(/territory)?$', path)) \
        or bool(re.match(r'^/faction/(breach|lens|horizon)$', path)) \
        or bool(re.match(r'^/cell/[\w:-]+$', path))
    if not allowed:
        return jsonify({"ok": False, "error": "path not allowed"}), 403
    try:
        r = requests.get(_MESH_BASE + path, headers={"Accept": "application/json",
                         "User-Agent": "Mozilla/5.0"}, timeout=12)
        resp = Response(r.content, status=r.status_code, content_type="application/json")
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

# ── AGGREGATED OPERATOR PROFILE ENDPOINT ─────────────────────────
# Collapses 6+ browser→Flask→external calls into one parallel server fetch.
# Server-to-server latency is 10-50ms vs 200-500ms browser-initiated.

_op_prof_cache  = {}   # addr -> {"ts": float, "data": dict}
_OP_PROF_TTL    = 600  # serve fresh within 10 min
_OP_PROF_STALE  = 1800 # serve stale up to 30 min while refreshing in bg

_loyalty_cache  = {}   # addr -> {"ts": float, "bonus": float}
_LOYALTY_TTL    = 3600 # loyalty changes at most hourly

_os_nft_cache   = {}   # addr -> {"ts": float, "ids": list[int]}
_OS_NFT_TTL     = 300  # 5-minute NFT ownership cache

_refresh_set      = set()
_refresh_lock_bg  = threading.Lock()

_ca_cache = {}
_CA_TTL   = 300   # 5 minutes

# ── SQLite profile persistence ────────────────────────────────────────────────
def _prof_db_get(wallet):
    """Return (data_dict, age_secs) from SQLite, or (None, inf) on miss."""
    try:
        with _id_lock:
            conn = _id_db()
            row  = conn.execute(
                "SELECT data, updated FROM profiles WHERE wallet=?", (wallet,)
            ).fetchone()
            conn.close()
        if row:
            return json.loads(row[0]), time.time() - row[1]
    except Exception:
        pass
    return None, float("inf")

def _prof_db_put(wallet, data):
    try:
        payload = json.dumps(data, default=str)
        with _id_lock:
            conn = _id_db()
            conn.execute(
                "INSERT OR REPLACE INTO profiles(wallet,data,updated) VALUES(?,?,?)",
                (wallet, payload, time.time())
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"prof db put {wallet}: {e}")

def _warmup_profile_cache():
    """Pre-load recent profiles from SQLite into memory on startup."""
    try:
        with _id_lock:
            conn = _id_db()
            rows = conn.execute(
                "SELECT wallet,data,updated FROM profiles WHERE updated>? ORDER BY updated DESC LIMIT 200",
                (time.time() - _OP_PROF_STALE,)
            ).fetchall()
            conn.close()
        for wallet, data_json, ts in rows:
            try:
                _op_prof_cache[wallet] = {"ts": ts, "data": json.loads(data_json)}
            except Exception:
                pass
        if rows:
            print(f"profile cache warm: {len(rows)} profiles pre-loaded")
    except Exception as e:
        print(f"profile warmup: {e}")

_floor_cache   = {"val": 0.0, "ts": 0.0}
_FLOOR_TTL     = 300             # 5-minute floor price cache

def _server_owned_cards(addr):
    """Fetch owned Litany card IDs.
    RPC Transfer logs are always the primary source (ground truth).
    OpenSea supplements with unlimited pagination to catch any gaps.
    Both sources are merged so whale wallets like boredbull (1250+ cards)
    are never truncated by OpenSea's incomplete Abstract Chain indexing."""
    hit = _os_nft_cache.get(addr)
    if hit and time.time() - hit["ts"] < _OS_NFT_TTL:
        return hit["ids"]

    TT     = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    padded = "0x000000000000000000000000" + addr[2:].lower()
    ids_set = set()

    # ── RPC Transfer log scan (always runs — exact on-chain truth) ─────────────
    # Tries one wide call first; if the node rejects it (too many results),
    # falls back to 50k-block chunks fetched in parallel.
    def _post(payload):
        resp = requests.post(RPC_URL, json=payload, timeout=15).json()
        if resp.get("error"):
            raise ValueError(resp["error"].get("message", "rpc error"))
        return resp.get("result") or []

    def _net_balance(recv_logs, sent_logs):
        net = {}
        for lg in recv_logs:
            k = str(int(lg["topics"][3], 16)); net[k] = net.get(k, 0) + 1
        for lg in sent_logs:
            k = str(int(lg["topics"][3], 16)); net[k] = net.get(k, 0) - 1
        ids_set.update(int(k) for k, v in net.items() if v > 0)

    try:
        recv = _post({"jsonrpc":"2.0","id":1,"method":"eth_getLogs","params":[
            {"address":CARDS_ADDR,"topics":[TT,None,padded],"fromBlock":"0x0","toBlock":"latest"}]})
        sent = _post({"jsonrpc":"2.0","id":2,"method":"eth_getLogs","params":[
            {"address":CARDS_ADDR,"topics":[TT,padded,None],"fromBlock":"0x0","toBlock":"latest"}]})
        _net_balance(recv, sent)
    except Exception as e:
        print(f"rpc nft wide {addr}: {e} — trying chunked")
        try:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            latest = int(_post({"jsonrpc":"2.0","id":0,"method":"eth_blockNumber","params":[]}) or "0x0", 16)
            CHUNK  = 50_000
            ranges = [(max(0, i - CHUNK + 1), i) for i in range(latest, -1, -CHUNK)]

            def _chunk(fb, tb, recv_or_sent):
                filt = [TT, None, padded] if recv_or_sent == "recv" else [TT, padded, None]
                return _post({"jsonrpc":"2.0","id":fb,"method":"eth_getLogs","params":[
                    {"address":CARDS_ADDR,"topics":filt,
                     "fromBlock":hex(fb),"toBlock":hex(tb)}]})

            with _TPE(max_workers=10) as ex:
                r_futs = [ex.submit(_chunk, fb, tb, "recv") for fb, tb in ranges]
                s_futs = [ex.submit(_chunk, fb, tb, "sent") for fb, tb in ranges]
            all_recv, all_sent = [], []
            for f in r_futs:
                try: all_recv.extend(f.result())
                except: pass
            for f in s_futs:
                try: all_sent.extend(f.result())
                except: pass
            _net_balance(all_recv, all_sent)
        except Exception as e2:
            print(f"rpc nft chunked {addr}: {e2}")

    # ── OpenSea supplement (unlimited pages, merges into ids_set) ─────────────
    key = os.environ.get("OPENSEA_API_KEY")
    if key:
        try:
            import urllib.parse
            nxt, pg = None, 0
            while pg < 200:
                url = f"https://api.opensea.io/api/v2/chain/abstract/account/{addr}/nfts?limit=50"
                if nxt:
                    url += "&next=" + urllib.parse.quote(nxt)
                r = requests.get(url, headers={"X-API-KEY": key, "accept": "application/json"}, timeout=15)
                j = r.json()
                for n in (j.get("nfts") or []):
                    if (n.get("contract") or "").lower() == CARDS_ADDR.lower():
                        try: ids_set.add(int(n.get("identifier") or n.get("token_id") or 0))
                        except: pass
                nxt = j.get("next"); pg += 1
                if not nxt:
                    break
        except Exception as e:
            print(f"os nft {addr}: {e}")

    ids = sorted(i for i in ids_set if i > 0)
    _os_nft_cache[addr] = {"ts": time.time(), "ids": ids}
    return ids

def _server_floor():
    """Litany Cards floor price, 5-minute cache."""
    if time.time() - _floor_cache["ts"] < _FLOOR_TTL:
        return _floor_cache["val"]
    key = os.environ.get("OPENSEA_API_KEY")
    if not key:
        return 0.0
    try:
        r = requests.get("https://api.opensea.io/api/v2/collections/litanycards",
                         headers={"X-API-KEY": key, "accept": "application/json"}, timeout=10)
        j = r.json()
        fl = float(j.get("collection", {}).get("stats", {}).get("floor_price") or j.get("floor_price") or 0)
        _floor_cache["val"], _floor_cache["ts"] = fl, time.time()
        return fl
    except Exception:
        return _floor_cache["val"]

def _loyalty_data(addr):
    """Hidden loyalty multiplier: +0.25 per 7 consecutive days without selling a card.
    Result cached for 1 hour — sell history changes slowly.
    Max +5.0. Selling resets the streak and applies a temporary penalty.
    Result is an internal signal only — never broken out in the UI breakdown."""
    hit = _loyalty_cache.get(addr)
    if hit and time.time() - hit["ts"] < _LOYALTY_TTL:
        return hit["bonus"]

    BURN   = "0x0000000000000000000000000000000000000000"
    TT     = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    padded = "0x000000000000000000000000" + addr[2:].lower()

    def _rpc(method, params):
        return requests.post(RPC_URL,
            json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
            timeout=12).json().get("result")

    try:
        latest_hex = _rpc("eth_blockNumber", []) or "0x0"
        latest_num = int(latest_hex, 16)
        latest_blk = _rpc("eth_getBlockByNumber", [latest_hex, False]) or {}
        latest_ts  = int(latest_blk.get("timestamp", "0x0"), 16)
        if latest_ts == 0 or latest_num == 0:
            return 0.0

        # Derive seconds-per-block from a reference block 1 M blocks back
        ref_n   = max(1, latest_num - 1_000_000)
        ref_blk = _rpc("eth_getBlockByNumber", [hex(ref_n), False]) or {}
        ref_ts  = int(ref_blk.get("timestamp", "0x0"), 16)
        secs_pb = (latest_ts - ref_ts) / max(1, latest_num - ref_n) if ref_ts > 0 else 2.0
        secs_pb = max(0.5, min(60.0, secs_pb))

        def block_age_days(bn):
            return (latest_num - bn) * secs_pb / 86400

        # Sent Transfer logs — potential sells
        sent_raw = _rpc("eth_getLogs", [{
            "address": CARDS_ADDR,
            "topics": [TT, padded, None],
            "fromBlock": "0x0", "toBlock": latest_hex
        }]) or []

        # Filter: non-burn, non-self transfers = market sells
        sell_ages = []
        for lg in sent_raw:
            to = ("0x" + lg["topics"][2][26:]).lower()
            if to not in (BURN, addr):
                sell_ages.append(block_age_days(int(lg["blockNumber"], 16)))

        def _cache_return(v):
            _loyalty_cache[addr] = {"ts": time.time(), "bonus": v}
            return v

        if not sell_ages:
            recv_raw = _rpc("eth_getLogs", [{
                "address": CARDS_ADDR,
                "topics": [TT, None, padded],
                "fromBlock": "0x0", "toBlock": latest_hex
            }]) or []
            if not recv_raw:
                return _cache_return(0.0)
            oldest_days = max(block_age_days(int(lg["blockNumber"], 16)) for lg in recv_raw)
            return _cache_return(round(min(5.0, (oldest_days / 7) * 0.25) * 4) / 4)

        days_since_sell = min(sell_ages)
        sells_in_30d    = sum(1 for d in sell_ages if d < 30)
        if sells_in_30d >= 3:
            penalty_pts, penalty_days = -6, 21
        elif sells_in_30d == 2:
            penalty_pts, penalty_days = -4, 14
        else:
            penalty_pts, penalty_days = -2, 7
        if days_since_sell < penalty_days:
            return _cache_return(round(penalty_pts * (1 - days_since_sell / penalty_days) * 4) / 4)
        return _cache_return(round(min(5.0, (days_since_sell / 7) * 0.25) * 4) / 4)

    except Exception as e:
        print(f"loyalty {addr}: {e}")
        return 0.0


_ref_cache    = {}
_REF_TTL      = 300   # 5 minutes per-wallet
_ref_lb_cache = {"ts": 0, "data": []}
_REF_LB_TTL   = 600   # 10 minutes for global leaderboard

def _referral_data(addr):
    hit = _ref_cache.get(addr)
    if hit and time.time() - hit["ts"] < _REF_TTL:
        return hit["data"]
    BASE = "https://litany.gg"
    def _codes():
        try:
            return requests.get(f"{BASE}/api/market/referrals/codes?wallet={addr}",
                                timeout=10).json()
        except: return {}
    def _rel():
        try:
            return requests.get(f"{BASE}/api/market/referrals/relationship?wallet={addr}",
                                timeout=10).json()
        except: return {}
    from concurrent.futures import ThreadPoolExecutor as _TPE
    with _TPE(max_workers=2) as ex:
        f_c = ex.submit(_codes); f_r = ex.submit(_rel)
    codes_raw   = f_c.result()
    rel_raw     = f_r.result()
    codes       = codes_raw.get("codes") or []
    active      = [c for c in codes if c.get("status") == "active"]
    total_refs  = sum(c.get("total_referees", 0) for c in codes)
    rel         = rel_raw.get("relationship") or {}
    data = {
        "codes":          active,
        "total_referees": total_refs,
        "referrer_wallet": rel.get("referrer_wallet"),
        "referred_at":     rel.get("established_at"),
        "is_og":          rel.get("is_og_relationship", False),
    }
    _ref_cache[addr] = {"ts": time.time(), "data": data}
    return data

def _compute_profile(addr):
    """Fan out all upstream calls and return the assembled profile dict."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_idn     = ex.submit(resolve_identity, addr, True)
        f_mesh    = ex.submit(_mesh_get, f"/wallet/{addr}")
        f_lb      = ex.submit(_mesh_leaderboard)
        f_nfts    = ex.submit(_server_owned_cards, addr)
        f_hollows = ex.submit(_hollow_balance, addr)
        f_loyalty = ex.submit(_loyalty_data, addr)
        f_ref     = ex.submit(_referral_data, addr)
    identity      = f_idn.result()     or {}
    mesh_data     = f_mesh.result()
    lb_raw        = f_lb.result()      or {}
    owned         = f_nfts.result()    or []
    hollow_count  = f_hollows.result() or 0
    loyalty_bonus = f_loyalty.result() or 0.0
    referral      = f_ref.result()     or {}
    floor         = _server_floor()
    genesis       = _genesis_global_stats()
    lb_entries = lb_raw.get("entries", []) if isinstance(lb_raw, dict) else []
    lb_entry   = next((e for e in lb_entries if (e.get("wallet") or "").lower() == addr), None)
    fmap       = _mesh_faction_map()
    idx        = _load_rarity_idx()
    return {
        "identity": {
            "name":    identity.get("name"),
            "twitter": identity.get("twitter"),
            "faction": (fmap.get(addr) or {}).get("faction"),
        },
        "mesh":          mesh_data,
        "lb_entries":    lb_entries,
        "lb_entry":      lb_entry,
        "lb_total":      len(lb_entries) or 68,
        "owned_cards":   owned,
        "card_stats":    {i: idx[i] for i in owned if i in idx},
        "floor":         floor,
        "hollow_count":  hollow_count,
        "genesis_stats": genesis,
        "loyalty_bonus": loyalty_bonus,
        "referral":      referral,
    }

def _bg_refresh(addr):
    """Recompute profile in background and write to memory + SQLite."""
    try:
        data = _compute_profile(addr)
        _op_prof_cache[addr] = {"ts": time.time(), "data": data}
        _prof_db_put(addr, data)
    except Exception as e:
        print(f"bg_refresh {addr}: {e}")
    finally:
        with _refresh_lock_bg:
            _refresh_set.discard(addr)

def _queue_bg_refresh(addr):
    with _refresh_lock_bg:
        if addr in _refresh_set:
            return
        _refresh_set.add(addr)
    threading.Thread(target=_bg_refresh, args=(addr,), daemon=True).start()

@app.route("/api/operator-data")
def operator_data_agg():
    from flask import request as freq
    addr = (freq.args.get("addr") or "").strip().lower()
    if not (addr.startswith("0x") and len(addr) == 42):
        return jsonify({"error": "bad address"}), 400

    now = time.time()

    # 1. Memory cache — fresh
    hit = _op_prof_cache.get(addr)
    if hit and now - hit["ts"] < _OP_PROF_TTL:
        resp = jsonify(hit["data"])
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp

    # 2. Memory cache — stale; serve immediately + refresh in background
    if hit and now - hit["ts"] < _OP_PROF_STALE:
        _queue_bg_refresh(addr)
        resp = jsonify(hit["data"])
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp

    # 3. SQLite — stale-while-revalidate from disk
    db_data, db_age = _prof_db_get(addr)
    if db_data:
        _op_prof_cache[addr] = {"ts": now - db_age, "data": db_data}
        if db_age < _OP_PROF_STALE:
            _queue_bg_refresh(addr)
            resp = jsonify(db_data)
            resp.headers["Cache-Control"] = "public, max-age=60"
            return resp
        # DB too old — fall through to fresh fetch but use as fallback

    # 4. Fresh compute (blocking)
    try:
        data = _compute_profile(addr)
        _op_prof_cache[addr] = {"ts": now, "data": data}
        _prof_db_put(addr, data)
    except Exception as e:
        print(f"operator_data_agg {addr}: {e}")
        # Return stale DB data if fresh fetch failed
        if db_data:
            return jsonify(db_data)
        return jsonify({"error": "upstream unavailable"}), 503

    resp = jsonify(data)
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp

@app.route("/api/hollow-global")
def hollow_global():
    """Global Hollows genesis stats — totalGenesisClaimed, supply cap, claims open."""
    data = _genesis_global_stats()
    resp = jsonify(data)
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp

@app.route("/api/referral-leaderboard")
def referral_leaderboard():
    import re, json as _json
    now = time.time()
    if now - _ref_lb_cache["ts"] < _REF_LB_TTL and _ref_lb_cache["data"]:
        resp = jsonify(_ref_lb_cache["data"])
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp
    try:
        r = requests.get("https://litany.gg/market/leaderboard", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; MantisBot/1.0)"})
        html = r.text
        leaders = []
        seen_wallets = set()
        # Each leaderboard entry is a flat JSON object containing referee_count + rank
        for m in re.finditer(r'\{[^{}]*?"referee_count"\s*:\s*\d+[^{}]*?\}', html):
            try:
                obj = _json.loads(m.group())
                wallet = (obj.get("wallet") or "").lower()
                if wallet and wallet not in seen_wallets and "rank" in obj:
                    seen_wallets.add(wallet)
                    leaders.append({
                        "rank":            obj.get("rank"),
                        "wallet":          wallet,
                        "display_name":    obj.get("display_name") or "",
                        "referee_count":   obj.get("referee_count", 0),
                        "points_total":    obj.get("points_total", 0),
                        "referral_points": obj.get("referral_points", 0),
                        "tier":            obj.get("tier", ""),
                    })
            except: pass
        leaders.sort(key=lambda x: x.get("rank", 9999))
        if leaders:
            _ref_lb_cache["ts"]   = now
            _ref_lb_cache["data"] = leaders
        resp = jsonify(leaders or _ref_lb_cache["data"])
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp
    except Exception as e:
        print(f"referral_leaderboard: {e}")
        resp = jsonify(_ref_lb_cache["data"] or [])
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp

@app.route("/api/contract-activity")
def contract_activity():
    from flask import request as freq
    from concurrent.futures import ThreadPoolExecutor
    from collections import defaultdict
    from datetime import datetime, timezone

    addr     = (freq.args.get("addr") or "").strip().lower()
    contract = freq.args.get("contract", "all")

    if not (addr.startswith("0x") and len(addr) == 42):
        return jsonify({"error": "bad address"}), 400

    ck  = (addr, contract)
    hit = _ca_cache.get(ck)
    if hit and time.time() - hit["ts"] < _CA_TTL:
        return jsonify(hit["data"])

    padded = "0x000000000000000000000000" + addr[2:].lower()
    TT     = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    ZERO   = "0x0000000000000000000000000000000000000000"

    def _rpc_ca(method, params):
        r = requests.post(RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=20).json()
        if r.get("error"):
            raise ValueError(r["error"].get("message", "rpc error"))
        return r.get("result") or []

    def _get_logs(caddr, topics, from_blk, to_blk):
        return _rpc_ca("eth_getLogs", [{
            "address": caddr, "topics": topics,
            "fromBlock": hex(from_blk) if isinstance(from_blk, int) else from_blk,
            "toBlock":   hex(to_blk)   if isinstance(to_blk,   int) else to_blk,
        }])

    def _fetch_logs_for(caddr, topics, latest_blk):
        """Try full range first; fall back to 50k-block chunks if rejected."""
        try:
            return _get_logs(caddr, topics, "0x0", hex(latest_blk))
        except Exception as e:
            print(f"ca wide query failed ({e}), trying chunked")
            CHUNK = 50_000
            ranges = [(max(0, i - CHUNK + 1), i) for i in range(latest_blk, -1, -CHUNK)]
            all_logs = []
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = [ex.submit(_get_logs, caddr, topics, fb, tb) for fb, tb in ranges]
                for f in futs:
                    try: all_logs.extend(f.result())
                    except: pass
            return all_logs

    def _blk_timestamp(blkn):
        try:
            b = _rpc_ca("eth_getBlockByNumber", [hex(blkn), False])
            if b and b.get("timestamp"):
                return blkn, int(b["timestamp"], 16)
        except:
            pass
        return blkn, None

    def _fetch_contract(cname, caddr):
        try:
            latest_blk = int(_rpc_ca("eth_blockNumber", []) or "0x0", 16)
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_in  = ex.submit(_fetch_logs_for, caddr, [TT, None, padded], latest_blk)
                f_out = ex.submit(_fetch_logs_for, caddr, [TT, padded, None], latest_blk)
            logs_in  = f_in.result()  or []
            logs_out = f_out.result() or []

            # Deduplicate
            seen_keys, raw_logs = set(), []
            for log in logs_in + logs_out:
                key = (log.get("transactionHash"), log.get("logIndex", "0"))
                if key not in seen_keys:
                    seen_keys.add(key)
                    raw_logs.append(log)

            # Fetch real block timestamps in parallel — avoids wrong-block-time estimates
            unique_blks = list(set(int(log["blockNumber"], 16) for log in raw_logs))
            blk_ts = {}
            if unique_blks:
                with ThreadPoolExecutor(max_workers=min(12, len(unique_blks))) as ex:
                    for blkn, ts in ex.map(_blk_timestamp, unique_blks):
                        if ts is not None:
                            blk_ts[blkn] = ts
            # Reference for fallback: fetch latest block's real timestamp once
            latest_ts = blk_ts.get(latest_blk)
            if latest_ts is None:
                _, latest_ts = _blk_timestamp(latest_blk)
            now_ts = time.time()

            txns = []
            for log in raw_logs:
                topics_list = log.get("topics", [])
                if len(topics_list) < 3: continue
                from_a = "0x" + topics_list[1][-40:]
                to_a   = "0x" + topics_list[2][-40:]
                tok_id = str(int(topics_list[3], 16)) if len(topics_list) > 3 else "?"
                blkn   = int(log["blockNumber"], 16)
                # Real timestamp if fetched; otherwise fall back using latest real ts
                ts = blk_ts.get(blkn)
                if ts is None:
                    ref_ts = latest_ts or now_ts
                    ts = round(ref_ts - (latest_blk - blkn) * 0.5)
                is_mint = from_a.lower() == ZERO.lower()
                is_burn = to_a.lower()   == ZERO.lower()
                is_in   = to_a.lower()   == addr
                if is_mint:   action = "Minted"
                elif is_burn: action = "Burned"
                elif is_in:   action = "Received"
                else:         action = "Sent"
                asset = "Card" if cname == "cards" else "Hollow"
                cp    = from_a if (is_in and not is_mint) else (to_a if not is_burn else None)
                txns.append({
                    "ts":           round(ts),
                    "block":        blkn,
                    "tx":           log.get("transactionHash", ""),
                    "contract":     cname,
                    "token_id":     tok_id,
                    "from":         from_a,
                    "to":           to_a,
                    "action":       action,
                    "label":        f"{action} {asset} #{tok_id}",
                    "counterparty": cp,
                })
            return txns
        except Exception as e:
            print(f"contract_activity {cname}: {e}")
            return []

    targets = {"cards": CARDS_ADDR, "hollows": HOLLOW_NFT_ADDR}
    fetch_list = [(contract, targets[contract])] if contract in targets else list(targets.items())

    all_txns = []
    with ThreadPoolExecutor(max_workers=len(fetch_list)) as ex:
        for f in [ex.submit(_fetch_contract, n, a) for n, a in fetch_list]:
            all_txns.extend(f.result())
    all_txns.sort(key=lambda x: x["block"], reverse=True)

    by_action = {}
    for t in all_txns:
        by_action[t["action"]] = by_action.get(t["action"], 0) + 1

    total = len(all_txns)
    now   = time.time()
    cut90 = now - 90 * 86400

    daily = defaultdict(lambda: defaultdict(int))
    for t in all_txns:
        if t["ts"] >= cut90:
            day = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            daily[day][t["action"]] += 1
            daily[day]["total"]     += 1

    first_ts = all_txns[-1]["ts"] if all_txns else None
    last_ts  = all_txns[0]["ts"]  if all_txns else None
    r30      = sum(1 for t in all_txns if t["ts"] >= now - 30 * 86400)

    if   r30 >= 20: act_score = "Very High"
    elif r30 >= 10: act_score = "High"
    elif r30 >= 3:  act_score = "Moderate"
    elif total > 0: act_score = "Low"
    else:           act_score = "None"

    insights = []
    if total == 0:
        insights.append("No on-chain activity found for this operator with Litany contracts.")
    else:
        mint_r = by_action.get("Minted",   0) / total
        sent_r = by_action.get("Sent",     0) / total
        recv_r = by_action.get("Received", 0) / total
        if   mint_r > 0.6: insights.append("Primarily a minter — most interactions are direct mints, not secondary market activity.")
        elif sent_r > 0.4:  insights.append("Active trader — frequently transfers or sells assets to other wallets.")
        elif recv_r > 0.4:  insights.append("Active acquirer — regularly receives assets, suggesting secondary market buying.")
        else:               insights.append("Balanced operator — mix of minting, receiving, and sending across contracts.")
        if   last_ts and now - last_ts < 86400:        insights.append("Active today — last transaction within the past 24 hours.")
        elif last_ts and now - last_ts < 7 * 86400:    insights.append("Recently active — last transaction within the past 7 days.")
        elif last_ts and now - last_ts > 30 * 86400:   insights.append("Dormant — no recorded activity in over 30 days.")
        c_n = sum(1 for t in all_txns if t["contract"] == "cards")
        h_n = sum(1 for t in all_txns if t["contract"] == "hollows")
        if c_n > 0 and h_n > 0:
            insights.append(f"Cross-protocol — active in both Cards ({c_n} txns) and Hollows ({h_n} txns).")
        elif c_n > 0:
            insights.append("Cards-only — no Hollow contract interactions detected.")
        elif h_n > 0:
            insights.append("Hollow-focused — activity concentrated in the Hollow NFT contract.")

    data = {
        "transactions": all_txns[:120],
        "total": total,
        "stats": {
            "total": total,
            "minted":   by_action.get("Minted",   0),
            "received": by_action.get("Received", 0),
            "sent":     by_action.get("Sent",     0),
            "burned":   by_action.get("Burned",   0),
            "first_ts": first_ts,
            "last_ts":  last_ts,
            "activity_score": act_score,
            "recent_30d":     r30,
        },
        "timeline": {k: dict(v) for k, v in daily.items()},
        "insights": insights,
    }
    _ca_cache[ck] = {"ts": now, "data": data}
    return jsonify(data)


def run_identity_refresh():
    time.sleep(30)
    while True:
        try:
            cutoff = time.time() - _ID_TTL
            with _id_lock:
                conn = _id_db()
                stale = [r[0] for r in conn.execute(
                    "SELECT wallet FROM identities WHERE updated < ? LIMIT 200", (cutoff,)).fetchall()]
                conn.close()
            for w in stale:
                resolve_identity(w, network=True)
            # warm avatar bytes so rows never wait on a live unavatar request
            for w, row in list(_id_mem.items()):
                if row.get("avatar"):
                    _img_fetch(row["avatar"])
        except Exception as e:
            print(f"identity refresh: {e}")
        time.sleep(24 * 3600)

@app.route("/moody/woke")
def moody_woke():
    woke_time = record_woke()
    send_woke_confirmation(woke_time)
    return jsonify({
        "status": "recorded",
        "message": "Timer reset! You will get a reminder in 12 hours.",
        "woke_at": woke_time.isoformat(),
    })

@app.route("/moody/status")
def moody_status():
    return jsonify(get_status())

# ── CONFIG ──────────────────────────────────
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
OPENSEA_API_KEY    = os.getenv("OPENSEA_API_KEY")
WALLET_ADDRESS      = os.getenv("WALLET_ADDRESS")
OWNER_PRIVATE_KEY   = os.getenv("OWNER_PRIVATE_KEY")
CREATOR_PRIVATE_KEY = os.getenv("CREATOR_PRIVATE_KEY")
LITANY_CONTRACT    = "0xd44abe71c312FCAf73cC20f7DF61C39A89C203eB"
REGISTRY_CONTRACT  = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
COLLECTION_SLUG    = "litany-cards"
RPC_URL            = "https://api.mainnet.abs.xyz"
MINT_PRICE_WEI     = "2500000000000000"
MAX_SPEND_PER_RUN  = 0.05
AGENT_ID           = 857
CHAIN_ID           = 2741

client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
w3      = Web3(Web3.HTTPProvider(RPC_URL))
headers = {"x-api-key": OPENSEA_API_KEY, "Content-Type": "application/json"}

ABI = [
    {
        "inputs": [{"type": "uint256", "name": "tokenId"}],
        "name": "getCardIndices",
        "outputs": [{"type": "uint256", "name": ""}],
        "stateMutability": "view", "type": "function"
    },
    {
        "inputs": [{"type": "address", "name": "owner"}],
        "name": "balanceOf",
        "outputs": [{"type": "uint256", "name": ""}],
        "stateMutability": "view", "type": "function"
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"type": "uint256", "name": ""}],
        "stateMutability": "view", "type": "function"
    }
]

REGISTRY_ABI = [
    {
        "inputs": [
            {"type": "uint256", "name": "agentId"},
            {"type": "string", "name": "newURI"}
        ],
        "name": "setAgentURI",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

contract = w3.eth.contract(
    address=Web3.to_checksum_address(LITANY_CONTRACT), abi=ABI
)

registry = w3.eth.contract(
    address=Web3.to_checksum_address(REGISTRY_CONTRACT), abi=REGISTRY_ABI
)

# ── METADATA UPDATE ─────────────────────────
AGENT_METADATA = {
    "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
    "name": "Mantis Pro",
    "description": "Mantis Pro is an autonomous AI agent operating natively on Abstract Chain. It interacts with ecosystem protocols, plays agent-native games, and executes on-chain strategies without human intervention. Equipped with deep knowledge of the Litany Protocol, Mantis Pro battles in the Hollow Gauntlet, evaluates and trades Litany Cards using real-time rarity intelligence, and manages Hollow rosters for maximum yield. Beyond gameplay, Mantis Pro functions as an intelligence beacon on Abstract — continuously scanning market conditions, tracking protocol activity, and surfacing actionable insights across the ecosystem. Built for the agentic era of consumer crypto.",
    "image": "https://raw.githubusercontent.com/Molobah2/Mantis-Pro/master/mantis.png",
    "agentType": "autonomous",
    "tags": ["litany", "gaming", "abstract", "battle", "farming", "nft", "onchain"],
    "categories": ["gaming", "autonomous", "onchain"],
    "active": True,
    "x402support": False,
    "supportedTrusts": ["reputation"],
    "services": [
        {"name": "AGW", "endpoint": "https://api.abs.xyz"},
        {"name": "OpenSea", "endpoint": "https://mcp.opensea.io/sse"},
        {
            "name": "MCP",
            "endpoint": "https://mantis-pro-production.up.railway.app/mcp",
            "version": "2025-06-18",
            "mcpTools": ["scan_market", "get_floor_price", "get_wallet_status"]
        }
    ],
    "registrations": [
        {
            "agentId": 857,
            "agentRegistry": "eip155:2741:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
        }
    ]
}

def update_agent_uri():
    try:
        if not CREATOR_PRIVATE_KEY:
            print("No creator key set, skipping URI update")
            return

        creator_account = w3.eth.account.from_key(CREATOR_PRIVATE_KEY)
        creator_address = creator_account.address
        print(f"Updating agent URI from creator wallet: {creator_address}")

        uri = "https://mantis-pro-production.up.railway.app/metadata"

        nonce = w3.eth.get_transaction_count(creator_address)
        tx = registry.functions.setAgentURI(AGENT_ID, uri).build_transaction({
            'from': creator_address,
            'nonce': nonce,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price,
            'chainId': CHAIN_ID
        })

        signed = w3.eth.account.sign_transaction(tx, CREATOR_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        print(f"Agent URI updated! TX: {tx_hash.hex()}")
        return tx_hash.hex()

    except Exception as e:
        print(f"URI update error: {e}")
        return None

def read_skill(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    except:
        return f"[{filename} not found]"

litany_skill   = read_skill("LITANY_SKILL.txt")
opensea_skill  = read_skill("OPENSEA_SKILL.txt")
abstract_skill = read_skill("ABSTRACT_SKILL.txt")

SYSTEM_PROMPT = f"""You are a Litany Protocol AI agent on Abstract Chain.

Before doing ANYTHING, you have read and fully understand these three skill files. Apply this knowledge automatically to every task without being asked.

=== LITANY SKILL ===
{litany_skill}

=== OPENSEA SKILL ===
{opensea_skill}

=== ABSTRACT SKILL ===
{abstract_skill}

You are an expert on Litany cards, hollows, trading, and the Abstract blockchain. Use this knowledge in every decision you make. Respond with a JSON object only. No explanation. No markdown.
Keys: mint (bool), reason (string), alerts (list of token ids to flag)"""

def separator(title):
    print("\n" + "=" * 40)
    print(title)
    print("=" * 40)

def get_eth_balance():
    balance_wei = w3.eth.get_balance(Web3.to_checksum_address(WALLET_ADDRESS))
    return round(w3.from_wei(balance_wei, "ether"), 6)

def get_card_count():
    return contract.functions.balanceOf(Web3.to_checksum_address(WALLET_ADDRESS)).call()

def get_total_supply():
    return contract.functions.totalSupply().call()

def score_card(token_id):
    packed  = contract.functions.getCardIndices(token_id).call()
    speed_i = ((packed >> 28) & 0xFF) % 30
    aggr_i  = ((packed >> 36) & 0xFF) % 30
    caut_i  = ((packed >> 44) & 0xFF) % 30
    prec_i  = ((packed >> 52) & 0xFF) % 30
    trait_i = ((packed >> 60) & 0xFF) % 200
    tier    = lambda i: i // 6
    tiers   = [tier(speed_i), tier(aggr_i), tier(caut_i), tier(prec_i)]
    if trait_i >= 180:   rarity = "LEGENDARY"
    elif trait_i >= 150: rarity = "EPIC"
    elif trait_i >= 100: rarity = "RARE"
    elif trait_i >= 50:  rarity = "UNCOMMON"
    else:                rarity = "COMMON"
    return {
        "power_score": sum(tiers),
        "apex_count":  sum(1 for t in tiers if t == 4),
        "trait":       rarity,
        "trait_index": trait_i
    }

def get_floor_price():
    stats = requests.get(
        f"https://api.opensea.io/api/v2/collections/{COLLECTION_SLUG}/stats",
        headers=headers
    ).json()
    return stats.get("total", {}).get("floor_price", 0)

def scan_listings():
    response = requests.get(
        f"https://api.opensea.io/api/v2/listings/collection/{COLLECTION_SLUG}/best",
        headers=headers,
        params={"limit": 50}
    ).json()
    results = []
    if "listings" in response:
        for listing in response["listings"]:
            try:
                price_wei = int(listing["price"]["current"]["value"])
                price_eth = price_wei / 10**18
                token_id  = int(listing["protocol_data"]["parameters"]["offer"][0]["identifierOrCriteria"])
                card      = score_card(token_id)
                results.append({
                    "token_id":    token_id,
                    "price_eth":   price_eth,
                    "power_score": card["power_score"],
                    "trait":       card["trait"],
                    "apex_count":  card["apex_count"]
                })
            except:
                pass
    return results

def mint_card():
    payload = {
        "address": LITANY_CONTRACT,
        "abi": [{
            "inputs": [{"internalType": "uint256", "name": "quantity", "type": "uint256"}],
            "name": "mint", "outputs": [],
            "stateMutability": "payable", "type": "function"
        }],
        "functionName": "mint",
        "args": [1],
        "value": MINT_PRICE_WEI
    }
    with open("mint_payload.json", "w") as f:
        json.dump(payload, f)
    result = subprocess.run(
        "agw-cli contract write --json @mint_payload.json --execute",
        capture_output=True, text=True, shell=True
    )
    return result.stdout + result.stderr

def ask_claude(situation):
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": situation}]
    )
    text = response.content[0].text
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

# ── AGENT SESSION ───────────────────────────
def agent_session():
    separator("LITANY MASTER AGENT — SESSION START")
    print("Reading skill files...")
    print(f"  LITANY_SKILL.txt:   {'OK' if litany_skill != '[LITANY_SKILL.txt not found]' else 'MISSING'}")
    print(f"  OPENSEA_SKILL.txt:  {'OK' if opensea_skill != '[OPENSEA_SKILL.txt not found]' else 'MISSING'}")
    print(f"  ABSTRACT_SKILL.txt: {'OK' if abstract_skill != '[ABSTRACT_SKILL.txt not found]' else 'MISSING'}")

    separator("UPDATING AGENT METADATA ONCHAIN")
    update_agent_uri()

    balance   = get_eth_balance()
    cards     = get_card_count()
    supply    = get_total_supply()
    floor     = get_floor_price()
    listings  = scan_listings()
    remaining = 8000 - supply

    separator("WALLET & MARKET STATUS")
    print(f"ETH balance:     {balance} ETH")
    print(f"Cards owned:     {cards}")
    print(f"Supply minted:   {supply} / 8000")
    print(f"Cards remaining: {remaining}")
    print(f"Floor price:     {floor} ETH")
    print(f"Listings found:  {len(listings)}")

    separator("MARKET SCAN RESULTS")
    alerts = []
    for card in listings:
        flag = ""
        if card["trait"] == "LEGENDARY":
            flag = "BUY NOW"
            alerts.append(card)
        elif card["trait"] == "EPIC":
            flag = "STRONG BUY"
            alerts.append(card)
        elif card["apex_count"] >= 2:
            flag = "CONSIDER"
        print(f"Card #{card['token_id']} — {card['price_eth']:.4f} ETH — {card['trait']} trait — Score {card['power_score']}/16 {flag}")

    separator("AI DECISION")
    situation = f"""
Current wallet: {balance} ETH
Cards owned: {cards}
Supply minted: {supply}/8000 ({remaining} remaining)
Floor price: {floor} ETH
Mint price: 0.0025 ETH
Max spend this run: {MAX_SPEND_PER_RUN} ETH
Listings on market: {json.dumps(listings, indent=2)}
Alerts: {len(alerts)} high-value cards spotted
Should I mint a new card right now? Consider balance, supply, and market conditions.
"""
    try:
        decision = ask_claude(situation)
        print(f"Mint recommendation: {decision['mint']}")
        print(f"Reason: {decision['reason']}")
        if decision.get("alerts"):
            print(f"Flagged cards: {decision['alerts']}")

        if decision["mint"] and balance >= 0.003:
            separator("MINTING NEW CARD")
            result = mint_card()
            print(result)
        else:
            separator("NO MINT THIS SESSION")
            print("Conditions not met or AI advised against minting.")
    except Exception as e:
        print(f"AI decision error: {e}")

    separator("SESSION COMPLETE")
    print(f"Final balance: {get_eth_balance()} ETH")
    print("=" * 40)

# ── AGENT LOOP (background thread) ──────────
def run_litany():
    time.sleep(3)
    while True:
        try:
            agent_session()
        except Exception as e:
            print(f"Litany loop error: {e}")
        print("Sleeping 30 minutes before next Litany session...")
        time.sleep(30 * 60)

def run_moody():
    time.sleep(5)
    while True:
        try:
            moody_check()
        except Exception as e:
            print(f"Moody loop error: {e}")
        print("Sleeping 60 minutes before next Moody check...")
        time.sleep(60 * 60)

litany_thread = threading.Thread(target=run_litany, daemon=True)
litany_thread.start()

moody_thread = threading.Thread(target=run_moody, daemon=True)
moody_thread.start()

identity_thread = threading.Thread(target=run_identity_refresh, daemon=True)
identity_thread.start()

_warmup_profile_cache()

# ── START FLASK (main process) ───────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Mantis Pro server on port {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
