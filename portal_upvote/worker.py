import time
import threading
from . import store, node_client

_status = {
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
    """Vote once for every registered user, including the owner."""
    with _status_lock:
        if _status["running"]:
            return
        _status["running"] = True

    try:
        apps = store.get_apps()
        if not apps:
            _set(running=False, last_run_ts=time.time(), last_run_ok=False,
                 last_error="Catalog empty — trigger a catalog refresh at /portal-upvote")
            return

        users = store.get_active_users()
        if not users:
            _set(running=False, last_run_ts=time.time(), last_run_ok=False,
                 last_error="No registered users")
            return

        print(f"[upvote] daily run — {len(users)} registered user(s)")
        success_count = 0
        last_tx  = None
        last_app = None

        for user in users:
            addr = user["address"]
            try:
                last        = store.get_last_upvoted_per_app_for_user(addr)
                sorted_apps = sorted(apps, key=lambda a: last.get(a["id"], 0.0))
                voted       = False

                for app in sorted_apps[:10]:
                    try:
                        print(f"[upvote] {addr[:10]}… → app_id={app['id']} name={app['name']!r}")
                        result  = node_client.call_upvote_for_user(user, app["id"])
                        tx_hash = result.get("txHash", "")
                        store.record_upvote(app["id"], tx_hash, "success", addr)
                        print(f"[upvote] {addr[:10]}… done → tx={tx_hash}")
                        voted        = True
                        success_count += 1
                        last_tx  = tx_hash
                        last_app = app
                        break
                    except Exception as inner:
                        err = str(inner)
                        if "revert" in err.lower() or "0xda1a7ce4" in err:
                            store.record_upvote(app["id"], "", "reverted", addr)
                            continue
                        raise

                if not voted:
                    first_app = sorted_apps[0] if sorted_apps else apps[0]
                    store.record_upvote(first_app["id"], "", "already_voted", addr)
                    print(f"[upvote] {addr[:10]}… — all apps reverted, treating as already voted today")

            except Exception as e:
                print(f"[upvote] {addr[:10]}… error: {e}")

        _set(
            running=False,
            last_run_ts=time.time(),
            last_run_ok=success_count > 0,
            last_run_app=last_app,
            last_tx_hash=last_tx,
            last_error=None if success_count > 0 else "No successful votes",
        )

    except Exception as e:
        print(f"[upvote] run error: {e}")
        _set(running=False, last_run_ts=time.time(), last_run_ok=False, last_error=str(e))
