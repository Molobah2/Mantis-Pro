# MANTIS PRO — CLAUDE CODE HANDOFF

You are taking over full development of **Mantis Pro**, a Flask web app at **mantispro.xyz** serving a suite of intelligence dashboards for the Litany protocol (an agentic NFT/gaming ecosystem on Abstract chain, chainId 2741).

Repo: `github.com/Molobah2/Mantis-Pro` (origin), local path: `C:\Users\HP\Mantis-Pro`

Note: this folder also contains some untracked files from an unrelated tool (Spark Agent, a Telegram bot framework) — `roots.sst`, `spark-install.ps1`, `test.json`. These are not part of the website and should never be `git add`-ed.
Deploy: Railway, auto-deploys on `git push origin master`.
Agent identity: wallet `0x40fafac283f5eda53bc572c0bc02caebca96036e` ("MantisPro", Agent #857, EIP-8004 registered, 8004scan).

## YOUR OPERATING MODE

You have full edit/run/deploy permission on this repo. Make changes directly, test them, commit, and push — don't just describe changes and wait. When the user describes a feature or bug, investigate the actual code first (don't assume), fix it, verify it parses/runs, then deploy.

For Python: always verify with `python3 -c "import ast; ast.parse(open('agent.py').read())"` before considering a backend change done.
For frontend: this is vanilla HTML/CSS/JS per page (no build step) — each tool is one self-contained `.html` file with inline `<style>` and `<script>`. No bundlers, no frameworks unless explicitly asked.

## DESIGN STANDARD — NON-NEGOTIABLE

Every page must feel like a **premium institutional intelligence terminal**. Reference points: Bloomberg Terminal, Nansen, Arkham, CoinMarketCap Pro, Linear, Stripe, Palantir. Never: Bootstrap-default, generic admin dashboard, spreadsheet/table-first UI, blockchain-explorer aesthetics.

Concretely:
- Dark intelligence tools (Activity Explorer, Operator Profile): near-black backgrounds (`#050907`–`#0c1410`), glassmorphism panels (`rgba(255,255,255,.03-.05)` with 1px subtle borders), emerald green accent (`#23e07a` / `#10a050`) with soft glows on key numbers/rings, JetBrains Mono for all numerals/addresses/hashes, Inter for UI text.
- The main `/litany` dashboard is **light** (white/green institutional) — this was an explicit, twice-reaffirmed user preference. Do not redesign it dark unless the user explicitly asks again.
- Card-based layouts over tables wherever the content is about people/operators/events — entity-first, not row-first. Visual hierarchy: avatar/identity → action → metadata, with wallet addresses always secondary/muted, never the headline.
- Every interactive element needs a hover state. Numbers that represent live/changing data should animate (count-up, easing).
- High density is good — Bloomberg-style information density beats whitespace-heavy minimalism for this audience, but never at the cost of visual hierarchy.

If a build looks "fine" but generic, it has failed the brief. Iterate until it feels premium.

## DATA INTEGRITY — HARD RULE

**Never fabricate, simulate, or estimate data.** Not names, avatars, scores, volumes, floors, rarity, factions, reputation, or activity. Every number/label on every page must trace to one of the verified sources below. If something can't be verified, show "Verification Unavailable" / "Awaiting Protocol Data" / a generated identicon — never a plausible-looking fake value. This is the single most important constraint in this codebase and has been enforced consistently; do not relax it for "demo polish."

## VERIFIED DATA SOURCES (use only these — do not guess endpoints)

