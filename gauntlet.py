"""
Litany demo gauntlet runner for Mantis Pro.
Runs headless Playwright battles in Railway (cloud).
Auth state (cookies + localStorage) persisted in SQLite between runs.
Wallet signing bridged from JS → Python using GAUNTLET_PRIVATE_KEY.
"""

import os
import json
import time
import asyncio
import sqlite3
import threading
import re

from eth_account import Account
from eth_account.messages import encode_defunct

GAUNTLET_KEY  = os.environ.get("GAUNTLET_PRIVATE_KEY", "").strip()
GAUNTLET_ADDR = os.environ.get("GAUNTLET_ADDRESS", "").strip().lower()
DEMO_BASE     = "https://litany.gg/demo"
DASHBOARD_URL = f"{DEMO_BASE}/dashboard"

SECTORS = {
    "surge": {
        "name": "Surge Sector",
        "url":  f"{DEMO_BASE}/crawl/sector_0/prep",
        "enter_kw": "SECTOR",
        "stages":   3,
        "hollows":  ["Monolith", "Shellvoid"],
    },
    "rigid": {
        "name": "Rigid Sector",
        "url":  f"{DEMO_BASE}/crawl/sector_4/prep",
        "enter_kw": "SECTOR",
        "stages":   3,
        "hollows":  ["Echoshade", "Primordia"],
    },
    "drift": {
        "name": "The Drift",
        "url":  f"{DEMO_BASE}/crawl/drift/prep",
        "enter_kw": "DRIFT",
        "stages":   5,
        "hollows":  ["Monolith", "Shellvoid", "Primordia"],
    },
}

_DB_PATH = os.path.join(os.path.dirname(__file__), "identities.db")
_g_lock  = threading.Lock()

# ── current run state (thread-safe) ─────────────────────────────────────────
_run_state = {
    "running":   False,
    "sector":    None,
    "hollow":    None,
    "stage":     0,
    "started":   None,
    "last_log":  "",
}
_run_state_lock = threading.Lock()

def _set_run_state(**kwargs):
    with _run_state_lock:
        _run_state.update(kwargs)

def get_run_state():
    with _run_state_lock:
        return dict(_run_state)

# ── SQLite helpers ────────────────────────────────────────────────────────────

