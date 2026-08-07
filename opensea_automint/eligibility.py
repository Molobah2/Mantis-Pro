"""
v1 eligibility placeholder: determines whether a drop's PUBLIC stage is
currently mintable. Per-wallet Merkle allowlist checks are not confirmed
reachable client-side yet (see RESEARCH_NOTES.md) — this module is
intentionally thin so future allowlist logic can extend it without callers
elsewhere needing to change.
"""
import json


def is_publicly_mintable(drop: dict) -> bool:
    """
    drop is a dict shaped like store.get_tracked_drops() rows (has a
    'stage_data' JSON string field containing {"status": ..., "status_detail": ...}
    as written by drops.get_drops()).
    Returns True only if status == "minting_now". Returns False (not an
    exception) if stage_data is missing/unparseable — fail safe to
    'not mintable' rather than crash.
    """
    stage_data = drop.get("stage_data")
    if not stage_data:
        return False
    try:
        parsed = json.loads(stage_data)
    except (TypeError, ValueError):
        return False
    if not isinstance(parsed, dict):
        return False
    return parsed.get("status") == "minting_now"
