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
    Play one full gauntlet run. Mirrors working_battle.py flow exactly.
    Returns dict: {result, stages_won, pearl, hollow, sector}.
    """
    from playwright.async_api import async_playwright

    sector      = SECTORS.get(sector_key, SECTORS["surge"])
    hollow_used = ""
    logs        = []
    result      = "error"
    stages_won  = 0
    pearl       = 0

    def log(msg: str):
        print(f"  [gauntlet] {msg}")
        logs.append(msg)
        _set_run_state(last_log=msg)

    if not GAUNTLET_ADDR:
        return {"result": "error", "stages_won": 0, "pearl": 0,
                "hollow": "", "sector": sector_key}

    _set_run_state(running=True, sector=sector_key, hollow="",
                   stage=0, started=time.time(), last_log="Starting...")

    async def _run():
        nonlocal result, stages_won, pearl, hollow_used

        async with async_playwright() as pw:
            log("Launching browser...")
            browser = await pw.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-background-networking",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800}
            )

            # Inject wallet provider so the demo sees us as connected
            await context.add_init_script(_provider_js(GAUNTLET_ADDR))
            await context.add_init_script(_wagmi_seed_js(GAUNTLET_ADDR))

            async def _setup_page(p):
                try:
                    await p.expose_function("__pwSignPersonal", _sign_personal)
                    await p.expose_function("__pwSignTyped",    _sign_typed)
                except Exception:
                    pass
            context.on("page", lambda p: asyncio.ensure_future(_setup_page(p)))

            page = await context.new_page()
            await _setup_page(page)

            # ── click_btn mirrors working_battle.py exactly ────────────────
            async def click_btn(keyword):
                try:
                    buttons = await page.query_selector_all("button, a")
                    for btn in buttons:
                        text = await btn.inner_text()
                        if keyword.upper() in text.upper():
                            await btn.click()
                            await asyncio.sleep(2)
                            return True
                except Exception:
                    pass
                return False

            try:
                # ── landing / first-run onboarding ────────────────────────
                log("Navigating to demo landing...")
                await page.goto(DEMO_BASE, wait_until="domcontentloaded", timeout=30000)
                log("Waiting 15s for wallet connect...")
                await asyncio.sleep(15)

                content = await page.inner_text("body")
                log(f"Landing: {content[:300].strip()!r}")

                if "ENTER THE GAUNTLET" in content.upper():
                    log("First-run: clicking ENTER THE GAUNTLET")
                    await asyncio.sleep(5)
                    await click_btn("ENTER THE GAUNTLET")
                    await asyncio.sleep(5)

                # ── run one battle (working_battle.py: run_one_battle) ─────
                log(f"\nNavigating to {sector['name']}...")
                await page.goto(sector["url"], wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

                # Close tutorial if present
                try:
                    buttons = await page.query_selector_all("button")
                    for btn in buttons:
                        text = await btn.inner_text()
                        if text.strip().lower() in ("x", "×", "✕"):
                            await btn.click()
                            await asyncio.sleep(1)
                            break
                except Exception:
                    pass

                # Read prep page and select first available hollow
                prep_text = await page.inner_text("body")
                log(f"Prep page:\n{prep_text[:800]}")

                # Use get_by_text like working_battle.py for hollow selection
                try:
                    await page.get_by_text("Monolith", exact=False).first.click(timeout=3000)
                    hollow_used = "Monolith"
                    log("Monolith selected!")
                    await asyncio.sleep(2)
                except Exception:
                    pass

                if not hollow_used:
                    # Try every hollow name visible in SELECT TEAM section
                    _SKIP = {"LEVEL", "HP", "TYPE", "FW", "ATK", "DEF", "SPD",
                             "SELECT", "TEAM", "SLOTS", "NONE", "DEFAULT", "AI",
                             "INVENTORY", "EMPTY", "LOADOUT", "ITEMS",
                             "ENTRY", "FEE", "ETH", "RESETS", "SIZE", "SURGE",
                             "RIGID", "VOID", "DRIFT"}
                    section = prep_text
                    if "SELECT TEAM" in prep_text:
                        s = prep_text.find("SELECT TEAM")
                        e = prep_text.find("LOADOUT", s)
                        section = prep_text[s:e] if e > s else prep_text[s:s+600]
                    for line in section.split("\n"):
                        w = line.strip()
                        if (3 <= len(w) <= 20 and not any(c.isdigit() for c in w)
                                and w.upper() not in _SKIP and w.replace(" ","").isalpha()):
                            try:
                                await page.get_by_text(w, exact=True).first.click(timeout=3000)
                                hollow_used = w
                                log(f"Selected hollow: {w!r}")
                                await asyncio.sleep(2)
                                break
                            except Exception:
                                continue

                _set_run_state(hollow=hollow_used or "unknown")
                log(f"Hollow used: {hollow_used!r}")

                # Scroll and click Enter
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)
                try:
                    buttons = await page.query_selector_all("button")
                    for btn in buttons:
                        text = await btn.inner_text()
                        if "ENTER" in text.upper() and "SECTOR" in text.upper():
                            await btn.click()
                            log("Entered sector!")
                            await asyncio.sleep(5)
                            break
                except Exception:
                    log("Could not click Enter via button — trying get_by_text")
                    try:
                        await page.get_by_text("ENTER", exact=False).first.click(timeout=3000)
                        await asyncio.sleep(5)
                    except Exception as ex:
                        log(f"Enter failed: {ex}")

                # ── 3-stage battle loop (working_battle.py) ───────────────
                for stage in range(1, sector["stages"] + 1):
                    log(f"  Stage {stage}...")
                    _set_run_state(stage=stage)

                    await click_btn("BEGIN STAGE")
                    log("  Battle running...")
                    await asyncio.sleep(10)

                    content = await page.inner_text("body")
                    log(f"  After battle: {content[100:500].strip()!r}")

                    if "CRAWL COMPLETE" in content.upper():
                        log("  CRAWL COMPLETE!")
                        stages_won = sector["stages"]
                        result = "win"
                        await asyncio.sleep(2)
                        await click_btn("VIEW RESULTS")
                        await asyncio.sleep(3)
                        break

                    elif "VICTORY" in content.upper():
                        stages_won += 1
                        log(f"  Stage {stage} VICTORY!")
                        await click_btn("CONTINUE")
                        await asyncio.sleep(3)

                        content = await page.inner_text("body")
                        if "CRAWL COMPLETE" in content.upper():
                            log("  CRAWL COMPLETE after CONTINUE!")
                            stages_won = sector["stages"]
                            result = "win"
                            await asyncio.sleep(2)
                            await click_btn("VIEW RESULTS")
                            await asyncio.sleep(3)
                            break

                        if stage < sector["stages"]:
                            await asyncio.sleep(3)
                            await page.evaluate("window.scrollTo(0, 0)")
                            await asyncio.sleep(1)
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(1)
                            await click_btn("NEXT STAGE")
                            await asyncio.sleep(5)
                        else:
                            stages_won = sector["stages"]
                            result = "win"
                            content = await page.inner_text("body")
                            if "CRAWL COMPLETE" in content.upper():
                                await click_btn("VIEW RESULTS")
                            else:
                                await click_btn("RESULT")
                            await asyncio.sleep(3)
                            break

                    elif "DEFEAT" in content.upper():
                        log(f"  Stage {stage} DEFEAT!")
                        result = "defeat"
                        break

                # ── extract PEARL ──────────────────────────────────────────
                await asyncio.sleep(4)
                content = await page.inner_text("body")
                log(f"Results page:\n{content[:600]}")
                pearl = await _extract_pearl(page)
                log(f"PEARL earned: {pearl}")

                # Return to dashboard
                if not await click_btn("RETURN TO DASHBOARD"):
                    log("Going to dashboard manually...")
                    await page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

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
