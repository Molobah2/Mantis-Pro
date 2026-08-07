"""
Research spike — NOT part of the app, not imported by anything, safe to delete.

Question this answers: does opensea.io expose (a) a live/upcoming drop
schedule and (b) per-wallet Merkle allowlist proofs anywhere a browser can
see them (page state, XHR/fetch responses, or references inside loaded JS
bundles) — or is that data only ever computed server-side/on submit, in
which case v1 of the OpenSea Auto-Mint tool must fall back to public-stage
mints only?

This is read-only reconnaissance of a normal public webpage — the same
information anyone's browser DevTools "Network" tab would show. It makes no
attempt to bypass authentication, rate limits, or any access control, and
never connects a real wallet.

Known limitation: without a real connected wallet, any API call that only
fires *after* wallet connection (e.g. "here is your personalized Merkle
proof") cannot be observed this way. This spike can only prove whether the
DATA SHAPE / ENDPOINTS exist (e.g. referenced in JS bundles, or public-stage
schedule data visible pre-connection) — not exercise a real per-wallet proof
lookup. That distinction is called out explicitly in the conclusion.

Usage:  venv/Scripts/python.exe scripts/opensea_spike.py
Output: scripts/opensea_spike_findings.json (raw evidence) + a printed summary.
"""

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

OUTPUT_PATH = Path(__file__).parent / "opensea_spike_findings.json"

# Only these are worth digging into deeply — everything else (analytics,
# fonts, unrelated widgets) is noise we filter out.
RELEVANT_HOSTS = ["opensea.io", "api.opensea.io"]
NOISE_URL_SUBSTRINGS = ["google-analytics", "quill.run", "gtm", "sentry", "segment.io"]

# Substrings worth flagging inside response bodies (JSON, RSC payloads, or JS bundles).
INTERESTING_BODY_HINTS = [
    "merkleproof", "merkle_proof", "merkletree", "eligib", "allowlist",
    "seadrop", "mintpublic", "mintallowlist", "mintsigned", "stageactive",
    "phaseid", "phase_id", "mintstage", "dropstage",
]

MAX_BODY_SNIPPET = 400
MAX_HITS_PER_RESPONSE = 8


def _is_relevant(url: str) -> bool:
    if any(noise in url for noise in NOISE_URL_SUBSTRINGS):
        return False
    return any(host in url for host in RELEVANT_HOSTS)


def _scan_body_for_hints(body: str) -> list[dict]:
    lower = body.lower()
    hits = []
    for hint in INTERESTING_BODY_HINTS:
        idx = lower.find(hint)
        if idx != -1:
            start = max(0, idx - 60)
            end = min(len(body), idx + 60)
            hits.append({"hint": hint, "context": body[start:end]})
        if len(hits) >= MAX_HITS_PER_RESPONSE:
            break
    return hits


def _capture_responses(page, label: str) -> list[dict]:
    captured = []

    def on_response(response):
        url = response.url
        if not _is_relevant(url):
            return
        content_type = response.headers.get("content-type", "")
        entry = {"url": url, "status": response.status, "content_type": content_type}
        # Read body for anything text-ish — JSON, RSC (text/x-component), JS bundles.
        is_textlike = any(t in content_type for t in ("json", "x-component", "javascript", "text/html"))
        if is_textlike:
            try:
                body = response.text()
                hits = _scan_body_for_hints(body)
                if hits:
                    entry["keyword_hits"] = hits
                elif len(body) < 200:
                    entry["body_snippet"] = body[:MAX_BODY_SNIPPET]
            except Exception as e:
                entry["body_error"] = str(e)
        # Only keep entries that actually found something, to keep output readable.
        if entry.get("keyword_hits") or entry.get("body_snippet"):
            captured.append(entry)

    page.on("response", on_response)
    return captured


def run_spike() -> dict:
    findings = {
        "drops_page": {"url": "https://opensea.io/drops", "drop_cards": [],
                        "keyword_evidence": []},
        "collection_page": None,
        "conclusion": None,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ))

            drops_evidence = _capture_responses(page, "drops")
            print("[spike] loading https://opensea.io/drops ...")
            page.goto("https://opensea.io/drops", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(4000)
            findings["drops_page"]["keyword_evidence"] = drops_evidence

            # Pull visible drop cards via the DOM (server-rendered text), not raw HTML regex —
            # this should surface each drop's name + live/upcoming status + any visible time.
            cards = page.locator("a[href^='/collection/']")
            count = cards.count()
            seen_hrefs = set()
            drop_cards = []
            for i in range(min(count, 20)):
                try:
                    href = cards.nth(i).get_attribute("href")
                    text = cards.nth(i).inner_text(timeout=2000).strip()
                except Exception:
                    continue
                if href and href not in seen_hrefs and text:
                    seen_hrefs.add(href)
                    drop_cards.append({"href": href, "visible_text": text[:300]})
            findings["drops_page"]["drop_cards"] = drop_cards
            print(f"[spike] found {len(drop_cards)} drop card(s) with visible text")

            if drop_cards:
                collection_url = f"https://opensea.io{drop_cards[0]['href']}"
                print(f"[spike] loading collection page: {collection_url}")
                collection_evidence = _capture_responses(page, "collection")
                page.goto(collection_url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(4000)

                # Grab visible page text (mint button, countdown, price, "eligible" wording etc.)
                try:
                    visible_text = page.locator("body").inner_text(timeout=3000)
                except Exception:
                    visible_text = ""
                text_hints = _scan_body_for_hints(visible_text)

                findings["collection_page"] = {
                    "url": collection_url,
                    "keyword_evidence": collection_evidence,
                    "visible_text_keyword_hits": text_hints,
                    "visible_text_snippet": visible_text[:1000],
                }
                print(f"[spike] collection page: {len(collection_evidence)} response(s) with "
                      f"keyword hits, {len(text_hints)} hit(s) in visible page text")
        finally:
            browser.close()

    evidence_count = (
        len(findings["drops_page"]["keyword_evidence"])
        + (len(findings["collection_page"]["keyword_evidence"]) if findings["collection_page"] else 0)
        + (len(findings["collection_page"]["visible_text_keyword_hits"]) if findings["collection_page"] else 0)
    )
    if evidence_count == 0:
        findings["conclusion"] = (
            "NO keyword evidence of Merkle proof / allowlist / stage machinery found in any "
            "response body or visible page text, even after broadening the search to JS bundles "
            "and RSC payloads. Combined with no publicly documented API for this data, the "
            "responsible default for v1 is public-stage mints only, with allowlist support "
            "deferred until a connected-wallet manual test (which this automated spike cannot "
            "safely perform) confirms proof data actually reaches the client after connecting."
        )
    else:
        findings["conclusion"] = (
            f"{evidence_count} keyword hit(s) found — see scripts/opensea_spike_findings.json "
            "for exact context. This shows the RELEVANT TERMS exist somewhere client-side "
            "(e.g. referenced in a JS bundle) but does NOT by itself prove a real per-wallet "
            "proof is deliverable without a connected wallet — manual verification with a real "
            "wallet connection is still required before building the eligibility engine against "
            "this as a confirmed data source."
        )

    return findings


if __name__ == "__main__":
    result = run_spike()
    OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    print(f"\n[spike] wrote findings to {OUTPUT_PATH}")
    print(f"[spike] CONCLUSION: {result['conclusion']}")
