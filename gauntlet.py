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

# ── wagmi localStorage seed (bypasses RainbowKit connect button) ─────────────

def _wagmi_seed_js(address: str) -> str:
    """
    Pre-seed wagmi v2's zustand-persist 'store' key so it auto-reconnects
    on page load without the user clicking the (blocked) RainbowKit button.
    """
    addr = address.lower()
    return f"""
(function() {{
    const ADDR     = '{addr}';
    const CHAIN_ID = 2741;
    const UID      = 'injected';

    // wagmi v2 persists connection state at localStorage['store']
    const wagmiState = {{
        state: {{
            connections: {{
                __type: 'Map',
                value: [[UID, {{
                    accounts: [ADDR],
                    chainId:  CHAIN_ID,
                    connector: {{ id: 'injected', name: 'MetaMask',
                                  type: 'injected', uid: UID }}
                }}]]
            }},
            chainId: CHAIN_ID,
            current: UID
        }},
        version: 2
    }};

    try {{ localStorage.setItem('store', JSON.stringify(wagmiState)); }}
    catch(e) {{ console.warn('[Mantis] wagmi seed failed', e); }}

    // After the page boots, fire connection events so wagmi picks up our provider
    window.addEventListener('load', function() {{
        setTimeout(function() {{
            try {{
                if (window.ethereum) {{
                    window.ethereum.emit('connect',         {{ chainId: '0xab5' }});
                    window.ethereum.emit('accountsChanged', [ADDR]);
                }}
            }} catch(e) {{}}
        }}, 1500);
    }});
}})();
"""

# ── Battle helpers ────────────────────────────────────────────────────────────

async def _body_text(page, timeout: float = 5.0) -> str:
    """Read body text via JS eval with a hard timeout so a dead browser can't hang."""
    try:
        return await asyncio.wait_for(
            page.evaluate("document.body ? document.body.innerText : ''"),
            timeout=timeout,
        )
    except Exception:
        return ""

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
        text = await _body_text(page)
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
        content = await _body_text(page)
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
                c2 = await _body_text(page)
                if "SIGN" in c2.upper():
                    await _click_kw(page, "SIGN")
                    await page.wait_for_timeout(3000)
                return True
    except Exception as e:
        print(f"  [gauntlet] maybe_connect_wallet: {e}")
    return False

async def _is_authed(page) -> bool:
    """True if we appear logged in — requires game-specific content not on guest landing."""
    try:
        url  = page.url
        text = await _body_text(page)
        t    = text.upper()
        # Guest landing shows "HOLLOW GAUNTLET" / "CRAWL. FIGHT." marketing copy.
        # Authed dashboard shows balance info + hollow roster.
        authed = (
            "demo/dashboard" in url
            or "YOUR HOLLOWS" in t
            or ("PEARL" in t and "DASHBOARD" in t)
            or ("ETH" in t and "BALANCE" in t)
            or "SELECT HOLLOW" in t
        )
        return authed
    except Exception:
        return False

# ── Core battle runner ────────────────────────────────────────────────────────

