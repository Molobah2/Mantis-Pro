"""
Persistent historical snapshots for tracked collections — floor price,
listed count, supply, owner count over time. Mirrors
opensea_automint/store.py's SQLite pattern exactly (module-level lock,
per-call connections, schema created once per process) so this survives
Railway redeploys the same way that DB already does, via the shared
DATA_DIR-mounted volume.

Without this, nft_insights only ever knows "right now" — scan.py's cache
is 20 minutes and then gone. OpenSea's API has no floor-price-history
endpoint (verified live — see nft_insights/sales_history.py's docstring
for the one thing it DOES expose retroactively), so floor/listing trends
can only be tracked going forward from whenever a collection is first
scanned. insights.py must never present a comparison built on missing or
too-stale history as if it were real — get_snapshot_near enforces a
caller-supplied staleness tolerance for exactly that reason.
"""
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass

from . import config

_DB = os.path.join(config.DATA_DIR, "nft_insights_snapshots.db")
_lock = threading.Lock()
_initialized_dbs: set[str] = set()


def _init_schema(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS tracked_collections (
        slug              TEXT PRIMARY KEY,
        first_tracked_at  REAL,
        last_scanned_at   REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS collection_snapshots (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        slug               TEXT,
        captured_at        REAL,
        floor_price        REAL,
        floor_price_symbol TEXT,
        listed_count       INTEGER,
        supply             INTEGER,
        num_owners         INTEGER
    )""")
    c.execute("""CREATE INDEX IF NOT EXISTS idx_snapshots_slug_captured_at
        ON collection_snapshots(slug, captured_at)""")
    c.commit()


def _conn() -> sqlite3.Connection:
    """Must be called while holding `_lock`."""
    c = sqlite3.connect(_DB, check_same_thread=False)
    if _DB not in _initialized_dbs:
        try:
            _init_schema(c)
        except Exception:
            c.close()
            raise
        _initialized_dbs.add(_DB)
    return c


@dataclass(frozen=True)
class SnapshotInput:
    slug: str
    floor_price: float | None
    floor_price_symbol: str | None
    listed_count: int | None
    supply: int | None
    num_owners: int | None


def track_collection(slug: str) -> None:
    """Marks a collection as tracked (first time) and bumps its
    last_scanned_at (every time) — called whenever anyone scans it, so the
    background snapshot job (agent.py) only ever polls collections someone
    actually asked about."""
    now = time.time()
    with _lock, closing(_conn()) as c:
        c.execute("""
            INSERT INTO tracked_collections (slug, first_tracked_at, last_scanned_at)
            VALUES (?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET last_scanned_at=excluded.last_scanned_at
        """, (slug, now, now))
        c.commit()


def get_tracked_collections() -> list[str]:
    with _lock, closing(_conn()) as c:
        rows = c.execute("SELECT slug FROM tracked_collections ORDER BY slug").fetchall()
    return [r[0] for r in rows]


def get_stalest_tracked_collections(limit: int) -> list[str]:
    """The `limit` tracked slugs whose most recent snapshot is oldest
    (never-snapshotted slugs first) — used by the background snapshot job
    (scan.py's snapshot_tracked_collections) to bound how much work one run
    does regardless of how large tracked_collections has grown, while still
    rotating fairly through the whole set over successive runs rather than
    always favoring the same alphabetically-first slugs."""
    with _lock, closing(_conn()) as c:
        rows = c.execute("""
            SELECT tc.slug
            FROM tracked_collections tc
            LEFT JOIN (
                SELECT slug, MAX(captured_at) AS last_captured
                FROM collection_snapshots
                GROUP BY slug
            ) latest ON latest.slug = tc.slug
            ORDER BY COALESCE(latest.last_captured, 0) ASC
            LIMIT ?
        """, (limit,)).fetchall()
    return [r[0] for r in rows]


def days_tracked(slug: str) -> float:
    """How long this slug has had *any* history — 0.0 if never tracked.
    Used as the data-confidence figure (see insights.py/routes.py) so a
    just-discovered collection never implies deeper history than it has."""
    with _lock, closing(_conn()) as c:
        row = c.execute(
            "SELECT first_tracked_at FROM tracked_collections WHERE slug=?", (slug,)
        ).fetchone()
    if not row:
        return 0.0
    return max(0.0, (time.time() - row[0]) / 86400)


def record_snapshot(snapshot: SnapshotInput) -> int:
    now = time.time()
    with _lock, closing(_conn()) as c:
        cur = c.execute("""
            INSERT INTO collection_snapshots (
                slug, captured_at, floor_price, floor_price_symbol,
                listed_count, supply, num_owners
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.slug, now, snapshot.floor_price, snapshot.floor_price_symbol,
            snapshot.listed_count, snapshot.supply, snapshot.num_owners,
        ))
        c.commit()
        return cur.lastrowid


def get_snapshot_near(slug: str, target_epoch: float, max_distance_s: float) -> dict | None:
    """The snapshot whose captured_at is closest to target_epoch, or None if
    no snapshot exists within max_distance_s of it. The tolerance is the
    caller's decision (e.g. insights.py asking for "~7 days ago" with a
    tolerance sized to the snapshot cadence) — this function never silently
    substitutes a much staler point and lets the caller treat it as fresh."""
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT slug, captured_at, floor_price, floor_price_symbol,
                   listed_count, supply, num_owners
            FROM collection_snapshots
            WHERE slug=?
            ORDER BY ABS(captured_at - ?)
            LIMIT 1
        """, (slug, target_epoch)).fetchone()
    if not row:
        return None
    if abs(row[1] - target_epoch) > max_distance_s:
        return None
    return {
        "slug": row[0],
        "captured_at": row[1],
        "floor_price": row[2],
        "floor_price_symbol": row[3],
        "listed_count": row[4],
        "supply": row[5],
        "num_owners": row[6],
    }
