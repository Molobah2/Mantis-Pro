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
    """Called by APScheduler once per day. Picks the next Portal app and upvotes it."""
    with _status_lock:
        if _status["running"]:
            return
        _status["running"] = True

    try:
        node_client.restore_from_env()  # restore session from Railway env var if file is gone

        app = selector.pick_next_app()
        if app is None:
            _set(running=False, last_run_ts=time.time(), last_run_ok=False,
                 last_error="Catalog empty — trigger a catalog refresh at /portal-upvote")
            return

        print(f"[upvote] daily job → app_id={app['id']} name={app['name']!r}")
        result  = node_client.call_upvote(app["id"])
        tx_hash = result.get("txHash", "")
        store.record_upvote(app["id"], tx_hash, "success")
        _set(running=False, last_run_ts=time.time(), last_run_ok=True,
             last_run_app=app, last_tx_hash=tx_hash, last_error=None)
        print(f"[upvote] done → tx={tx_hash}")

    except Exception as e:
        print(f"[upvote] error: {e}")
        _set(running=False, last_run_ts=time.time(), last_run_ok=False, last_error=str(e))