async def run_battle(sector_key: str = "surge") -> dict:
    """
    Play one full gauntlet run in the given sector.
    Returns dict: {result, stages_won, pearl, hollow, sector}.
    Hard 90-second timeout prevents hung browser from blocking forever.
    """
    from playwright.async_api import async_playwright

    sector      = SECTORS.get(sector_key, SECTORS["surge"])
    hollow_used = sector["hollows"][0]
    logs        = []
    result      = "error"
    stages_won  = 0
    pearl       = 0

    def log(msg: str):
        print(f"  [gauntlet:{sector_key}] {msg}")
        logs.append(msg)
        _set_run_state(last_log=msg)

    if not GAUNTLET_ADDR:
        return {"result": "error", "stages_won": 0, "pearl": 0,
                "hollow": hollow_used, "sector": sector_key}

    _set_run_state(running=True, sector=sector_key, hollow=hollow_used,
                   stage=0, started=time.time(), last_log="Starting...")

    async def _run():
        nonlocal result, stages_won, pearl, hollow_used

        async with async_playwright() as pw:
            ctx_kwargs = {"viewport": {"width": 1280, "height": 800}}
            saved = _state_get("storage_state")
            if saved:
                try:
                    ctx_kwargs["storage_state"] = json.loads(saved)
                    log("Restored auth state from DB")
                except Exception:
                    pass

            log("Launching browser...")
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
                    # Removed --js-flags memory cap — it caused hard V8 OOM crashes
                    # because wagmi+rainbowkit alone need >256 MB of compiled heap.
                ],
            )
            log("Browser launched")
            context = await browser.new_context(**ctx_kwargs)

            await context.route(
                re.compile(
                    r"\.(png|jpg|jpeg|gif|webp|mp4|webm|ogg|mp3|wav|woff2?|ttf|otf)(\?.*)?$",
                    re.I,
                ),
                lambda route: route.abort(),
            )
            # Block RainbowKit UI chunk — 280 KB / ~80 MB compiled heap, causes crash.
            # Auth is handled instead by pre-seeding wagmi's localStorage state.
            await context.route(
                re.compile(r"/chunks/0v449np~zp91v\.js", re.I),
                lambda route: route.fulfill(
                    status=200, body="/* rainbowkit blocked */",
                    content_type="application/javascript"
                ),
            )

            # Inject window.ethereum provider + wagmi state seed before any JS runs
            await context.add_init_script(_provider_js(GAUNTLET_ADDR))
            await context.add_init_script(_wagmi_seed_js(GAUNTLET_ADDR))
            await context.add_init_script("""
                const s = document.createElement('style');
                s.textContent = `*, *::before, *::after {
                    animation-duration: 0.001s !important;
                    transition-duration: 0.001s !important;
                }`;
                document.addEventListener('DOMContentLoaded', () => document.head.appendChild(s));
            """)

            async def _setup_page(p):
                try:
                    await p.expose_function("__pwSignPersonal", _sign_personal)
                    await p.expose_function("__pwSignTyped",    _sign_typed)
                except Exception:
                    pass
            context.on("page", lambda p: asyncio.ensure_future(_setup_page(p)))

            page = await context.new_page()
            await _setup_page(page)

            intercepted = []
            page.on("request", lambda req: intercepted.append(req.url)
                    if "/api/" in req.url else None)

            # Crash detection: raise immediately instead of hanging on next eval
            _crashed = asyncio.Event()
            page.on("crash", lambda: _crashed.set())

            async def _safe_eval(js: str, t: float = 5.0) -> str:
                eval_task  = asyncio.ensure_future(page.evaluate(js))
                crash_task = asyncio.ensure_future(_crashed.wait())
                done, pending = await asyncio.wait(
                    [eval_task, crash_task],
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=t,
                )
                for task in pending:
                    task.cancel()
                if _crashed.is_set():
                    raise RuntimeError("Page crashed")
                if eval_task in done:
                    return eval_task.result()
                raise RuntimeError(f"eval timed out after {t}s")

            try:
                # ── auth phase ──────────────────────────────────────────────
                # Go straight to dashboard — our wagmi seed + window.ethereum
                # should make the app treat us as connected.
                log("Navigating to demo dashboard...")
                await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                log(f"URL after goto: {page.url}")
                log("Sleeping 8s for React hydration...")
                await asyncio.sleep(8)

                if _crashed.is_set():
                    raise RuntimeError("Page crashed during hydration")

                content = await _body_text(page)
                log(f"Dashboard text: {content[:600].strip()!r}")
                log(f"Current URL: {page.url}")
                log(f"Authed: {await _is_authed(page)}")

                # Get full HTML to see what's on page even if innerText is empty
                try:
                    html_snippet = await asyncio.wait_for(
                        page.evaluate("document.body ? document.body.innerHTML.slice(0,800) : ''"),
                        timeout=5.0,
                    )
                    log(f"Body HTML: {html_snippet!r}")
                except Exception as e:
                    log(f"HTML err: {e}")

                # Read ALL localStorage keys to find demo state key
                try:
                    ls = await asyncio.wait_for(
                        page.evaluate("""
                            Object.entries(localStorage)
                                .map(([k,v]) => k + '=' + String(v).slice(0,150))
                                .join(' || ')
                        """),
                        timeout=5.0,
                    )
                    log(f"localStorage: {ls[:800]}")
                except Exception as e:
                    log(f"localStorage err: {e}")

                try:
                    ns = await context.storage_state()
                    _state_put("storage_state", json.dumps(ns))
                    log("State saved")
                except Exception:
                    pass

                # ── navigate to sector prep ─────────────────────────────────
                log(f"Navigating to {sector['name']}...")
                await page.goto(sector["url"], wait_until="domcontentloaded", timeout=30000)
                log(f"Prep URL: {page.url}")
                await asyncio.sleep(8)

                prep_content = await _body_text(page)
                log(f"Prep page: {prep_content[:600].strip()!r}")

                try:
                    for btn in await page.query_selector_all("button"):
                        if (await btn.inner_text()).strip().lower() == "x":
                            await btn.click()
                            await asyncio.sleep(1)
                            break
                except Exception:
                    pass

                # ── select hollow(s) — use query_selector_all (instant, no locator timeout)
                async def _select_hollow(name: str) -> bool:
                    try:
                        els = await page.query_selector_all("button, div, span, p, li, td")
                        for el in els:
                            try:
                                txt = await asyncio.wait_for(el.inner_text(), timeout=0.5)
                                if name.lower() in txt.lower():
                                    await el.click()
                                    await asyncio.sleep(1)
                                    return True
                            except Exception:
                                continue
                    except Exception as e:
                        log(f"select_hollow error: {e}")
                    return False

                for hollow in sector["hollows"]:
                    if await _select_hollow(hollow):
                        hollow_used = hollow
                        log(f"Selected {hollow}")
                        await asyncio.sleep(1)
                    else:
                        log(f"Could not select {hollow}")

                _set_run_state(hollow=hollow_used)

                # ── enter sector ───────────────────────────────────────────
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)

                if not await _click_kw(page, f"ENTER {sector['enter_kw']}"):
                    await _click_kw(page, "ENTER")

                log("Entered sector")
                await asyncio.sleep(5)

                # ── stage loop ─────────────────────────────────────────────
                for stage in range(1, sector["stages"] + 1):
                    _set_run_state(stage=stage)
                    log(f"Stage {stage}/{sector['stages']}...")

                    if "results" in page.url:
                        log("Already on results page")
                        result = "win"
                        break

                    await _click_kw(page, "BEGIN STAGE")
                    await asyncio.sleep(10)

                    if ("crawl" not in page.url and "battle" not in page.url
                            and "results" not in page.url):
                        log(f"Page left crawl: {page.url}")
                        await page.goto(DASHBOARD_URL)
                        break

                    content = await _body_text(page)

                    if "results" in page.url or "CRAWL COMPLETE" in content.upper():
                        log("Crawl complete!")
                        stages_won = stage
                        result     = "win"
                        await _click_kw(page, "VIEW RESULTS")
                        await asyncio.sleep(3)
                        break

                    elif "VICTORY" in content.upper():
                        stages_won = stage
                        log(f"Stage {stage} VICTORY")
                        await _click_kw(page, "CONTINUE")
                        await asyncio.sleep(3)

                        if "results" in page.url:
                            result = "win"
                            break

                        c2 = await _body_text(page)
                        if "CRAWL COMPLETE" in c2.upper():
                            result = "win"
                            await _click_kw(page, "VIEW RESULTS")
                            await asyncio.sleep(3)
                            break

                        if stage == sector["stages"]:
                            result = "win"
                        else:
                            await asyncio.sleep(2)
                            await page.evaluate("window.scrollTo(0,0)")
                            await asyncio.sleep(0.5)
                            await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                            await asyncio.sleep(0.5)
                            await _click_kw(page, "NEXT STAGE")
                            await asyncio.sleep(5)

                    elif "DEFEAT" in content.upper():
                        log(f"Stage {stage} DEFEAT")
                        result = "defeat"
                        break

                # ── extract PEARL ─────────────────────────────────────────
                await asyncio.sleep(3)
                pearl = await _extract_pearl(page)
                log(f"PEARL earned: {pearl}")

                await _click_kw(page, "RETURN TO DASHBOARD")
                await asyncio.sleep(3)

                try:
                    fs = await context.storage_state()
                    _state_put("storage_state", json.dumps(fs))
                except Exception:
                    pass

                api_calls = list(set(intercepted))
                if api_calls:
                    log(f"API calls: {', '.join(api_calls[:10])}")

            except Exception as e:
                log(f"Battle error: {e}")
                result = "error"

            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

    try:
        await asyncio.wait_for(_run(), timeout=90.0)
    except asyncio.TimeoutError:
        log("Battle timed out after 90s — browser likely hung")
        result = "error"
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
