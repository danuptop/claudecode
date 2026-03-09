# REPORT BASE — THIRD AUDIT: MAR 09, 2026

**Date:** March 9, 2026
**Scope:** Mar 09 pipeline run + validation of Mar 08 patch deployment
**Trigger:** User-reported duplicates: USD.AI ($19.4M) vs USD AI PERMIAN LABS ($13.4M)
**Verdict:** ALL Mar 08 patches remain undeployed. Every identified bug is still active in production. New duplicates and slop created overnight.

---

## 0. CRITICAL: MAR 08 PATCHES NEVER DEPLOYED

The Mar 08 audit identified 11 bugs (F-01 through F-11) and created 3 patch description files.
**None were deployed.** The audit report itself states:

> Script patches deployed to Tony: BLOCKED — SSH unavailable from sandboxed audit environment

The pipeline ran again at `2026-03-09T034743` with the same broken scripts.
**Every bug is still producing garbage in production.**

---

## 1. DUPLICATE CONFIRMED: USD.AI = USD AI PERMIAN LABS

These two Report Base pages are about the **same entity**:

| Field | Mar 08 Page | Mar 09 Page |
|-------|-------------|-------------|
| Page ID | `31cf30f9-bdff-81cc` | `31ef30f9-bdff-8176` |
| Title | USD.AI — $19.4M PUBLIC SALE | USD AI PERMIAN LABS — $13.4M FUNDING |
| REPORT KEY | `fundraising-intel:v3:usd-ai:19400000` | `fundraising-intel:v3:usd-ai-permian-labs:13400000` |
| QA STATUS | WARN | PASS |
| COMPANY | "USD.AI" | (empty) |
| POC | 6 team members | (none) |
| Content | Bare skeleton | Full outreach+hiring (all boilerplate) |

**Proof they're the same:** The Mar 08 audit found investor parsing corruption in the USD.AI page: `"David Choi (CEO/co-founder"`, `"Permian Labs)"`, `"Conor Moore"` — Permian Labs is USD.AI's parent/lab entity.

**Why dedup failed:**
1. Slug mismatch: `usd-ai` ≠ `usd-ai-permian-labs`
2. Amount mismatch: $19.4M vs $13.4M (31% gap, exceeds 5% tolerance)
3. No entity alias resolution exists in the pipeline

---

## 2. NEW FINDINGS: MAR 09 BATCH QUALITY AUDIT

### F-12 [CRITICAL]: QA STATUS=PASS on pages with zero actionable content

Every Mar 09 enriched page passes QA despite having:
- Empty COMPANY field
- "PRIMARY POC NOT IDENTIFIED"
- "No contact data"
- "Not listed" for investors
- Round type "UNKNOWN"
- "API timeout" in competitor benchmark
- Wrong company descriptions

**Affected pages (Mar 09 run `funding-intel-20260309T034743`):**

| Page | Amount | QA STATUS | Issues |
|------|--------|-----------|--------|
| USD AI PERMIAN LABS | $13.4M | PASS | Duplicate entity, empty COMPANY, no POC, no investors, UNKNOWN round type |
| CREMA FINANCE | $5.4M | PASS | No POC, no investors, UNKNOWN round type, API timeout |
| LAYERZERO | $261.3M | PASS | **Wrong company** (power systems, not crypto), no POC, no investors |
| BACKPACK | $20M | PASS | No POC, UNKNOWN round type |
| NOVIG | $75M | PASS | Not audited in detail |
| KUTT | $0 | PASS | $0 amount (F-11 still active), M&A not a funding round |

### F-13 [HIGH]: Domain scraper resolves wrong company

LAYERZERO page scraped `layerzero.com` (LayerZero Power Systems — "Static Transfer Switches, Power Distribution Units") instead of LayerZero Labs (crypto interoperability protocol, `layerzero.network`). The $261.3M aggregate is LayerZero Labs' cumulative funding.

### F-14 [HIGH]: Outreach angles contain fabricated claims

The outreach angle for USD AI PERMIAN LABS states "solid VC backing" when zero investors are listed. This phrase is hardcoded in the template regardless of actual investor data. Every outreach angle is functionally identical:

