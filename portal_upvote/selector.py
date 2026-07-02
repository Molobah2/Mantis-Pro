from . import catalog, store


def pick_next_app():
    """
    Returns the next app to upvote or None if the catalog is empty.

    Strategy: least-recently-upvoted across the full live Portal catalog.
    Apps never upvoted sort first (ts=0), then oldest upvote timestamp first.
    This rotates through every visible app before repeating any.
    """
    apps = catalog.get_catalog()
    if not apps:
        return None

    last = store.get_last_upvoted_per_app()

    def _key(a):
        return last.get(a["id"], 0.0)

    return sorted(apps, key=_key)[0]
