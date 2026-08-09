import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass

_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.dirname(__file__)))
_DB       = os.path.join(_DATA_DIR, "opensea_automint.db")
_lock = threading.Lock()

# Tracks which DB file paths have already had their schema created in this
# process, so _conn() only pays the CREATE TABLE / ALTER TABLE cost once per
# path instead of on every single call. Callers must hold `_lock` before
# calling _conn() (every public function below does).
_initialized_dbs: set[str] = set()


def _init_schema(c: sqlite3.Connection) -> None:
    # Note: an earlier design used ERC-4337 smart accounts (ZeroDev) for
    # firing, with a separate `smart_accounts` cache table for their
    # counterfactual addresses. Replaced with direct-signed-transaction
    # firing from a plain session EOA for speed — session_grants now stores
    # that key's own `session_address` directly, and no smart-account
    # derivation happens anywhere in this codebase anymore. The old table
    # is no longer created (a pre-existing one on a live DB is simply
    # unused, not actively dropped — nothing reads it either way).
    c.execute("""CREATE TABLE IF NOT EXISTS session_grants (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_address           TEXT,
        session_address         TEXT,
        encrypted_session_key   TEXT,
        permission_config       TEXT,
        allowed_targets         TEXT,
        value_cap_wei           TEXT,
        expires_at              REAL,
        revoked                 INTEGER DEFAULT 0,
        created_at               REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tracked_drops (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        collection_slug   TEXT UNIQUE,
        name              TEXT,
        contract_address  TEXT,
        chain             TEXT DEFAULT 'ethereum',
        mint_page_url     TEXT,
        discovered_at     REAL,
        source            TEXT,
        stage_data        TEXT,
        updated_at        REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS arm_requests (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_address      TEXT,
        drop_id            INTEGER,
        session_grant_id   INTEGER,
        quantity           INTEGER,
        max_price_wei      TEXT,
        go_live_at         REAL,
        status             TEXT,
        created_at         REAL,
        updated_at         REAL
    )""")
    # Enforces "at most one active arm request per (owner, drop)" at the DB
    # level — the real guard against the TOCTOU race in firing.arm_drop's
    # own check-then-insert (that check is only an optimization/early-exit
    # for a clear error message; this index is what actually prevents two
    # concurrent requests from both creating a row and each independently
    # firing a real, duplicate mint). A partial index so terminal rows
    # (succeeded/failed/cancelled/expired) never collide — re-arming after
    # any of those must still be allowed.
    c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_arm_requests_active_owner_drop
        ON arm_requests(owner_address, drop_id)
        WHERE status IN ('pending_schedule', 'scheduled', 'armed', 'fired')
    """)
    c.execute("""CREATE TABLE IF NOT EXISTS mint_attempts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        arm_request_id  INTEGER,
        attempted_at    REAL,
        tx_hash         TEXT,
        user_op_hash    TEXT,
        status          TEXT,
        error_message   TEXT,
        gas_used        TEXT,
        block_number    INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS eligibility_cache (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        drop_id       INTEGER,
        owner_address TEXT,
        is_eligible   INTEGER,
        merkle_proof  TEXT,
        phase_id      TEXT,
        checked_at    REAL,
        source        TEXT
    )""")
    # Migrate: reserved for future ALTER TABLE additions, wrapped defensively.
    try:
        c.execute("ALTER TABLE tracked_drops ADD COLUMN chain TEXT DEFAULT 'ethereum'")
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        # A DB created before the ERC-4337-to-direct-transaction rewrite
        # still has the old column name — rename it in place rather than
        # requiring a fresh DB (no real user funds/grants existed under the
        # old model yet, but this keeps the migration honest regardless).
        c.execute("ALTER TABLE session_grants RENAME COLUMN smart_account_address TO session_address")
    except sqlite3.OperationalError:
        pass  # already renamed, or table was created fresh with the new name
    c.commit()


def _conn() -> sqlite3.Connection:
    """Open a connection to the opensea_automint DB, initializing schema on first use.
    Must be called while holding `_lock`."""
    c = sqlite3.connect(_DB, check_same_thread=False)
    if _DB not in _initialized_dbs:
        try:
            _init_schema(c)
        except Exception:
            c.close()
            raise
        _initialized_dbs.add(_DB)
    return c


# ── DTOs for functions with many related fields (avoids same-typed
#    positional/keyword args being accidentally transposed at call sites) ──

@dataclass(frozen=True)
class SessionGrantInput:
    owner_address: str
    session_address: str
    encrypted_session_key: str
    permission_config: str
    allowed_targets: str
    value_cap_wei: str
    expires_at: float


@dataclass(frozen=True)
class TrackedDropInput:
    collection_slug: str
    name: str
    contract_address: str
    mint_page_url: str
    source: str
    stage_data: str


@dataclass(frozen=True)
class ArmRequestInput:
    owner_address: str
    drop_id: int
    session_grant_id: int
    quantity: int
    max_price_wei: str
    go_live_at: float | None


@dataclass(frozen=True)
class MintAttemptInput:
    arm_request_id: int
    tx_hash: str | None
    user_op_hash: str | None
    status: str
    error_message: str | None
    gas_used: str | None
    block_number: int | None


@dataclass(frozen=True)
class EligibilityInput:
    drop_id: int
    owner_address: str
    is_eligible: bool | None
    merkle_proof: str | None
    phase_id: str | None
    source: str


# ── Session grants ───────────────────────────────────────────────────

def insert_session_grant(grant: SessionGrantInput) -> int:
    """Store a new (already-encrypted) scoped session key grant. Returns the new row id."""
    owner = grant.owner_address.lower()
    now = time.time()
    with _lock, closing(_conn()) as c:
        cur = c.execute("""
            INSERT INTO session_grants (
                owner_address, session_address, encrypted_session_key,
                permission_config, allowed_targets, value_cap_wei, expires_at,
                revoked, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (owner, grant.session_address, grant.encrypted_session_key,
              grant.permission_config, grant.allowed_targets, grant.value_cap_wei,
              grant.expires_at, now))
        c.commit()
        return cur.lastrowid