```
"Congrats on the $XM raise with solid VC backing—[adjective] for [COMPANY].
Up Top Search specializes in crypto-native talent... Let's chat."
```

### F-15 [MEDIUM]: Hiring predictions are static templates, not intelligence

Every page receives identical "Predicted Hiring Needs" from a lookup table keyed only on vertical classification. CREMA FINANCE (DeFi) and LAYERZERO (interoperability) both get "Institutional Sales", "Prime Brokerage", "FX/Quant Trader" because the vertical classifier defaults to "Institutional Crypto".

The only hiring signal is always:
```
[█░░░░ 2/10] Hiring inferred from $XM UNKNOWN raise — typical post-raise expansion expected
```

This is not intelligence. It's a fill-in-the-blank sentence.

### F-17 [MEDIUM]: Vertical classifier ignores domain scrape data

NOVIG is a US sports prediction market (betting platform). The domain scrape correctly identified this from novig.co. But the vertical classifier still assigned "Institutional Crypto", generating nonsensical hiring predictions (Prime Brokerage, FX/Quant Trader). The classifier uses a static default, not the scraped description.

This also raises a pipeline scope question: NOVIG may not belong in a crypto fundraising pipeline at all.

### F-16 [MEDIUM]: Round type always shows UNKNOWN for enriched pages

The outreach section always displays round type as "UNKNOWN" even when the source page title contains the round type (e.g., "FUNDING", "PUBLIC SALE", "SEED"). The enrichment pipeline doesn't pass round_type to the outreach template.

---

## 3. ROOT CAUSE: WHY AUDITS AND PATCHES AREN'T STICKING

### 3a. Patches are description files, not deployable code

The `patches/` directory contains Python files that are actually prose:
```python
# SEARCH FOR a block similar to:
#   def create_outreach_page(...):
# ACTION: Comment out or guard the entire standalone page creation
```

These are instructions for a human to manually edit production scripts on the Tony server. They are not diffs, not executable patches, not deployable. They require SSH access + manual editing.

### 3b. Dedup model is structurally insufficient

The REPORT KEY format `fundraising-intel:v3:{slug}:{amount}` cannot handle:
- **Company name variations**: USD.AI / USD AI / USD AI PERMIAN LABS
- **Amount variations**: $19.4M / $13.4M for the same deal from different sources
- **Aggregated amounts**: LayerZero's $261.3M is cumulative funding, not a single round
- **Non-funding events**: KUTT M&A is not a fundraising round

**What's needed:** Entity resolution (fuzzy company matching, alias table) BEFORE key generation.

### 3c. QA validator checks structure, not quality

`qa_validator.py` only checks:
- ✅ REPORT KEY populated
- ✅ Error regex patterns (HTTPSConnectionPool, etc.)
- ✅ Marker pair presence
- ✅ Content length > 500 chars

It does NOT check:
- ❌ COMPANY field populated
- ❌ POC actually identified (vs placeholder)
- ❌ Investors present (vs "Not listed")
- ❌ Round type resolved (vs "UNKNOWN")
- ❌ Description relevance/accuracy
- ❌ Domain correctness
- ❌ $0 amounts
- ❌ Outreach angle contains fabricated claims

### 3d. Enrichment pipeline produces boilerplate, not research

The founder-intel-pipeline's failure cascade:
1. Domain lookup → wrong domain or fails
2. Website scrape → scrapes news site chrome or wrong company
3. DuckDuckGo search → times out (every time, per evidence)
4. Grok API → times out (every time, per evidence)
5. **Fallback: stamp a static template with $COMPANY, $AMOUNT, $VERTICAL**

The fallback IS the normal path. Real enrichment never succeeds. The output is a template stamp pretending to be research.

---

## 4. REQUIRED FIXES (PRIORITIZED)

### P0: Deploy existing patches to production NOW

The 3 patch descriptions from Mar 08 must be applied to Tony:
1. `founder-intel-pipeline.py`: Kill standalone OUTREACH TARGET page creation
2. `funding-intel-brief.py`: Fix investor parsing, normalize REPORT KEYs
3. `hiring_intel_module.py`: Suppress error-as-signal, handle Grok timeouts

