import os
import json
import requests
import subprocess
import threading
import time
from web3 import Web3
from dotenv import load_dotenv
import anthropic
from flask import Flask, jsonify

load_dotenv()

# ── BUNNY BUTTON ────────────────────────────
from bunny_routes import register_bunny_routes
from bunny_agent import bunny_session

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

register_bunny_routes(app)

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

@app.route("/bunny/done")
def bunny_done():
    from bunny_agent import _mark_done, _daily_state
    from flask import request
    task = request.args.get("task", "").strip()
    valid_tasks = [
        "press_button", "energy_potion", "claim_farm", "streak",
        "steal_upgrade", "regen_upgrade", "buy_farm", "breed_upgrade",
        "connect_x", "connect_discord", "refer_friends", "join_party",
        "stake_carrots", "creator_content"
    ]
    if not task:
        return jsonify({"error": "Pass ?task=task_name", "valid_tasks": valid_tasks}), 400
    if task not in valid_tasks:
        return jsonify({"error": f"Unknown task: {task}", "valid_tasks": valid_tasks}), 400
    _mark_done(task)
    return jsonify({"status": "done", "task": task, "message": f"Task '{task}' marked complete. Mantis won't remind you about it today."})

@app.route("/bunny/tasks")
def bunny_tasks():
    from bunny_agent import _daily_state
    return jsonify({
        "date": _daily_state.get("date", "?"),
        "tasks_done": list(_daily_state.get("tasks_done", set())),
    })

@app.route("/bunny/dashboard")
def bunny_dashboard():
    import os
    dashboard_path = os.path.join(os.path.dirname(__file__), "bunny_dashboard.html")
    with open(dashboard_path, "r", encoding="utf-8") as f:
        html = f.read()
    from flask import Response
    return Response(html, mimetype="text/html")

@app.route("/api/bunny-proxy")
def bunny_proxy():
    from flask import request as freq
    import requests as req2
    endpoint = freq.args.get("endpoint", "/leaderboard")
    allowed = ["/leaderboard", "/eth-price", "/presale/status", "/party/list"]
    if not any(endpoint.startswith(a) for a in allowed):
        return jsonify({"error": "endpoint not allowed"}), 403
    try:
        resp = req2.get(
            f"https://www.bunnybutton.xyz/api{endpoint}",
            headers={"Accept": "application/json"},
            timeout=8
        )
        from flask import Response
        r = Response(resp.content, status=resp.status_code, content_type="application/json")
        r.headers['Access-Control-Allow-Origin'] = '*'
        return r
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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

def run_bunny():
    time.sleep(5)
    while True:
        try:
            bunny_session()
            moody_check()
        except Exception as e:
            print(f"Bunny loop error: {e}")
        print("Sleeping 60 minutes before next Bunny session...")
        time.sleep(60 * 60)

litany_thread = threading.Thread(target=run_litany, daemon=True)
litany_thread.start()

bunny_thread = threading.Thread(target=run_bunny, daemon=True)
bunny_thread.start()

# ── START FLASK (main process) ───────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting Mantis Pro server on port {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