def _gdb():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS gauntlet_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          REAL    NOT NULL,
        sector      TEXT,
        hollow      TEXT,
        result      TEXT,
        stages_won  INTEGER DEFAULT 0,
        stages_total INTEGER DEFAULT 0,
        pearl_earned INTEGER DEFAULT 0,
        log         TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gauntlet_state (
        key     TEXT PRIMARY KEY,
        value   TEXT,
        updated REAL NOT NULL
    )""")
    conn.commit()
    return conn

def _state_get(key):
    try:
        with _g_lock:
            conn = _gdb()
            row  = conn.execute(
                "SELECT value FROM gauntlet_state WHERE key=?", (key,)
            ).fetchone()
            conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"gauntlet state_get {key}: {e}")
        return None

def _state_put(key, value):
    try:
        with _g_lock:
            conn = _gdb()
            conn.execute(
                "INSERT OR REPLACE INTO gauntlet_state(key,value,updated) VALUES(?,?,?)",
                (key, value, time.time())
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"gauntlet state_put {key}: {e}")

def _log_run(sector, hollow, result, stages_won, stages_total, pearl_earned, log_text):
    try:
        with _g_lock:
            conn = _gdb()
            conn.execute(
                "INSERT INTO gauntlet_runs"
                "(ts,sector,hollow,result,stages_won,stages_total,pearl_earned,log)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), sector, hollow, result,
                 stages_won, stages_total, pearl_earned, log_text)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"gauntlet log_run: {e}")

def get_stats():
    """Return aggregate gauntlet stats + recent run history."""
    try:
        with _g_lock:
            conn = _gdb()
            rows = conn.execute(
                "SELECT id,ts,sector,hollow,result,stages_won,stages_total,pearl_earned,log"
                " FROM gauntlet_runs ORDER BY ts DESC LIMIT 200"
            ).fetchall()
            conn.close()

        runs = [{
            "id":           r[0], "ts":     r[1], "sector": r[2],
            "hollow":       r[3], "result": r[4], "stages_won":  r[5],
            "stages_total": r[6], "pearl":  r[7], "log":     (r[8] or ""),
        } for r in rows]

        total   = len(runs)
        wins    = sum(1 for r in runs if r["result"] == "win")
        pearl   = sum(r["pearl"] for r in runs)
        now     = time.time()
        today   = sum(1 for r in runs if now - r["ts"] < 86400)

        by_sector = {}
        for r in runs:
            s = r["sector"] or "unknown"
            if s not in by_sector:
                by_sector[s] = {"total": 0, "wins": 0, "pearl": 0}
            by_sector[s]["total"] += 1
            by_sector[s]["wins"]  += 1 if r["result"] == "win" else 0
            by_sector[s]["pearl"] += r["pearl"]

        return {
            "runs":       runs[:100],
            "total":      total,
            "wins":       wins,
            "win_rate":   round(wins / total * 100) if total else 0,
            "pearl_total": pearl,
            "today":      today,
            "by_sector":  by_sector,
            "running":    get_run_state(),
        }
    except Exception as e:
        print(f"gauntlet get_stats: {e}")
        return {
            "runs": [], "total": 0, "wins": 0, "win_rate": 0,
            "pearl_total": 0, "today": 0, "by_sector": {}, "running": get_run_state(),
        }

# ── Signing bridge (Python side) ──────────────────────────────────────────────

def _sign_personal(msg_hex: str) -> str:
    """Sign personal_sign / eth_sign challenge using GAUNTLET_PRIVATE_KEY."""
    if not GAUNTLET_KEY:
        print("gauntlet sign: GAUNTLET_PRIVATE_KEY not set")
        return "0x" + "00" * 65
    try:
        raw = bytes.fromhex(msg_hex[2:] if msg_hex.startswith("0x") else msg_hex)
        msg    = encode_defunct(primitive=raw)
        signed = Account.sign_message(msg, private_key=GAUNTLET_KEY)
        return "0x" + signed.signature.hex()
    except Exception as e:
        print(f"gauntlet sign_personal: {e}")
        return "0x" + "00" * 65

def _sign_typed(typed_json: str) -> str:
    """Sign EIP-712 typed data using GAUNTLET_PRIVATE_KEY."""
    if not GAUNTLET_KEY:
        return "0x" + "00" * 65
    try:
        data   = json.loads(typed_json)
        domain = data.get("domain", {})
        types  = {k: v for k, v in data.get("types", {}).items() if k != "EIP712Domain"}
        message = data.get("message", {})
        signed  = Account.sign_typed_data(
            private_key=GAUNTLET_KEY,
            domain_data=domain,
            message_types=types,
            message_data=message,
        )
        return "0x" + signed.signature.hex()
    except Exception as e:
        print(f"gauntlet sign_typed: {e}")
        return "0x" + "00" * 65

# ── window.ethereum injection script ─────────────────────────────────────────

def _provider_js(address: str) -> str:
    addr = address.lower()
    return f"""