**These have been "ready" for 24+ hours and are still not deployed.**

### P1: Fix QA validator — add content quality checks

Add to `qa_validator.py`:

```python
# FAIL conditions (not PASS):
- COMPANY field empty
- Amount == 0 (F-11)
- Round type == "UNKNOWN" or missing
- POC section contains "NOT IDENTIFIED" with no fallback
- Outreach angle contains "solid VC backing" when investors == "Not listed"

# WARN conditions:
- Investors == "Not listed" or "Unknown"
- Description == "N/A"
- All API calls timed out (competitor benchmark, DuckDuckGo, Clay)
- Domain doesn't match company vertical (e.g., power systems ≠ crypto)
```

### P2: Add entity resolution / company alias matching

Before generating REPORT KEY, normalize company name:
1. Strip legal suffixes (Labs, Protocol, Finance, DAO, Inc, Ltd)
2. Normalize punctuation (USD.AI → USD AI → usd-ai)
3. Check an alias table: `{"usd-ai-permian-labs": "usd-ai", "layerzero-labs": "layerzero"}`
4. Query existing pages with fuzzy prefix match, not just exact slug

### P3: Stop enriching when all data sources fail

If domain scrape, DuckDuckGo, Grok, and Clay all fail/timeout:
- Do NOT write a template-stamped outreach section
- Set QA STATUS = WARN with note "Enrichment failed — all sources timed out"
- Leave the marker placeholders unfilled
- A page with no outreach is better than a page with fabricated outreach

### P4: Fix domain resolution

- Maintain a known-domains table for major crypto companies
- When scraping, verify the domain's content matches the company's vertical
- If `layerzero.com` returns "Power Distribution Units", reject it and try `layerzero.network`

### P5: Archive/merge the USD.AI duplicate

- `31ef30f9-bdff-8176` (USD AI PERMIAN LABS $13.4M) should be archived
- Content (if any is salvageable) should be merged into `31cf30f9-bdff-81cc` (USD.AI $19.4M)
- The Mar 08 page is the canonical, with POC assignments and team awareness

### P6: Audit all Mar 09 pages for $0 amounts and wrong domains

Run a sweep of the Mar 09 batch:
- Archive KUTT ($0 M&A — not a fundraising event)
- Fix LAYERZERO domain/description
- Verify all other Mar 09 pages against F-11 through F-16

---

## 5. PAGES ACTIONED

| Page ID | Title | Action | Status |
|---------|-------|--------|--------|
| `31ef30f9-bdff-8176` | USD AI PERMIAN LABS — $13.4M | ARCHIVED — duplicate of USD.AI | ✅ DONE |
| `31ef30f9-bdff-8140` | KUTT — $0 M&A | ARCHIVED — not a funding round, $0 parse | ✅ DONE |
| `31ef30f9-bdff-81fe` | LAYERZERO — $261.3M | QA STATUS → FAIL — wrong company (power systems) | ✅ DONE |
| `31ef30f9-bdff-8107` | CREMA FINANCE — $5.4M | QA STATUS → WARN — template fill, no real data | ✅ DONE |
| `31ef30f9-bdff-81f9` | BACKPACK — $20M | QA STATUS → WARN — no POC, template fill | ✅ DONE |
| `31ef30f9-bdff-81ef` | NOVIG — $75M | QA STATUS → WARN — sports betting, not crypto; wrong vertical | ✅ DONE |
| `31cf30f9-bdff-81cc` | USD.AI — $19.4M (canonical) | Updated QA ISSUES with dup cross-ref, LAST AUDITED AT | ✅ DONE |

---

## 6. META-PROCESS FAILURE

The loop is:
1. Audit finds bugs → patches written as description files
2. Patches require manual SSH deployment to Tony
3. SSH is unavailable from the audit environment
4. Pipeline runs again with same bugs
5. More garbage created → another audit triggered
6. GOTO 1

**This loop must break.** Either:
- Deploy patches through a CI/CD pipeline (git push → auto-deploy)
- Give the audit environment SSH access to Tony
- Or: pause the pipeline until patches are confirmed deployed

Running the pipeline without the fixes is strictly worse than not running it — it creates records that require manual cleanup and erode trust in the system.