def get_active_session_grant(owner_address: str) -> dict | None:
    """Most recent non-revoked, non-expired session grant for this owner, or None."""
    owner = owner_address.lower()
    now = time.time()
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, owner_address, session_address, encrypted_session_key,
                   permission_config, allowed_targets, value_cap_wei, expires_at,
                   revoked, created_at
            FROM session_grants
            WHERE owner_address=? AND revoked=0 AND expires_at > ?
            ORDER BY id DESC LIMIT 1
        """, (owner, now)).fetchone()
    if not row:
        return None
    return _session_grant_row_to_dict(row)


def get_session_grant(grant_id: int) -> dict | None:
    """A single session grant by id, regardless of revoked/expired status —
    the firing watcher needs to see (and reject) an expired/revoked grant
    explicitly, not have it silently disappear the way get_active_session_grant
    would."""
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, owner_address, session_address, encrypted_session_key,
                   permission_config, allowed_targets, value_cap_wei, expires_at,
                   revoked, created_at
            FROM session_grants WHERE id=?
        """, (grant_id,)).fetchone()
    if not row:
        return None
    return _session_grant_row_to_dict(row)


def revoke_session_grant(grant_id: int) -> None:
    """Mark a session grant as revoked so it's no longer returned as active."""
    with _lock, closing(_conn()) as c:
        c.execute("UPDATE session_grants SET revoked=1 WHERE id=?", (grant_id,))
        c.commit()


def _session_grant_row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0], "owner_address": row[1], "session_address": row[2],
        "encrypted_session_key": row[3], "permission_config": row[4],
        "allowed_targets": row[5], "value_cap_wei": row[6], "expires_at": row[7],
        "revoked": row[8], "created_at": row[9],
    }


# ── Tracked drops ─────────────────────────────────────────────────────

