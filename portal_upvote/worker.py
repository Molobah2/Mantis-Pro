import time
import threading
from . import selector, store, node_client

_status      = {
    "last_run_ts":  None,
    "last_run_ok":  None,
    "last_run_app": None,
    "last_tx_hash": None,
    "last_error":   None,
    "running":      False,
}
_status_lock = threading.Lock()


def _set(**kw):
    with _status_lock:
        _status.update(kw)


def get_status():
    with _status_lock:
        return dict(_status)


def run_daily_upvote():
    """
    Phase 1 — owner vote via SESSION_FILE (single-user path, unchanged).
    Phase 2 — vote once per registered user stored in the users DB table.
    """
    with _status_lock:
        if _status["running"]:
            return
        _status["running"] = True

    try:
        # ── Phase 1: owner session (SESSION_FILE) ──────────────────────
        node_client.restore_from_env()

        apps = store.get_apps()
        if not apps:
            _set(running=False, last_run_ts=time.time(), last_run_ok=False,
                 last_error="Catalog empty — trigger a catalog refresh at /portal-upvote")
            return

        last        = store.get_last_upvoted_per_app()
        sorted_apps = sorted(apps, key=lambda a: last.get(a["id"], 0.0))
        success     = False

        for app in sorted_apps[:10]:
            try:
                print(f"[upvote] daily job (owner) → app_id={app['id']} name={app['name']!r}")
                result  = node_client.call_upvote(app["id"])
                tx_hash = result.get("txHash", "")
                store.record_upvote(app["id"], tx_hash, "success")
                _set(running=False, last_run_ts=time.time(), last_run_ok=True,
                     last_run_app=app, last_tx_hash=tx_hash, last_error=None)
                print(f"[upvote] owner done → tx={tx_hash}")
                success = True
                break
            except Exception as inner:
                err = str(inner)
                if "revert" in err.lower() or "0xda1a7ce4" in err:
                    store.record_upvote(app["id"], "", "reverted")
                    print(f"[upvote] owner: app {app['id']} reverted, trying next")
                    continue
                raise

        if not success:
            _set(running=False, last_run_ts=time.time(), last_run_ok=False,
                 last_error="All candidate apps reverted")

    except Exception as e:
        print(f"[upvote] owner error: {e}")
        _set(running=False, last_run_ts=time.time(), last_run_ok=False, last_error=str(e))

    finally:
        # ── Phase 2: registered users ──────────────────────────────────
        _set(running=False)
        _run_user_votes(apps if 'apps' in dir() else store.get_apps())


def _run_user_votes(apps):
    """Vote once per registered user. Runs after the owner vote completes."""
    if not apps:
        return

    from .config import AGW_ADDRESS
    owner_addr = (AGW_ADDRESS or "").lower()

    users = store.get_active_users()
    if not users:
        return

    print(f"[upvote] running user votes for {len(users)} registered user(s)")

    for user in users:
        if user["address"] == owner_addr:
            continue  # owner already voted in phase 1

        try:
            last = store.get_last_upvoted_per_app_for_user(user["address"])
            sorted_apps = sorted(apps, key=lambda a: last.get(a["id"], 0.0))
            voted = False

            for app in sorted_apps[:10]:
                try:
                    print(f"[upvote] user {user['address'][:10]}… → app_id={app['id']}")
                    result  = node_client.call_upvote_for_user(user, app["id"])
                    tx_hash = result.get("txHash", "")
                    store.record_upvote(app["id"], tx_hash, "success", user["address"])
                    print(f"[upvote] user {user['address'][:10]}… done → tx={tx_hash}")
                    voted = True
                    break
                except Exception as inner:
                    err = str(inner)
                    if "revert" in err.lower() or "0xda1a7ce4" in err:
                        store.record_upvote(app["id"], "", "reverted", user["address"])
                        continue
                    raise

            if not voted:
                print(f"[upvote] user {user['address'][:10]}… — all apps reverted")

        except Exception as e:
            print(f"[upvote] user {user['address'][:10]}… error: {e}")
