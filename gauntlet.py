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
HEADLESS      = os.environ.get("HEADLESS", "true").lower() != "false"
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
    Pre-seed wagmi v2 localStorage state AND the litany demo game state
    (with a valid hollow) so the app sees us as connected with a ready team.
    """
    addr = address.lower()
    return f"""
(function() {{
    const ADDR     = '{addr}';
    const CHAIN_ID = 2741;
    const UID      = 'injected';
    const NOW      = Date.now();

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

    // Mark all tutorials complete so no overlay appears on prep page.
    // Do NOT inject fake hollow data — the game reads the wallet's real hollows.
    const existingRaw = localStorage.getItem('litany_demo_' + ADDR);
    if (existingRaw) {{
        try {{
            const existing = JSON.parse(existingRaw);
            if (existing.tutorial) {{
                existing.tutorial.completedFirstCrawl  = true;
                existing.tutorial.completedFirstSynthesis = true;
            }}
            localStorage.setItem('litany_demo_' + ADDR, JSON.stringify(existing));
        }} catch(e) {{}}
    }}

    // Fire connection events so wagmi re-reads our injected provider
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
                await asyncio.sleep(1)
                return True
    except Exception as e:
        print(f"  [gauntlet] click_kw '{keyword}': {e}")
    return False

async def _extract_pearl(page) -> int:
    """Read PEARL count from CRAWL REPORT screen.

    CRAWL REPORT layout:
        NET GAINS
        +0.002 ETH
        +741        ← PEARL (standalone integer between ETH line and XP line)
        +50 XP
    """
    try:
        text = await _body_text(page)
        # Primary: find the +N sandwiched between ETH and XP in NET GAINS
        m = re.search(
            r'NET GAINS.*?\+[\d.]+\s*ETH\s*\+(\d+)',
            text, re.I | re.DOTALL
        )
        if m:
            return int(m.group(1))
        # Secondary: ×N from per-stage demo_pearl display (e.g. "demo_pearl ×246")
        m = re.search(r'×(\d+)', text)
        if m:
            return int(m.group(1))
        # Fallback legacy patterns
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
                headless=HEADLESS,
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
            # Replace the 280KB RainbowKit chunk with a proper Turbopack-format stub.
            # Correct format: .push([scriptRef, id1,id2,...idN, factory])
            # factory calls e.s(["name",0,value], moduleId) for each module.
            # All 24 module IDs from the original chunk are registered here.
            # 110163 (viem http) is NOT in the original chunk — left unblocked.
            _rk_stub = (
                "(function(){"
                "var _R=globalThis.React;"
                "function _pt(p){return _R?_R.createElement(_R.Fragment,null,p&&p.children):p&&p.children||null;}"
                "function _noop(){}"
                "function _hook(){return{data:undefined,isLoading:false,error:null};}"
                f"var _A='{GAUNTLET_ADDR}',_CID=2741,_UID='injected';"
                "function _cfg(opts){"
                "var _ssr=!!(opts&&opts.ssr);"
                "var _map=new Map([[_UID,{accounts:[_A],chainId:_CID,connector:{id:_UID,name:'MetaMask',type:'injected',uid:_UID}}]]);"
                "var _st={connections:_map,chainId:_CID,current:_UID,status:'connected'};"
                "return{chains:(opts&&opts.chains)||[],connectors:[],"
                "storage:{getItem:function(){return null;},setItem:_noop,removeItem:_noop},"
                "ssr:_ssr,"
                "_internal:{ssr:_ssr,"
                "connectors:{setup:function(){return{id:_UID,type:'injected',uid:_UID,name:'MetaMask',emitter:{on:_noop,off:_noop,emit:_noop}};},"
                "getState:function(){return[];},setState:_noop,subscribe:function(){return _noop;}},"
                "events:{conn:{onConnect:_noop,onDisconnect:_noop},change:_noop,disconnect:_noop},"
                "chains:{getState:function(){return _st;},subscribe:function(){return _noop;}}},"
                "get state(){return _st;},"
                "getState:function(){return _st;},"
                "setState:function(fn){if(typeof fn==='function')_st=fn(_st);},"
                "subscribe:function(l){return _noop;},"
                "getClient:function(){return{request:async function(){return null;},chain:{id:_CID}};},"
                "reconnect:_noop};}"
                "(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["
                "typeof document!=='undefined'?document.currentScript:void 0,"
                "103111,289600,485738,196943,885157,151794,356327,"
                "160518,557073,373403,712369,700510,249994,985369,"
                "174960,979454,377349,604140,689862,794702,200057,"
                "627152,518714,722652,"
                "function(e){"
                "e.s(['darkTheme',0,function(){return{};}],103111);"
                "e.s(['lightTheme',0,function(){return{};}],289600);"
                "e.s(['midnightTheme',0,function(){return{};}],485738);"
                "e.s(['createMapValueFn',0,function(){return function(){return null;};}],196943);"
                "e.s(['createSprinkles',0,function(){return function(){return{};};}],885157);"
                "e.s(['useAccountEffect',0,_noop],151794);"
                "e.s(['default',0,_noop],356327);"
                "e.s(['useBalance',0,_hook],160518);"
                "e.s(['normalize',0,function(v){return v;}],557073);"
                "e.s(['useEnsAvatar',0,_hook],373403);"
                "e.s(['useEnsName',0,_hook],712369);"
                "e.s(['usePublicClient',0,function(){return undefined;}],700510);"
                "e.s(['useDisconnect',0,function(){return{disconnect:_noop};}],249994);"
                "e.s(['RemoveScroll',0,_pt],985369);"
                "e.s(['assignInlineVars',0,function(){return{};}],174960);"
                "e.s(['useConnect',0,function(){return{connect:_noop,connectors:[],status:'disconnected'};}],979454);"
                "e.s(['ProviderNotFoundError',0,function ProvNotFound(){},'SwitchChainNotSupportedError',0,function SwitchNotSupported(){}],377349);"
                "e.s(['useSwitchChain',0,function(){return{switchChain:_noop};}],604140);"
                "e.s(['createConnector',0,function(fn){return fn;}],689862);"
                "e.s(['injected',0,function(){return{id:'injected',type:'injected'};}],794702);"
                "e.s(['createConfig',0,_cfg],200057);"
                "e.s(['walletConnect',0,function(){return{id:'walletConnect'};}],627152);"
                "e.s(['metaMask',0,function(){return{id:'metaMask'};}],518714);"
                "e.s(['RainbowKitProvider',0,_pt],722652);"
                "}]);})();"
            )
            await context.route(
                re.compile(r"/chunks/0v449np~zp91v\.js", re.I),
                lambda route: route.fulfill(
                    status=200, body=_rk_stub,
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

            # Capture JS console errors for diagnostics
            js_errors = []
            page.on("console", lambda msg: js_errors.append(f"[{msg.type}] {msg.text[:200]}")
                    if msg.type in ("error", "warning") else None)
            page.on("pageerror", lambda err: js_errors.append(f"[pageerror] {str(err)[:200]}"))

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
                # Navigate to /demo first (SSR content renders immediately).
                # The wagmi seed + window.ethereum make the app see us as
                # connected. After hydration (~10s) it should redirect to
                # /demo/dashboard. We then wait there for full render (~20s).
                log("Navigating to demo landing...")
                await page.goto(DEMO_BASE, wait_until="domcontentloaded", timeout=30000)
                log("Waiting 8s for hydration + auto-redirect...")
                await asyncio.sleep(8)

                if _crashed.is_set():
                    raise RuntimeError("Page crashed during hydration")

                log(f"URL after landing wait: {page.url}")
                content = await _body_text(page)
                log(f"Landing text: {content[:300].strip()!r}")

                # If still on landing (not redirected), go to dashboard directly
                if "dashboard" not in page.url:
                    log("No auto-redirect — navigating to dashboard directly...")
                    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                    log("Waiting 12s for React hydration on dashboard...")
                    await asyncio.sleep(12)

                if _crashed.is_set():
                    raise RuntimeError("Page crashed on dashboard")

                content = await _body_text(page)
                log(f"Dashboard text: {content[:600].strip()!r}")
                log(f"URL: {page.url}")
                log(f"Authed: {await _is_authed(page)}")
                if js_errors:
                    log(f"JS errors (dashboard): {' | '.join(js_errors[-6:])}")

                # Capture all localStorage keys for diagnostics
                try:
                    ls = await asyncio.wait_for(
                        page.evaluate("""
                            Object.entries(localStorage)
                                .map(([k,v]) => k + '=' + String(v).slice(0,120))
                                .join(' || ')
                        """),
                        timeout=5.0,
                    )
                    log(f"localStorage: {ls[:600]}")
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
                # Wait for React to hydrate on prep page
                await asyncio.sleep(12)

                if _crashed.is_set():
                    raise RuntimeError("Page crashed on prep page")

                prep_content = await _body_text(page)
                log(f"Prep page: {prep_content[:800].strip()!r}")

                if js_errors:
                    log(f"JS errors: {' | '.join(js_errors[-8:])}")

                # Also log localStorage demo state to confirm our hollow seed was read
                try:
                    demo_ls = await asyncio.wait_for(
                        page.evaluate(f"localStorage.getItem('litany_demo_{GAUNTLET_ADDR}')"),
                        timeout=5.0,
                    )
                    if demo_ls:
                        import json as _j
                        ds = _j.loads(demo_ls)
                        log(f"Demo state: {len(ds.get('hollows',[]))} hollow(s), "
                            f"demoETH={ds.get('demoETH')}, ver={ds.get('version')}")
                    else:
                        log("Demo state: not found in localStorage")
                except Exception as e:
                    log(f"Demo state read err: {e}")

                # Dismiss any tutorial/modal overlay before hollow selection.
                # The close button text is "×" (U+00D7), not the letter "x".
                try:
                    for el in await page.query_selector_all("button, div, span"):
                        t = (await el.inner_text()).strip()
                        if t in ("×", "x", "X", "✕", "close", "Close", "CLOSE"):
                            await el.click()
                            await asyncio.sleep(1)
                            break
                except Exception:
                    pass

                # ── helpers (proven pattern from working_battle.py) ────────
                log(f"Prep page:\n{prep_content[:800].strip()}")

                async def _gbt(text: str, timeout_ms: int = 5000) -> bool:
                    """Click via page.get_by_text — proven to find any element by text."""
                    try:
                        await page.get_by_text(text, exact=False).first.click(
                            timeout=timeout_ms
                        )
                        log(f"gbt_click({text!r}) OK")
                        return True
                    except Exception as ex:
                        log(f"gbt_click({text!r}) failed: {ex}")
                        return False

                # ── select hollow ──────────────────────────────────────────
                # Parse actual hollow names from the prep page — never hardcode.
                # The SELECT TEAM section lists each hollow by name.
                _SKIP = {"LEVEL", "HP", "TYPE", "FW", "ATK", "DEF", "SPD",
                         "SELECT", "TEAM", "SLOTS", "NONE", "DEFAULT", "AI",
                         "INVENTORY", "EMPTY", "LOADOUT", "ITEMS", "STAGE",
                         "ENTRY", "FEE", "ETH", "RESETS", "SIZE"}
                available_hollows = []
                section = prep_content
                if "SELECT TEAM" in prep_content:
                    s = prep_content.find("SELECT TEAM")
                    e = prep_content.find("LOADOUT", s)
                    section = prep_content[s:e] if e > s else prep_content[s:s+600]
                for line in section.split("\n"):
                    w = line.strip()
                    # Hollow names: 3-20 chars, no digits, not a known header word
                    if (3 <= len(w) <= 20
                            and not any(c.isdigit() for c in w)
                            and w.upper() not in _SKIP
                            and w.replace(" ", "").isalpha()):
                        available_hollows.append(w)
                log(f"Hollows found on prep page: {available_hollows}")

                for hollow in available_hollows:
                    try:
                        await page.get_by_text(hollow, exact=True).first.click(timeout=3000)
                        hollow_used = hollow
                        log(f"Selected hollow: {hollow!r}")
                        await asyncio.sleep(2)
                        break
                    except Exception as ex:
                        log(f"Could not click {hollow!r}: {ex}")

                if not hollow_used:
                    body_now = await _body_text(page)
                    log(f"No hollow selected. Page: {body_now[:400]!r}")
                    raise RuntimeError("No hollow available to select on prep page")

                _set_run_state(hollow=hollow_used)

                # ── enter sector ───────────────────────────────────────────
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)

                entered = False
                for kw in [sector["name"], "ENTER SURGE SECTOR",
                            "ENTER SECTOR", "ENTER DRIFT", "ENTER"]:
                    if await _gbt(kw, timeout_ms=3000):
                        entered = True
                        break
                log(f"Enter clicked: {entered} — waiting for battle page...")

                # Wait up to 15s for URL to become '/battle'
                for _ in range(15):
                    await asyncio.sleep(1)
                    if "battle" in page.url:
                        log(f"On battle page: {page.url}")
                        break
                else:
                    body_now = await _body_text(page)
                    log(f"Not on battle after 15s. URL={page.url!r} body={body_now[:300]!r}")
                    raise RuntimeError("Never navigated to battle page after ENTER")

                # ── battle loop (mirrors working_battle.py) ─────────────────
                _WIN_KWS  = ("CRAWL COMPLETE", "STAGE CLEAR", "SECTOR COMPLETE",
                             "STAGE COMPLETE", "YOU WIN", "HOLLOWS WIN")
                _LOSE_KWS = ("DEFEAT", "GAME OVER", "YOU LOSE")

                for stage in range(1, sector["stages"] + 2):
                    log(f"Stage {stage}...")

                    await _gbt("BEGIN STAGE", timeout_ms=5000)
                    log("BEGIN STAGE clicked — waiting 10s for battle to resolve...")
                    await asyncio.sleep(10)

                    if _crashed.is_set():
                        log("Page crashed during battle")
                        result = "error"
                        break

                    txt = await _body_text(page)
                    u   = txt.upper()
                    log(f"  After battle: {txt[100:500].strip()!r}")
                    _set_run_state(stage=stage)

                    if any(k in u for k in _WIN_KWS):
                        log("WIN detected — clicking VIEW RESULTS")
                        stages_won = sector["stages"]
                        result = "win"
                        await asyncio.sleep(2)
                        await _gbt("VIEW RESULTS", timeout_ms=5000)
                        await asyncio.sleep(5)
                        break

                    if "VICTORY" in u:
                        stages_won += 1
                        log(f"VICTORY stage {stage} — stages_won={stages_won}")
                        await _gbt("CONTINUE", timeout_ms=3000)
                        await asyncio.sleep(3)
                        txt2 = await _body_text(page)
                        if any(k in txt2.upper() for k in _WIN_KWS):
                            result = "win"
                            stages_won = sector["stages"]
                            await _gbt("VIEW RESULTS", timeout_ms=5000)
                            await asyncio.sleep(5)
                            break
                        if stage < sector["stages"]:
                            await asyncio.sleep(3)
                            await page.evaluate("window.scrollTo(0,0)")
                            await asyncio.sleep(1)
                            await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
                            await asyncio.sleep(1)
                            await _gbt("NEXT STAGE", timeout_ms=3000)
                            await asyncio.sleep(5)
                        else:
                            result = "win"
                            stages_won = sector["stages"]
                            await _gbt("VIEW RESULTS", timeout_ms=5000)
                            await asyncio.sleep(5)
                            break
                        continue

                    if any(k in u for k in _LOSE_KWS):
                        log("DEFEAT")
                        result = "defeat"
                        break

                    # Still on prep after many ticks = entry failed / no hollow
                    if "prep" in url and tick >= 4:
                        log(f"Still on prep at tick {tick} — retrying enter")
                        await _click_kw(page, "ENTER")
                        await asyncio.sleep(2)

                # ── extract PEARL ─────────────────────────────────────────
                # Give CRAWL REPORT time to render before reading
                await asyncio.sleep(5)
                # Log full results page for diagnostics
                results_txt = await _body_text(page)
                log(f"Results page: {results_txt[:800].strip()!r}")
                pearl = await _extract_pearl(page)
                log(f"PEARL earned: {pearl}")

                if not await _gbt("RETURN TO DASHBOARD", timeout_ms=5000):
                    await page.goto(DASHBOARD_URL)
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
        await asyncio.wait_for(_run(), timeout=270.0)
    except asyncio.TimeoutError:
        log("Battle timed out after 270s — browser likely hung")
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


if __name__ == "__main__":
    import sys
    sector = sys.argv[1] if len(sys.argv) > 1 else "surge"
    print(f"Running gauntlet: sector={sector!r}  headless={HEADLESS}")
    result = asyncio.run(run_battle(sector))
    print(f"\nResult: {result}")
