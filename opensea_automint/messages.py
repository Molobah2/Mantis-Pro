"""
Canonical signed-message builders for the OpenSea Auto-Mint tool's
owner-authorized actions (grant / arm / cancel).

Every one of these messages gets built TWICE, independently, by two
different languages: once in the browser (wallet-helper/connect-src/
eth-connect.tsx, in TypeScript) to produce the signature, and once here (in
Python) to verify it. Signature verification recovers the signer's address
from the message TEXT itself — so if the two implementations ever produce
different text for the same inputs, every legitimate signature fails
verification. Treat any change to these functions as a breaking change
requiring a matching frontend update in the same deploy.
"""


def build_grant_message(
    session_address: str, nft_contract: str, max_quantity: int,
    value_cap_wei: str, expires_at: float, timestamp: int,
) -> str:
    """The message an owner's wallet signs to authorize a fresh session key
    to mint one specific collection, up to a quantity/price cap, until it
    expires. There is no on-chain enforcement of these bounds (this project
    fires via a direct signed transaction from the session key, not an
    ERC-4337 smart-account CallPolicy) — this signature, and the resulting
    session_grants row, is the ONLY authorization record; firing.py's
    pre-fire checks are what actually hold the line."""
    return (
        "Mantis Pro OpenSea Auto-Mint\n"
        "action: grant\n"
        f"sessionAddress: {session_address}\n"
        f"nftContract: {nft_contract}\n"
        f"maxQuantity: {max_quantity}\n"
        f"valueCapWei: {value_cap_wei}\n"
        f"expiresAt: {expires_at}\n"
        f"timestamp: {timestamp}"
    )


def build_arm_message(collection_slug: str, quantity: int, max_price_wei: str, timestamp: int) -> str:
    """The message an owner's wallet signs to authorize arming a drop for
    auto-firing."""
    return (
        "Mantis Pro OpenSea Auto-Mint\n"
        "action: arm\n"
        f"collectionSlug: {collection_slug}\n"
        f"quantity: {quantity}\n"
        f"maxPriceWei: {max_price_wei}\n"
        f"timestamp: {timestamp}"
    )


def build_cancel_message(arm_id: int, timestamp: int) -> str:
    """The message an owner's wallet signs to authorize cancelling an arm
    request."""
    return (
        "Mantis Pro OpenSea Auto-Mint\n"
        "action: cancel\n"
        f"armId: {arm_id}\n"
        f"timestamp: {timestamp}"
    )


def build_revoke_grant_message(grant_id: int, timestamp: int) -> str:
    """The message an owner's wallet signs to authorize revoking a session
    grant on demand. Revocation only stops THIS APP from using that key for
    future arm/fire actions — it has no on-chain effect and does not sweep
    any ETH already sitting at the session address (see firing.revoke_grant)."""
    return (
        "Mantis Pro OpenSea Auto-Mint\n"
        "action: revoke-grant\n"
        f"grantId: {grant_id}\n"
        f"timestamp: {timestamp}"
    )
