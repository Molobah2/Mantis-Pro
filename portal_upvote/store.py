import sqlite3
import os
import time
import threading

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.dirname(__file__)))
_DB       = os.path.join(_DATA_DIR, "portal_upvote.db")
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS portal_apps (
        id           INTEGER PRIMARY KEY,
        name         TEXT,
        url          TEXT,
        discovered_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS upvote_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id       INTEGER,
        upvoted_at   REAL,
        tx_hash      TEXT,
        status       TEXT,
        user_address TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        address        TEXT PRIMARY KEY,
        encrypted_key  TEXT NOT NULL,
        session_config TEXT,
        expires_at     REAL,
        registered_at  REAL
    )""")
    # Migrate: add user_address column to existing upvote_log tables that predate multi-user
    try:
        c.execute("ALTER TABLE upvote_log ADD COLUMN user_address TEXT")
        c.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    c.commit()
    return c


# ── Apps ──────────────────────────────────────────────────────────────

def upsert_apps(apps):
    """apps: list of {id, name, url}"""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _lock:
        c = _conn()
        for a in apps:
            c.execute("""INSERT INTO portal_apps(id, name, url, discovered_at) VALUES(?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url""",
                      (a["id"], a.get("name", f"App #{a['id']}"), a.get("url", ""), now))
        c.commit()
        c.close()


def get_apps():
    with _lock:
        c = _conn()
        rows = c.execute(
            "SELECT id, name, url, discovered_at FROM portal_apps ORDER BY id"
        ).fetchall()
        c.close()
    return [{"id": r[0], "name": r[1], "url": r[2], "discovered_at": r[3]} for r in rows]


# ── Users ─────────────────────────────────────────────────────────────

def upsert_user(address, encrypted_key, session_config, expires_at):
    """Store or refresh a user's AGW session. address is the AGW wallet address."""
    now = time.time()
    addr = address.lower()
    with _lock:
        c = _conn()
        c.execute("""
            INSERT INTO users (address, encrypted_key, session_config, expires_at, registered_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                encrypted_key=excluded.encrypted_key,
                session_config=excluded.session_config,
                expires_at=excluded.expires_at
        """, (addr, encrypted_key, session_config, expires_at, now))
        c.commit()
        c.close()


def get_user(address):
    addr = address.lower()
    with _lock:
        c = _conn()
        row = c.execute(
            "SELECT address, encrypted_key, session_config, expires_at, registered_at FROM users WHERE address=?",
            (addr,)
        ).fetchone()
        c.close()
    if not row:
        return None
    return {"address": row[0], "encrypted_key": row[1], "session_config": row[2],
            "expires_at": row[3], "registered_at": row[4]}


def get_active_users():
    """All users whose session has not expired."""
    now = time.time()
    with _lock:
        c = _conn()
        rows = c.execute(
            "SELECT address, encrypted_key, session_config, expires_at, registered_at FROM users WHERE expires_at > ?",
            (now,)
        ).fetchall()
        c.close()
    return [{"address": r[0], "encrypted_key": r[1], "session_config": r[2],
             "expires_at": r[3], "registered_at": r[4]} for r in rows]


def get_user_count():
    now = time.time()
    with _lock:
        c = _conn()
        row = c.execute("SELECT COUNT(*) FROM users WHERE expires_at > ?", (now,)).fetchone()
        c.close()
    return row[0] if row else 0


def get_leaderboard(limit=20):
    """Return top voters sorted by total successful votes."""
    with _lock:
        c = _conn()
        rows = c.execute("""
            SELECT
                u.address,
                COUNT(ul.id)        AS vote_count,
                MAX(ul.upvoted_at)  AS last_voted,
                u.expires_at
            FROM users u
            LEFT JOIN upvote_log ul
                ON LOWER(ul.user_address) = u.address AND ul.status = 'success'
            GROUP BY u.address
            ORDER BY vote_count DESC, last_voted DESC
            LIMIT ?
        """, (limit,)).fetchall()
        c.close()
    return [{"address": r[0], "vote_count": r[1] or 0,
             "last_voted": r[2], "expires_at": r[3]} for r in rows]


# ── Upvote log ────────────────────────────────────────────────────────

def record_upvote(app_id, tx_hash, status="success", user_address=None):
    addr = user_address.lower() if user_address else None
    with _lock:
        c = _conn()
        c.execute(
            "INSERT INTO upvote_log(app_id, upvoted_at, tx_hash, status, user_address) VALUES(?,?,?,?,?)",
            (app_id, time.time(), tx_hash, status, addr)
        )
        c.commit()
        c.close()


def get_upvote_log(limit=50):
    with _lock:
        c = _conn()
        rows = c.execute("""
            SELECT ul.id, ul.app_id, pa.name, ul.upvoted_at, ul.tx_hash, ul.status, ul.user_address
            FROM upvote_log ul
            LEFT JOIN portal_apps pa ON pa.id = ul.app_id
            ORDER BY ul.upvoted_at DESC LIMIT ?
        """, (limit,)).fetchall()
        c.close()
    return [{"id": r[0], "app_id": r[1], "app_name": r[2],
             "upvoted_at": r[3], "tx_hash": r[4], "status": r[5], "user_address": r[6]} for r in rows]


def get_last_upvoted_per_app():
    """Returns {app_id: last_upvoted_ts} across ALL users — used for owner-only voting."""
    with _lock:
        c = _conn()
        rows = c.execute("""SELECT app_id, MAX(upvoted_at) FROM upvote_log
                            WHERE status='success' AND (user_address IS NULL OR user_address='')
                            GROUP BY app_id""").fetchall()
        c.close()
    return {r[0]: r[1] for r in rows}


def get_last_upvoted_per_app_for_user(address):
    """Returns {app_id: last_upvoted_ts} for a specific user."""
    addr = address.lower()
    with _lock:
        c = _conn()
        rows = c.execute("""SELECT app_id, MAX(upvoted_at) FROM upvote_log
                            WHERE status='success' AND LOWER(user_address)=?
                            GROUP BY app_id""", (addr,)).fetchall()
        c.close()
    return {r[0]: r[1] for r in rows}


def get_upvote_log_since(since_ts, limit=500, user_address=None):
    """Returns upvote log rows since a given Unix timestamp, optionally filtered by user."""
    with _lock:
        c = _conn()
        if user_address:
            addr = user_address.lower()
            rows = c.execute("""
                SELECT ul.id, ul.app_id, pa.name, ul.upvoted_at, ul.tx_hash, ul.status, ul.user_address
                FROM upvote_log ul
                LEFT JOIN portal_apps pa ON pa.id = ul.app_id
                WHERE ul.upvoted_at >= ? AND LOWER(ul.user_address) = ?
                ORDER BY ul.upvoted_at DESC LIMIT ?
            """, (since_ts, addr, limit)).fetchall()
        else:
            rows = c.execute("""
                SELECT ul.id, ul.app_id, pa.name, ul.upvoted_at, ul.tx_hash, ul.status, ul.user_address
                FROM upvote_log ul
                LEFT JOIN portal_apps pa ON pa.id = ul.app_id
                WHERE ul.upvoted_at >= ?
                ORDER BY ul.upvoted_at DESC LIMIT ?
            """, (since_ts, limit)).fetchall()
        c.close()
    return [{"id": r[0], "app_id": r[1], "app_name": r[2],
             "upvoted_at": r[3], "tx_hash": r[4], "status": r[5], "user_address": r[6]} for r in rows]


def get_stats(user_address=None):
    """Returns vote counts for today, 7d, 30d, and all time. Optionally scoped to one user."""
    now = time.time()
    midnight_utc = now - (now % 86400)
    with _lock:
        c = _conn()
        if user_address:
            addr = user_address.lower()
            def count_since(ts):
                return c.execute(
                    "SELECT COUNT(*) FROM upvote_log WHERE status='success' AND upvoted_at>=? AND LOWER(user_address)=?",
                    (ts, addr)
                ).fetchone()[0]
            stats = {
                "today":    count_since(midnight_utc),
                "week":     count_since(now - 7  * 86400),
                "month":    count_since(now - 30 * 86400),
                "all_time": c.execute("SELECT COUNT(*) FROM upvote_log WHERE status='success' AND LOWER(user_address)=?", (addr,)).fetchone()[0],
                "failed":   c.execute("SELECT COUNT(*) FROM upvote_log WHERE status!='success' AND LOWER(user_address)=?", (addr,)).fetchone()[0],
            }
        else:
            def count_since(ts):
                return c.execute(
                    "SELECT COUNT(*) FROM upvote_log WHERE status='success' AND upvoted_at >= ?", (ts,)
                ).fetchone()[0]
            stats = {
                "today":    count_since(midnight_utc),
                "week":     count_since(now - 7  * 86400),
                "month":    count_since(now - 30 * 86400),
                "all_time": c.execute("SELECT COUNT(*) FROM upvote_log WHERE status='success'").fetchone()[0],
                "failed":   c.execute("SELECT COUNT(*) FROM upvote_log WHERE status!='success'").fetchone()[0],
            }
        c.close()
    return stats