def upsert_tracked_drop(drop: TrackedDropInput) -> int:
    """Insert a newly-discovered drop or refresh an existing one, keyed by collection_slug.
    Returns the row id either way."""
    now = time.time()
    with _lock, closing(_conn()) as c:
        c.execute("""
            INSERT INTO tracked_drops (
                collection_slug, name, contract_address, mint_page_url,
                discovered_at, source, stage_data, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_slug) DO UPDATE SET
                name=excluded.name,
                contract_address=excluded.contract_address,
                mint_page_url=excluded.mint_page_url,
                source=excluded.source,
                stage_data=excluded.stage_data,
                updated_at=excluded.updated_at
        """, (drop.collection_slug, drop.name, drop.contract_address, drop.mint_page_url,
              now, drop.source, drop.stage_data, now))
        c.commit()
        row = c.execute(
            "SELECT id FROM tracked_drops WHERE collection_slug=?", (drop.collection_slug,)
        ).fetchone()
        return row[0]


def get_tracked_drops() -> list[dict]:
    """All currently tracked drops, oldest first."""
    with _lock, closing(_conn()) as c:
        rows = c.execute("""
            SELECT id, collection_slug, name, contract_address, chain, mint_page_url,
                   discovered_at, source, stage_data, updated_at
            FROM tracked_drops ORDER BY id
        """).fetchall()
    return [_tracked_drop_row_to_dict(r) for r in rows]


