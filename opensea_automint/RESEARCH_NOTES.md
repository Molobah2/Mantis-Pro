# OpenSea Auto-Mint — Research Spike Findings (2026-08-07)

Ran `scripts/opensea_spike.py` (Playwright, read-only, no wallet connected)
against live opensea.io pages to answer: is drop-schedule and Merkle
allowlist data actually reachable client-side, or must v1 assume nothing?

## Confirmed

- **Drop discovery works.** Scraping `/drops` for `a[href^='/collection/']`
  links plus their visible text reliably surfaces live/upcoming drops with
  real status labels ("MINTING NOW", "MINT STARTS IN <countdown>", specific
  dates). 12 real drops captured in one run.
- **The eligibility/allowlist mechanism is real, not speculative.** OpenSea's
  own frontend JS bundles (`_next/static/immutable/chunks/*.js`) contain
  direct references to a SeaDrop-aware internal GraphQL schema:
  `Erc721SeaDropV1`, `Collection.drop.activeDropStage`, `mintStages`, and —
  most tellingly — an error type `MinterNotEligibleForActiveDropStageError`.
  That error name only makes sense if OpenSea's backend computes real,
  per-wallet eligibility server-side. The GraphQL endpoint itself
  (`gql.opensea.io/graphql`) was observed firing for an unrelated query
  (`ToolsDirectoryQuery`), confirming it's live and reachable, just not
  publicly documented for third parties.

## Not confirmed

- A simple, no-wallet page load does **not** surface the actual per-wallet
  proof/eligibility data. Two explanations, not distinguished by this spike:
  (a) it's computed server-side during Next.js SSR/RSC and streamed in a
  non-trivial "flight" payload format rather than plain JSON, or (b) it's
  only fetched client-side at the moment of clicking "Mint" after a wallet
  is connected. Confirming which (and the exact response shape) requires
  driving a real wallet connection + mint-intent click on a live drop —
  something this automated spike deliberately did NOT attempt, since doing
  it wrong on a real drop risks triggering a real transaction.

## Decision

Per discussion with the user: **v1 ships public-stage drop discovery +
mint only.** Allowlist/proof support is not abandoned — it's deferred until
a manual, supervised session (user drives a real wallet connection on a
real or low-stakes drop while traffic is inspected together) confirms the
actual data shape. The DB schema (`eligibility_cache`, with
`is_eligible=NULL` meaning "not yet determined") already supports adding
this later without a migration.

## Reusable evidence

Raw captured evidence lives in `scripts/opensea_spike_findings.json`
(gitignored — regenerate by re-running `scripts/opensea_spike.py`, it's not
committed since it's a point-in-time scrape, not source of truth).