(function() {{
    const ADDR     = '{addr}';
    const CHAIN_ID = '0xab5';

    const listeners = {{}};
    const provider = {{
        isMetaMask:      true,
        selectedAddress: ADDR,
        chainId:         CHAIN_ID,
        networkVersion:  '2741',

        on(ev, cb) {{
            listeners[ev] = listeners[ev] || [];
            listeners[ev].push(cb);
            return this;
        }},
        removeListener(ev, cb) {{
            if (listeners[ev])
                listeners[ev] = listeners[ev].filter(x => x !== cb);
            return this;
        }},
        emit(ev, ...args) {{
            (listeners[ev] || []).forEach(cb => {{ try {{ cb(...args); }} catch(_) {{}} }});
        }},

        async request(args) {{
            const method = args && args.method;
            switch (method) {{
                case 'eth_requestAccounts':
                case 'eth_accounts':
                    this.selectedAddress = ADDR;
                    this.emit('accountsChanged', [ADDR]);
                    return [ADDR];

                case 'eth_chainId':         return CHAIN_ID;
                case 'net_version':         return '2741';
                case 'eth_blockNumber':     return '0x0';
                case 'eth_getBalance':      return '0x0';
                case 'wallet_switchEthereumChain':
                case 'wallet_addEthereumChain':
                    return null;

                case 'personal_sign': {{
                    // params: [message, address]
                    const msg = args.params[0];
                    if (!window.__pwSignPersonal)
                        throw new Error('signing bridge not ready');
                    return await window.__pwSignPersonal(msg);
                }}
                case 'eth_sign': {{
                    // params: [address, message]
                    const msg = args.params[1];
                    if (!window.__pwSignPersonal)
                        throw new Error('signing bridge not ready');
                    return await window.__pwSignPersonal(msg);
                }}
                case 'eth_signTypedData_v4':
                case 'eth_signTypedData_v3':
                case 'eth_signTypedData': {{
                    const raw = args.params[1];
                    const typedStr = typeof raw === 'string' ? raw : JSON.stringify(raw);
                    if (!window.__pwSignTyped)
                        throw new Error('signing bridge not ready');
                    return await window.__pwSignTyped(typedStr);
                }}
                default:
                    console.warn('[Mantis] ethereum.request: unhandled', method);
                    return null;
            }}
        }},
        send(method, params) {{ return this.request({{ method, params }}); }},
        sendAsync(args, cb) {{
            this.request(args)
                .then(r  => cb(null, {{ id: args.id, jsonrpc: '2.0', result: r }}))
                .catch(e => cb(e));
        }},
    }};

    try {{
        Object.defineProperty(window, 'ethereum', {{
            value: provider, writable: false, configurable: false,
        }});
    }} catch (_) {{
        window.ethereum = provider;
    }}

    // EIP-6963 provider announcement
    window.addEventListener('eip6963:requestProvider', () => {{
        window.dispatchEvent(new CustomEvent('eip6963:announceProvider', {{
            detail: {{
                info:     {{ uuid: 'mantis-gauntlet', name: 'Mantis Wallet',
                             icon: 'data:image/svg+xml,<svg/>', rdns: 'xyz.mantis' }},
                provider: window.ethereum,
            }}
        }}));
    }});
    window.dispatchEvent(new CustomEvent('eip6963:announceProvider', {{
        detail: {{
            info:     {{ uuid: 'mantis-gauntlet', name: 'Mantis Wallet',
                         icon: 'data:image/svg+xml,<svg/>', rdns: 'xyz.mantis' }},
            provider: window.ethereum,
        }}
    }}));
}})();
"""

# ── Battle helpers ────────────────────────────────────────────────────────────

async def _click_kw(page, keyword: str) -> bool:
    """Click the first button/link whose text contains keyword (case-insensitive)."""
    try:
        for el in await page.query_selector_all("button, a"):
            txt = (await el.inner_text()).strip().upper()
            if keyword.upper() in txt:
                await el.click()
                await page.wait_for_timeout(2000)
                return True
    except Exception as e:
        print(f"  [gauntlet] click_kw '{keyword}': {e}")
    return False

async def _extract_pearl(page) -> int:
    """Read PEARL count from result screen text."""
    try:
        text = await page.inner_text("body")
        for pat in [r"(\d+)\s*PEARL", r"PEARL[:\s]+(\d+)", r"earned[:\s]+(\d+)"]:
            m = re.search(pat, text, re.I)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0

async def _maybe_connect_wallet(page) -> bool:
    """
    If a wallet connect modal is visible, try to click through it.
    Prefers MetaMask / Injected / Browser Wallet over AGW-specific buttons.
    Returns True if we interacted with the modal.
    """
    try:
        content = await page.inner_text("body")
        connect_triggers = ["CONNECT WALLET", "CONNECT", "SIGN IN", "LOGIN", "GET STARTED"]
        if not any(k in content.upper() for k in connect_triggers):
            return False

        # First try clicking injected/MetaMask option in modal if present
        for kw in ["METAMASK", "INJECTED", "BROWSER WALLET", "INJECTED WALLET"]:
            if await _click_kw(page, kw):
                await page.wait_for_timeout(3000)
                return True

        # Fall back to generic connect button
        for kw in connect_triggers:
            if await _click_kw(page, kw):
                await page.wait_for_timeout(4000)
                # Handle any sign message prompt
                c2 = await page.inner_text("body")
                if "SIGN" in c2.upper():
                    await _click_kw(page, "SIGN")
                    await page.wait_for_timeout(3000)
                return True
    except Exception as e:
        print(f"  [gauntlet] maybe_connect_wallet: {e}")
    return False

async def _is_authed(page) -> bool:
    """True if we appear logged in (dashboard/game content visible)."""
    try:
        url  = page.url
        text = await page.inner_text("body")
        auth_signals = ["DASHBOARD", "PEARL", "GAUNTLET", "CRAWL", "HOLLOW", "SECTOR", "YOUR HOLLOWS"]
        return any(k in text.upper() for k in auth_signals) or "demo/dashboard" in url
    except Exception:
        return False

# ── Core battle runner ────────────────────────────────────────────────────────

async def run_battle(sector_key: str = "surge") -> dict:
    """
    Play one full gauntlet run in the given sector.
    Returns dict: {result, stages_won, pearl, hollow, sector}.
    Persists auth state and logs run to SQLite.
    """
    from playwright.async_api import async_playwright

    sector       = SECTORS.get(sector_key, SECTORS["surge"])
    hollow_used  = sector["hollows"][0]
    logs         = []

    def log(msg: str):
        print(f"  [gauntlet:{sector_key}] {msg}")
        logs.append(msg)
        _set_run_state(last_log=msg)

    if not GAUNTLET_ADDR:
        return {"result": "error", "stages_won": 0, "pearl": 0,
                "hollow": hollow_used, "sector": sector_key,
                "error": "GAUNTLET_ADDRESS not set"}

    _set_run_state(running=True, sector=sector_key, hollow=hollow_used,
                   stage=0, started=time.time(), last_log="Starting...")

    result     = "error"
    stages_won = 0
    pearl      = 0

    try:
        async with async_playwright() as pw:
            # Restore saved auth state (cookies + localStorage) if available
            ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
            saved = _state_get("storage_state")
            if saved:
                try:
                    ctx_kwargs["storage_state"] = json.loads(saved)
                    log("Restored auth state from DB")
                except Exception:
                    pass

            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-webgl",
                    "--disable-webgl2",
                    "--disable-3d-apis",
                    "--disable-accelerated-2d-canvas",
                    "--disable-accelerated-video-decode",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--disable-features=TranslateUI,VizDisplayCompositor",
                    "--js-flags=--max-old-space-size=256",
                ],
            )
            context = await browser.new_context(**ctx_kwargs)

            # Block heavy assets before any page loads to keep memory low
            await context.route(
                re.compile(
                    r"\.(png|jpg|jpeg|gif|webp|mp4|webm|ogg|mp3|wav|woff2?|ttf|otf)(\?.*)?$",
                    re.I,
                ),
                lambda route: route.abort(),
            )

            await context.add_init_script(_provider_js(GAUNTLET_ADDR))
            # Kill animations so the page stays lightweight
            await context.add_init_script("""
                const s = document.createElement('style');
                s.textContent = `*, *::before, *::after {
                    animation-duration: 0.001s !important;
                    transition-duration: 0.001s !important;
                }`;
                document.addEventListener('DOMContentLoaded', () => document.head.appendChild(s));
            """)

            # Auto-inject signing bridge into any popup the site opens
            async def _setup_page(p):
                try:
                    await p.expose_function("__pwSignPersonal", _sign_personal)
                    await p.expose_function("__pwSignTyped",    _sign_typed)
                except Exception:
                    pass
            context.on("page", lambda p: asyncio.ensure_future(_setup_page(p)))

            page = await context.new_page()
            await _setup_page(page)

            # Log /api/ calls for diagnostics
            intercepted = []
            page.on("request", lambda req: intercepted.append(req.url)
                    if "/api/" in req.url else None)

            try:
                # ── auth phase ──────────────────────────────────────────────
                # Navigate to landing and let the SPA auto-call
                # eth_requestAccounts via the injected window.ethereum.
                # Heavy assets are blocked; animations disabled.
                log("Navigating to demo landing...")
                await page.goto(DEMO_BASE, wait_until="domcontentloaded", timeout=30000)
                log("Waiting for SPA boot...")
                await page.wait_for_timeout(6000)

                try:
                    content = await page.inner_text("body")
                    log(f"Page snippet: {content[:300].strip()!r}")
                except Exception as e:
                    log(f"Could not read page body: {e}")
                    raise

                # Save whatever session state we have
                try:
                    ns = await context.storage_state()
                    _state_put("storage_state", json.dumps(ns))
                    log("Auth state saved")
                except Exception:
                    pass

                # ── navigate to sector prep ─────────────────────────────────
                log(f"Navigating to {sector['name']}...")
                await page.goto(sector["url"], wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)

                # Dismiss tutorial / any overlay
                try:
                    for btn in await page.query_selector_all("button"):
                        if (await btn.inner_text()).strip().lower() == "x":
                            await btn.click()
                            await page.wait_for_timeout(1000)
                            break
                except Exception:
                    pass

                # ── select hollow(s) ───────────────────────────────────────
                for hollow in sector["hollows"]:
                    try:
                        await page.get_by_text(hollow, exact=False).first.click()
                        hollow_used = hollow
                        log(f"Selected {hollow}")
                        await page.wait_for_timeout(2000)
                    except Exception:
                        log(f"Could not select {hollow}")

                _set_run_state(hollow=hollow_used)

                # ── enter sector ───────────────────────────────────────────
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)

                if not await _click_kw(page, f"ENTER {sector['enter_kw']}"):
                    await _click_kw(page, "ENTER")

                log("Entered sector")
                await page.wait_for_timeout(5000)

                # ── stage loop ─────────────────────────────────────────────
                for stage in range(1, sector["stages"] + 1):
                    _set_run_state(stage=stage)
                    log(f"Stage {stage}/{sector['stages']}...")

                    if "results" in page.url:
                        log("Already on results page")
                        result = "win"
                        break

                    await _click_kw(page, "BEGIN STAGE")
                    await page.wait_for_timeout(10000)

                    # Detect unexpected navigation away from crawl
                    if ("crawl" not in page.url and "battle" not in page.url
                            and "results" not in page.url):
                        log(f"Page left crawl: {page.url}")
                        await page.goto(DASHBOARD_URL)
                        break

                    content = await page.inner_text("body")

                    if "results" in page.url or "CRAWL COMPLETE" in content.upper():
                        log("Crawl complete!")
                        stages_won = stage
                        result     = "win"
                        await _click_kw(page, "VIEW RESULTS")
                        await page.wait_for_timeout(3000)
                        break

                    elif "VICTORY" in content.upper():
                        stages_won = stage
                        log(f"Stage {stage} VICTORY")
                        await _click_kw(page, "CONTINUE")
                        await page.wait_for_timeout(3000)

                        if "results" in page.url:
                            result = "win"
                            break

                        c2 = await page.inner_text("body")
                        if "CRAWL COMPLETE" in c2.upper():
                            result = "win"
                            await _click_kw(page, "VIEW RESULTS")
                            await page.wait_for_timeout(3000)
                            break

                        if stage == sector["stages"]:
                            result = "win"
                        elif stage < sector["stages"]:
                            await page.wait_for_timeout(2000)
                            await page.evaluate("window.scrollTo(0,0)")
                            await page.wait_for_timeout(500)
                            await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                            await page.wait_for_timeout(500)
                            await _click_kw(page, "NEXT STAGE")
                            await page.wait_for_timeout(5000)

                    elif "DEFEAT" in content.upper():
                        log(f"Stage {stage} DEFEAT")
                        result = "defeat"
                        break

                # ── extract PEARL ─────────────────────────────────────────
                await page.wait_for_timeout(3000)
                pearl = await _extract_pearl(page)
                log(f"PEARL earned: {pearl}")

                # ── return to dashboard & save fresh state ────────────────
                await _click_kw(page, "RETURN TO DASHBOARD")
                await page.wait_for_timeout(3000)

                try:
                    fs = await context.storage_state()
                    _state_put("storage_state", json.dumps(fs))
                except Exception:
                    pass

                # Log any API endpoints discovered
                api_calls = list(set(intercepted))
                if api_calls:
                    log(f"API calls observed: {', '.join(api_calls[:10])}")

            except Exception as e:
                log(f"Battle error: {e}")
                result = "error"

            finally:
                await browser.close()

    except Exception as e:
        log(f"Runner error: {e}")
        result = "error"

    finally:
        _set_run_state(running=False, last_log=f"Done: {result}")

    log(f"Final: {result} | stages_won={stages_won} | pearl={pearl}")
    _log_run(sector_key, hollow_used, result, stages_won,
             sector["stages"], pearl, "\n".join(logs))

    return {
        "result":     result,
        "stages_won": stages_won,
        "pearl":      pearl,
        "hollow":     hollow_used,
        "sector":     sector_key,
    }