def get_tracked_drop_by_slug(collection_slug: str) -> dict | None:
    """A single tracked drop by its collection_slug — the identifier the
    frontend actually has (the internal integer id is deliberately never
    exposed to API callers, see drops.py's to_display_dict). None if it
    doesn't exist."""
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, collection_slug, name, contract_address, chain, mint_page_url,
                   discovered_at, source, stage_data, updated_at
            FROM tracked_drops WHERE collection_slug=?
        """, (collection_slug,)).fetchone()
    if not row:
        return None
    return _tracked_drop_row_to_dict(row)


def get_tracked_drop(drop_id: int) -> dict | None:
    """A single tracked drop by id, or None if it doesn't exist."""
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, collection_slug, name, contract_address, chain, mint_page_url,
                   discovered_at, source, stage_data, updated_at
            FROM tracked_drops WHERE id=?
        """, (drop_id,)).fetchone()
    if not row:
        return None
    return _tracked_drop_row_to_dict(row)


def _tracked_drop_row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0], "collection_slug": row[1], "name": row[2],
        "contract_address": row[3], "chain": row[4], "mint_page_url": row[5],
        "discovered_at": row[6], "source": row[7], "stage_data": row[8],
        "updated_at": row[9],
    }


# ── Arm requests ──────────────────────────────────────────────────────

def create_arm_request(arm: ArmRequestInput) -> int:
    """Record a new arm request in 'pending_schedule' status. Returns the new
    row id. Raises sqlite3.IntegrityError if this (owner, drop) pair already
    has a non-terminal arm request — enforced by idx_arm_requests_active_owner_drop,
    not just an application-level check (see that index's comment in
    _init_schema for why this must be a real DB constraint)."""
    owner = arm.owner_address.lower()
    now = time.time()
    with _lock, closing(_conn()) as c:
        cur = c.execute("""
            INSERT INTO arm_requests (
                owner_address, drop_id, session_grant_id, quantity, max_price_wei,
                go_live_at, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending_schedule', ?, ?)
        """, (owner, arm.drop_id, arm.session_grant_id, arm.quantity, arm.max_price_wei,
              arm.go_live_at, now, now))
        c.commit()
        return cur.lastrowid


def update_arm_request_status(arm_id: int, status: str) -> None:
    """Move an arm request to a new lifecycle status
    ('pending_schedule'|'scheduled'|'armed'|'fired'|'succeeded'|'failed'|'cancelled'|'expired')."""
    now = time.time()
    with _lock, closing(_conn()) as c:
        c.execute(
            "UPDATE arm_requests SET status=?, updated_at=? WHERE id=?",
            (status, now, arm_id)
        )
        c.commit()


def try_cancel_arm_request(arm_id: int) -> bool:
    """Atomically cancels an arm request, but only while it's still in a
    non-terminal, not-yet-fired state — an already-fired/succeeded/failed
    request can't be un-fired, so cancellation after that point would be
    misleading. Returns True only if this call performed the cancellation."""
    now = time.time()
    with _lock, closing(_conn()) as c:
        cur = c.execute("""
            UPDATE arm_requests SET status='cancelled', updated_at=?
            WHERE id=? AND status IN ('pending_schedule', 'scheduled', 'armed')
        """, (now, arm_id))
        c.commit()
        return cur.rowcount == 1


def get_arm_request(arm_id: int) -> dict | None:
    """A single arm request by id, or None if it doesn't exist."""
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, owner_address, drop_id, session_grant_id, quantity, max_price_wei,
                   go_live_at, status, created_at, updated_at
            FROM arm_requests WHERE id=?
        """, (arm_id,)).fetchone()
    if not row:
        return None
    return _arm_request_row_to_dict(row)


def get_pending_arm_requests() -> list[dict]:
    """Arm requests the firing watcher still needs to check on (status in
    'pending_schedule'/'scheduled'/'armed') — everything short of a
    terminal state (fired/succeeded/failed/cancelled/expired)."""
    with _lock, closing(_conn()) as c:
        rows = c.execute("""
            SELECT id, owner_address, drop_id, session_grant_id, quantity, max_price_wei,
                   go_live_at, status, created_at, updated_at
            FROM arm_requests WHERE status IN ('pending_schedule', 'scheduled', 'armed')
            ORDER BY id
        """).fetchall()
    return [_arm_request_row_to_dict(r) for r in rows]


def get_grant_commitment_totals(session_grant_id: int) -> tuple[int, int]:
    """Sum of quantity and total spend (max_price_wei, already a per-arm
    TOTAL — see firing.arm_drop) across every arm request tied to this
    grant that still counts against its maxQuantity/valueCapWei cap: every
    non-terminal status, plus 'succeeded' (a completed mint really did
    spend that ETH). 'cancelled'/'failed'/'expired' requests never
    consumed the grant's allowance, so they're excluded.

    This is what lets arm_drop enforce a grant's caps CUMULATIVELY across
    however many arm requests get created against it over its lifetime —
    checking only the single request being validated would let a grant be
    re-armed after each success and spend past what the owner actually
    signed for (there's no on-chain CallPolicy backstopping this anymore;
    see this project's direct-transaction firing model)."""
    with _lock, closing(_conn()) as c:
        rows = c.execute("""
            SELECT quantity, max_price_wei FROM arm_requests
            WHERE session_grant_id=?
              AND status IN ('pending_schedule','scheduled','armed','fired','succeeded')
        """, (session_grant_id,)).fetchall()
    total_quantity = sum(r[0] for r in rows)
    total_spend_wei = sum(int(r[1]) for r in rows)
    return total_quantity, total_spend_wei


def get_active_arm_request_for_drop(owner_address: str, drop_id: int) -> dict | None:
    """The owner's non-terminal (not yet succeeded/failed/cancelled/expired)
    arm request for this drop, if any — used to stop a wallet from arming
    the same drop twice, which would otherwise risk firing twice."""
    owner = owner_address.lower()
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, owner_address, drop_id, session_grant_id, quantity, max_price_wei,
                   go_live_at, status, created_at, updated_at
            FROM arm_requests
            WHERE owner_address=? AND drop_id=?
              AND status IN ('pending_schedule', 'scheduled', 'armed', 'fired')
            ORDER BY id DESC LIMIT 1
        """, (owner, drop_id)).fetchone()
    if not row:
        return None
    return _arm_request_row_to_dict(row)


def try_claim_arm_request(arm_id: int) -> bool:
    """Atomically transitions one arm request from 'armed' to 'fired' —
    the sole guard against firing the same request twice from concurrent
    watcher ticks. Returns True only when THIS call performed the
    transition (i.e. it won the race); False means either the request
    wasn't in 'armed' status (already claimed by another tick, or not
    ready yet) or doesn't exist."""
    now = time.time()
    with _lock, closing(_conn()) as c:
        cur = c.execute(
            "UPDATE arm_requests SET status='fired', updated_at=? WHERE id=? AND status='armed'",
            (now, arm_id)
        )
        c.commit()
        return cur.rowcount == 1


def _arm_request_row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0], "owner_address": row[1], "drop_id": row[2],
        "session_grant_id": row[3], "quantity": row[4], "max_price_wei": row[5],
        "go_live_at": row[6], "status": row[7], "created_at": row[8], "updated_at": row[9],
    }