- **Abstract Mainnet**: chainId 2741, RPC `https://api.mainnet.abs.xyz` (proxied via app's `/api/abstract-rpc`), explorer `abscan.org`. Blocks are ~1 second apart — a "recent blocks" window must be large (12k+ chunked backward scans) or it returns nothing.
- **LitanyCards ERC-721**: `0xd44abe71c312FCAf73cC20f7DF61C39A89C203eB`, 8,000 supply. `tokenURI(id)` returns base64 JSON with embedded base64 SVG image and `attributes`. Transfer topic0: `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`.
- **Litany Mesh API** (public, no auth, JSON envelope `{ok,data}`): base `https://litany.gg/api/mesh`. Reads: `/leaderboards` (rank/wallet/faction/value), `/wallet/:address` (faction, claims, anchors, achievements, dates), `/faction-stats`, `/faction/:name`, `/events`, `/map`, `/health`. This is the source for faction (horizon/lens/breach), claims, anchors, achievements, mesh rank. Proxied server-side via `/api/litany-mesh?path=...` with an allowlist — extend the allowlist in `agent.py` if you need a new mesh path, don't bypass it.
- **Litany market leaderboard** (`litany.gg/market/leaderboard`, SSR): the only source of real operator **display names** for ~60-68 ranked wallets. Scraped server-side, cached. This is why name search only resolves names from this set — don't fake name resolution for wallets outside it.
- **ANS (Abstract Name Service)**: `0x86a282845a61302Ba4735d111b1a1417f6e617Ad`, on-chain, `getNameByAddress` / `textRecords` (avatar, twitter). Small coverage but real.
- **OpenSea API** (proxied via `/api/opensea?path=...`, key in `OPENSEA_API_KEY` env): NFT holdings, collection floor. Paginates 50/page via `next` cursor — always paginate fully, don't assume one page is everything (this caused a real "49 cards instead of 94" bug).
- **No public Abstract Portal/AGW avatar API exists** — extensively verified absent. Real avatar/X-pfp coverage is only via ANS. Everyone else gets a generated identicon. Don't reintroduce a fake "portal avatar" source.

## FACTION COLORS (apply consistently across every tool)

- **Horizon: `#D1AB58`** (official gold — confirmed via screenshot from Litany's own brand reference, this overrode an earlier incorrect green)
- **Lens: `#2f6df0`** (blue)
- **Breach: `#e5484d`** (red)

## CURRENT FILE MAP (all in repo root, served by `agent.py`)

- `agent.py` — Flask app. Routes: `/`, `/litany`, `/litany/scanner`, `/litany/rarity`, `/activity`, `/operator/<address>`, `/rarity_index.json`, `/api/abstract-rpc` (POST proxy), `/api/opensea` (GET proxy), `/api/agw-profile`, `/api/identities` (batch identity+faction resolver), `/api/avatar` (image proxy w/ identicon fallback), `/api/litany-mesh` (allowlisted mesh proxy), `/api/card-image/<id>` (decodes on-chain SVG), `/api/card-meta/<id>` (decodes on-chain attributes), `/api/card-stats` (rarity index lookup), `/api/operator-search` (name→address resolver from leaderboard scrape + mesh).
- `index.html` — homepage, light theme, links to Litany + Gigaverse (in progress) coverage.
- `litany.html` — main Litany dashboard, **light institutional theme**. Sections: Litany Core, Market Overview, Collection, Recent Activity (5-event preview linking to `/activity`), Mesh Command Center.
- `litany_scanner.html` — Wallet Scanner, light theme. Search by address or operator name (via `/api/operator-search`), shows holdings, links to `/operator/<address>`.
- `litany_rarity.html` — Rarity Engine, light theme. Consumes `rarity_index.json`.
- `activity.html` — Activity Explorer, **dark premium**. Dedicated full feed (separate from homepage preview) with filter tabs, card-based events, side panels (Live Stats, Top Operators, Faction Control), name search bar.
- `operator.html` — Operator Profile, **dark premium intelligence terminal** (just rebuilt — sidebar nav, hero with animated score gauge, stats bar, reputation radar, activity heatmap, portfolio donut, journey timeline, card grid with detail modal, global comparison). This is the flagship page — keep it at this bar going forward.
- `rarity_index.json` — precomputed rarity data for ~3,021 cards (tokenId → rank/percentile/score), built by `build_rarity_index.py`.
- `identities.db` — SQLite cache for resolved identities (wallet, name, twitter, avatar, source, updated).

## OPERATOR SCORE (the signature Mantis metric — keep this formula stable unless asked to change it)

Built in `operator.html`'s `computeScore()`: cards owned ×12, best card percentile ×2.2, average portfolio percentile ×0.8, mesh claims ×6, anchors ×35, achievements ×80, mesh rank bonus (300/175/75 for top 10/25/50). Tier thresholds (Wanderer 0, Explorer 250, Pathfinder 1000, Architect 3000, Sovereign 8000, Legend 15000). Always show the breakdown — never just the number.

## DEPLOY WORKFLOW

```
cd C:\Users\HP\Mantis-Pro
git add <files>
git commit -m "description"
git push origin master
```

Railway auto-deploys on push (~60s). Railway build env: Python version is sometimes auto-selected too new and breaks the build (`RAILPACK_PYTHON_VERSION` Railway variable or `.python-version` file pins it — currently should be pinned to 3.12; check Railway dashboard → Variables if a build fails with a mise/python error).

## WORKING STYLE THE USER EXPECTS

- Investigate actual code/data before proposing fixes — don't guess at root causes from a screenshot alone if you can check the source.
- When something looks broken, find the root cause (e.g. "49 instead of 94 cards" was an OpenSea pagination bug, not a display bug) rather than patching the symptom.
- Ship complete, working changes — not partial scaffolding with TODOs.
- The user will paste screenshots of bugs/design feedback often. Read them carefully for exact colors, exact numbers, exact UI complaints — match precisely.
- Maintain the honesty principle even under pressure to "make it impressive" — premium design and honest data are both non-negotiable, simultaneously.
