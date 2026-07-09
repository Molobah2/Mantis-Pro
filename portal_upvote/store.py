import sqlite3
import os
import time
import threading

_DB   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portal_upvote.db")
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
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id      INTEGER,
        upvoted_at  REAL,
        tx_hash     TEXT,
        status      TEXT
    )""")
    c.commit()
    return c


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


def record_upvote(app_id, tx_hash, status="success"):
    with _lock:
        c = _conn()
        c.execute("INSERT INTO upvote_log(app_id, upvoted_at, tx_hash, status) VALUES(?,?,?,?)",
                  (app_id, time.time(), tx_hash, status))
        c.commit()
        c.close()


def get_upvote_log(limit=50):
    with _lock:
        c = _conn()
        rows = c.execute("""
            SELECT ul.id, ul.app_id, pa.name, ul.upvoted_at, ul.tx_hash, ul.status
            FROM upvote_log ul
            LEFT JOIN portal_apps pa ON pa.id = ul.app_id
            ORDER BY ul.upvoted_at DESC LIMIT ?
        """, (limit,)).fetchall()
        c.close()
    return [{"id": r[0], "app_id": r[1], "app_name": r[2],
             "upvoted_at": r[3], "tx_hash": r[4], "status": r[5]} for r in rows]


def get_last_upvoted_per_app():
    """Returns {app_id: last_upvoted_ts} for successfully upvoted apps."""
    with _lock:
        c = _conn()
        rows = c.execute("""SELECT app_id, MAX(upvoted_at) FROM upvote_log
                            WHERE status='success' GROUP BY app_id""").fetchall()
        c.close()
    return {r[0]: r[1] for r in rows}


def get_upvote_log_since(since_ts, limit=500):
    """Returns upvote log rows since a given Unix timestamp."""
    with _lock:
        c = _conn()
        rows = c.execute("""
            SELECT ul.id, ul.app_id, pa.name, ul.upvoted_at, ul.tx_hash, ul.status
            FROM upvote_log ul
            LEFT JOIN portal_apps pa ON pa.id = ul.app_id
            WHERE ul.upvoted_at >= ?
            ORDER BY ul.upvoted_at DESC LIMIT ?
        """, (since_ts, limit)).fetchall()
        c.close()
    return [{"id": r[0], "app_id": r[1], "app_name": r[2],
             "upvoted_at": r[3], "tx_hash": r[4], "status": r[5]} for r in rows]


def get_stats():
    """Returns vote counts for today, 7d, 30d, and all time."""
    now = time.time()
    midnight_utc = now - (now % 86400)
    with _lock:
        c = _conn()
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