# ── Mint attempts ─────────────────────────────────────────────────────

def record_mint_attempt(attempt: MintAttemptInput) -> None:
    """Log one mint attempt (success, revert, or error) for an arm request."""
    with _lock, closing(_conn()) as c:
        c.execute("""
            INSERT INTO mint_attempts (
                arm_request_id, attempted_at, tx_hash, user_op_hash, status,
                error_message, gas_used, block_number
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (attempt.arm_request_id, time.time(), attempt.tx_hash, attempt.user_op_hash,
              attempt.status, attempt.error_message, attempt.gas_used, attempt.block_number))
        c.commit()


def get_mint_attempts(arm_request_id: int) -> list[dict]:
    """All mint attempts for an arm request, oldest first."""
    with _lock, closing(_conn()) as c:
        rows = c.execute("""
            SELECT id, arm_request_id, attempted_at, tx_hash, user_op_hash, status,
                   error_message, gas_used, block_number
            FROM mint_attempts WHERE arm_request_id=? ORDER BY attempted_at
        """, (arm_request_id,)).fetchall()
    return [_mint_attempt_row_to_dict(r) for r in rows]


def _mint_attempt_row_to_dict(row: tuple) -> dict:
    return {
        "id": row[0], "arm_request_id": row[1], "attempted_at": row[2], "tx_hash": row[3],
        "user_op_hash": row[4], "status": row[5], "error_message": row[6],
        "gas_used": row[7], "block_number": row[8],
    }


# ── Eligibility cache ─────────────────────────────────────────────────

def upsert_eligibility(eligibility: EligibilityInput) -> None:
    """Cache (or refresh) one wallet's eligibility + Merkle proof for one drop.
    is_eligible=None means 'not yet determined', distinct from False ('checked, not eligible')."""
    owner = eligibility.owner_address.lower()
    now = time.time()
    is_eligible_int = None if eligibility.is_eligible is None else int(eligibility.is_eligible)
    with _lock, closing(_conn()) as c:
        existing = c.execute(
            "SELECT id FROM eligibility_cache WHERE drop_id=? AND owner_address=?",
            (eligibility.drop_id, owner)
        ).fetchone()
        if existing:
            c.execute("""
                UPDATE eligibility_cache
                SET is_eligible=?, merkle_proof=?, phase_id=?, checked_at=?, source=?
                WHERE id=?
            """, (is_eligible_int, eligibility.merkle_proof, eligibility.phase_id, now,
                  eligibility.source, existing[0]))
        else:
            c.execute("""
                INSERT INTO eligibility_cache (
                    drop_id, owner_address, is_eligible, merkle_proof, phase_id,
                    checked_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (eligibility.drop_id, owner, is_eligible_int, eligibility.merkle_proof,
                  eligibility.phase_id, now, eligibility.source))
        c.commit()


def get_eligibility(drop_id: int, owner_address: str) -> dict | None:
    """Cached eligibility for one wallet + drop, or None if never checked."""
    owner = owner_address.lower()
    with _lock, closing(_conn()) as c:
        row = c.execute("""
            SELECT id, drop_id, owner_address, is_eligible, merkle_proof, phase_id,
                   checked_at, source
            FROM eligibility_cache WHERE drop_id=? AND owner_address=?
        """, (drop_id, owner)).fetchone()
    if not row:
        return None
    is_eligible = None if row[3] is None else bool(row[3])
    return {
        "id": row[0], "drop_id": row[1], "owner_address": row[2], "is_eligible": is_eligible,
        "merkle_proof": row[4], "phase_id": row[5], "checked_at": row[6], "source": row[7],
    }
