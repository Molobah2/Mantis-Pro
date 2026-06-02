#!/usr/bin/env python3
"""
Litany Card Rarity Indexer
===========================
Reads tokenURI for every minted Litany Card on Abstract, builds the phrase/trait
frequency distribution, computes a per-card rarity score, and writes rarity_index.json.

This is the indexer behind the Rarity Engine. Run it once (re-run to refresh).
It must run somewhere with internet access to the Abstract RPC — your machine or a
Railway one-off job — NOT inside a sandbox.

    pip install requests
    python build_rarity_index.py

Output: rarity_index.json  ->  commit it next to agent.py and deploy.
The /litany/rarity page reads it via /rarity_index.json.
"""
import json, base64, time
from concurrent.futures import ThreadPoolExecutor
import requests

RPC      = "https://api.mainnet.abs.xyz"
CARDS    = "0xd44abe71c312FCAf73cC20f7DF61C39A89C203eB"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64

_id = 0
def rpc(method, params):
    global _id; _id += 1
    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": _id, "method": method, "params": params}, timeout=30)
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]

def hexint(h):
    return int(h, 16)

def decode_string(result):
    """Decode a single ABI-encoded string return from eth_call."""
    h = result[2:] if result.startswith("0x") else result
    if len(h) < 128:
        return ""
    length = int(h[64:128], 16)
    data = h[128:128 + length * 2]
    return bytes.fromhex(data).decode("utf-8", errors="replace")

def token_uri(token_id):
    data = "0xc87b56dd" + format(token_id, "x").rjust(64, "0")   # tokenURI(uint256)
    res = rpc("eth_call", [{"to": CARDS, "data": data}, "latest"])
    uri = decode_string(res)
    i = uri.find("base64,")
    if i < 0:
        return None
    return json.loads(base64.b64decode(uri[i + 7:]))

def minted_token_ids():
    latest = hexint(rpc("eth_blockNumber", []))
    ids = []
    def scan(frm, to):
        logs = rpc("eth_getLogs", [{"address": CARDS, "topics": [TRANSFER, ZERO_TOPIC],
                                    "fromBlock": hex(frm), "toBlock": hex(to)}])
        for l in logs:
            ids.append(int(l["topics"][3], 16))
    try:
        scan(0, latest)                      # one shot if the RPC allows it
    except Exception:
        step = 100_000                       # otherwise chunk
        frm = 0
        while frm <= latest:
            for _ in range(3):
                try:
                    scan(frm, min(frm + step - 1, latest)); break
                except Exception:
                    time.sleep(1.0)
            frm += step
    return sorted(set(ids))

def main():
    print("Resolving minted Litany Cards from mint events…")
    ids = minted_token_ids()
    print(f"  found {len(ids)} minted cards. Reading onchain metadata…")
    if not ids:
        print("  no minted cards found — nothing to index yet.")
        return

    cards = {}
    def fetch(tid):
        for _ in range(3):
            try:
                return tid, token_uri(tid)
            except Exception:
                time.sleep(0.4)
        return tid, None

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for tid, meta in ex.map(fetch, ids):
            done += 1
            if meta:
                attrs = {}
                for a in meta.get("attributes", []):
                    if a and a.get("trait_type") is not None:
                        attrs[a["trait_type"]] = a.get("value")
                cards[tid] = {"name": meta.get("name", ""), "attrs": attrs}
            if done % 200 == 0:
                print(f"  {done}/{len(ids)}")

    total = len(cards)
    # frequency of every (trait_type -> value)
    freq = {}
    for c in cards.values():
        for k, v in c["attrs"].items():
            freq.setdefault(k, {})
            sv = str(v)
            freq[k][sv] = freq[k].get(sv, 0) + 1

    # classic rarity: sum of 1 / (value frequency) across a card's traits
    scored = []
    for tid, c in cards.items():
        s = 0.0
        for k, v in c["attrs"].items():
            cnt = freq[k].get(str(v), 1)
            s += 1.0 / cnt
        scored.append({"tokenId": tid, "name": c["name"], "attrs": c["attrs"], "score": round(s, 4)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, c in enumerate(scored):
        c["rank"] = i + 1

    out = {
        "generatedAt": int(time.time()),
        "totalCards": total,
        "traits": freq,
        "cards": scored,
    }
    with open("rarity_index.json", "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote rarity_index.json — {total} cards, {len(freq)} trait types.")

if __name__ == "__main__":
    main()
