import re
import time
import requests as _req
from . import store

_catalog_cache = {"ts": 0.0, "apps": []}
_CATALOG_TTL   = 3600  # 1-hour cache

_PORTAL_APIS = [
    "https://backend.portal.abs.xyz/api/apps",
    "https://abs.xyz/api/apps",
    "https://abs.xyz/api/discover/apps",
]
_PORTAL_DISCOVER_URL = "https://abs.xyz/explore"

# Upvote contract event: Upvoted(address indexed voter, uint256 indexed appId, uint256 indexed epoch)
_UPVOTE_CONTRACT  = "0x3b50de27506f0a8c1f4122a1e6f470009a76ce2a"
_UPVOTED_TOPIC    = "0xea66f58e474bc09f580000e81f31b334d171db387d0c6098ba47bd897741679b"


def _fetch_via_api():
    headers = {"Accept": "application/json", "User-Agent": "MantisBot/1.0"}
    for url in _PORTAL_APIS:
        try:
            r = _req.get(url, timeout=10, headers=headers)
            if r.status_code != 200:
                continue
            data  = r.json()
            items = (data.get("apps") or data.get("data") or data.get("items")
                     or data.get("results") or (data if isinstance(data, list) else None))
            if not items:
                continue
            apps = []
            for item in items:
                app_id = item.get("id") or item.get("appId") or item.get("app_id")
                name   = (item.get("name") or item.get("title") or item.get("appName")
                          or f"App #{app_id}")
                app_url = item.get("url") or item.get("link") or ""
                if app_id is not None:
                    apps.append({"id": int(app_id), "name": str(name), "url": str(app_url)})
            if apps:
                print(f"[catalog] API hit {url}: {len(apps)} apps")
                return apps
        except Exception as e:
            print(f"[catalog] API {url}: {e}")
    return []


def _fetch_via_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[catalog] Playwright not available")
        return []

    apps  = []
    seen  = set()
    urls  = [_PORTAL_DISCOVER_URL, "https://abs.xyz/apps", "https://abs.xyz/discover"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for url in urls:
                try:
                    page.goto(url, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(3000)
                    content = page.content()

                    # Strategy 1: href="/apps/123" or href="/app/123"
                    for m in re.finditer(r'href=["\'][^"\']*?/apps?/(\d+)["\']', content):
                        app_id = int(m.group(1))
                        if app_id not in seen:
                            seen.add(app_id)
                            apps.append({"id": app_id, "name": f"App #{app_id}",
                                         "url": f"https://abs.xyz/apps/{app_id}"})

                    # Strategy 2: JSON blobs with appId + name
                    for m in re.finditer(
                        r'"(?:appId|app_id|id)"\s*:\s*(\d+)[^}]{0,200}?'
                        r'"(?:name|title|appName)"\s*:\s*"([^"]+)"',
                        content, re.S
                    ):
                        app_id, name = int(m.group(1)), m.group(2).strip()
                        if app_id not in seen:
                            seen.add(app_id)
                            apps.append({"id": app_id, "name": name,
                                         "url": f"https://abs.xyz/apps/{app_id}"})

                    if apps:
                        break
                except Exception as e:
                    print(f"[catalog] playwright {url}: {e}")
        finally:
            browser.close()

    print(f"[catalog] Playwright: {len(apps)} apps")
    return apps


def _fetch_via_blockchain():
    """
    Query upvote contract event logs on Abstract to discover active appIds.
    Event: Upvoted(address indexed voter, uint256 indexed appId, uint256 indexed epoch)
    topic2 = appId, topic3 = epoch
    """
    from .config import get_rpc
    rpc = get_rpc()
    try:
        r = _req.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}, timeout=10)
        latest = int(r.json()["result"], 16)
        from_block = max(0, latest - 5000)

        r2 = _req.post(rpc, json={
            "jsonrpc": "2.0", "id": 2, "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(from_block),
                "toBlock":   "latest",
                "address":   _UPVOTE_CONTRACT,
                "topics":    [_UPVOTED_TOPIC],
            }],
        }, timeout=30)
        logs = r2.json().get("result", [])
        if not logs:
            return []

        # Determine current epoch (most frequent in recent logs)
        epoch_counts: dict[int, int] = {}
        for log in logs:
            topics = log.get("topics", [])
            if len(topics) >= 4:
                ep = int(topics[3], 16)
                epoch_counts[ep] = epoch_counts.get(ep, 0) + 1
        if not epoch_counts:
            return []
        current_epoch = max(epoch_counts, key=epoch_counts.get)

        seen: set[int] = set()
        apps = []
        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 4:
                continue
            if int(topics[3], 16) != current_epoch:
                continue
            app_id = int(topics[2], 16)
            if app_id not in seen:
                seen.add(app_id)
                apps.append({
                    "id":   app_id,
                    "name": f"App #{app_id}",
                    "url":  f"https://abs.xyz/apps/{app_id}",
                })

        apps.sort(key=lambda a: a["id"])
        print(f"[catalog] Blockchain: {len(apps)} apps from epoch {current_epoch}")
        return apps
    except Exception as e:
        print(f"[catalog] Blockchain fetch failed: {e}")
        return []


def fetch_catalog():
    """Try Portal API → blockchain events → Playwright."""
    apps = _fetch_via_api()
    if not apps:
        apps = _fetch_via_blockchain()
    if not apps:
        apps = _fetch_via_playwright()
    if not apps:
        print("[catalog] All sources returned empty — using stored catalog")
    return apps


def get_catalog(force_refresh=False):
    now = time.time()
    if not force_refresh and _catalog_cache["apps"] and now - _catalog_cache["ts"] < _CATALOG_TTL:
        return _catalog_cache["apps"]

    apps = fetch_catalog()
    if apps:
        store.upsert_apps(apps)
        _catalog_cache["apps"] = apps
        _catalog_cache["ts"]   = now
    else:
        stored = store.get_apps()
        if stored:
            _catalog_cache["apps"] = stored
            _catalog_cache["ts"]   = now - _CATALOG_TTL // 2   # retry sooner

    return _catalog_cache["apps"]
