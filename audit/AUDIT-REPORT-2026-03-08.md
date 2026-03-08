# REPORT BASE FUNDRAISING INTEL — FORENSIC AUDIT REPORT

**Date:** March 8, 2026 (Sunday)
**Auditor:** Production Reliability — Second-Round Forensic Audit
**Database:** Report Base (`902d47be-68c0-4da8-832a-a52272fc7b39`)
**Data Source:** `598bb1d8-26cb-4e2a-8b4f-8b80e1be7116`
**Scope:** Last 30 days of TYPE=FUNDRAISING INTEL pages + associated OUTREACH TARGET pages

---

## 1. FINDINGS (sorted by severity)

### [CRITICAL] F-01: founder-intel-pipeline.py creates redundant standalone OUTREACH TARGET pages

- **File:** `/home/ubuntu/clawd/scripts/founder-intel-pipeline.py`
- **Failure mode:** For every FUNDRAISING INTEL event, the pipeline creates BOTH:
  1. An embedded outreach section inside the canonical page (via `[[OUTREACH_INTEL_AUTO_START]]`/`[[OUTREACH_INTEL_AUTO_END]]` markers) — correct behavior
  2. A standalone new page with TYPE=OUTREACH TARGET — redundant
- **Evidence:** Every target company had exactly 2 pages: one canonical FUNDRAISING INTEL + one standalone OUTREACH TARGET (5 confirmed clusters across target companies, ~15+ across the full database)
- **Standalone pages have:** Empty REPORT KEY, empty RUN ID, SOURCE SKILL=founder-intel-pipeline
- **User impact:** Report Base is cluttered with duplicate pages. Users see 2 entries per event. The standalone pages have LOWER quality (founder not found, no Clay enrichment, DuckDuckGo errors embedded) compared to the canonical pages.

### [HIGH] F-02: Archived-duplicate references embedded in canonical page bodies

- **Files:** `/home/ubuntu/clawd/scripts/funding-intel-brief.py` (dedup/merge function)
- **Failure mode:** When the dedup system merges duplicates, it writes callout blocks with "Archived duplicate: PAGE_TITLE" directly into the canonical page body. These references persist forever and reference archived pages users shouldn't navigate to.
- **Affected pages:** USD.AI, CYCLOPS, BLUPRYNT, BASED (4 of 6 target companies)
- **User impact:** Confusing archived-page references visible to end users in canonical reports.

### [HIGH] F-03: Investor list parsing mixes person names with company names

- **File:** `/home/ubuntu/clawd/scripts/funding-intel-brief.py` (investor aggregation)
- **Failure mode:** When aggregating investors from multiple source records, the parser does not filter out person-role tokens. E.g., "Alex Wilson (co-founder" and "Cyclops)" appear as separate investor entries.
- **Affected pages:** CYCLOPS (6 corrupt entries), USD.AI (3 corrupt entries: "David Choi (CEO/co-founder", "Permian Labs)", "Conor Moore")
- **User impact:** Investor lists are noisy and unreliable.

### [MEDIUM] F-04: DuckDuckGo transport errors emitted as hiring signals

- **File:** `/home/ubuntu/clawd/scripts/hiring_intel_module.py`
- **Failure mode:** When DuckDuckGo web search fails with HTTPSConnectionPool timeout, the error string is recorded as a "hiring signal" with confidence 7/10 instead of being suppressed.
- **Evidence:** ALL standalone outreach pages contain: `"Search error: HTTPSConnectionPool(host='html.duckduckgo.com', port=443): Max retries exceeded"`
- **User impact:** False hiring signals in every report.

### [MEDIUM] F-05: Grok API timeout errors embedded in competitor benchmarks

- **File:** `/home/ubuntu/clawd/scripts/hiring_intel_module.py`
- **Failure mode:** Grok API timeouts produce raw error text in the competitor benchmark section instead of graceful fallback.
- **Evidence:** CYCLOPS outreach page: `"[Grok error: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out.]"`
- **User impact:** Broken competitor benchmark sections.

### [MEDIUM] F-06: REPORT KEY format inconsistency between legacy and v3

- **File:** `/home/ubuntu/clawd/scripts/funding-intel-brief.py`
- **Failure mode:** Older pages use date-based keys (e.g., `fundraising-intel:okx:2026-03-05`) while newer pages use amount-based v3 keys (e.g., `fundraising-intel:v3:okx:200000000`). The dedup system may not cross-match these formats.
- **Evidence:** OKX has 3 separate pages ($0, $200M, $25B) with different key formats.
- **User impact:** Duplicate pages for same company when amount parsing changes between runs.

### [LOW] F-07: Generic "AI Crypto" vertical classification

- **File:** `/home/ubuntu/clawd/scripts/hiring_intel_module.py`
- **Failure mode:** Every company is classified as "AI Crypto" vertical regardless of actual business.
- **Evidence:** CYCLOPS (stablecoin payments), BLUPRYNT (compliance), UTEXO (Bitcoin settlement) all classified as "AI Crypto"
- **User impact:** Inaccurate vertical tags, generic hiring predictions.

### [LOW] F-08: "Success Billionaire" identified as CYCLOPS CEO

- **File:** `/home/ubuntu/clawd/scripts/founder-intel-pipeline.py` (website scraper)
- **Failure mode:** Website scraper extracted "Success Billionaire" as CEO name (likely from Fortune.com article page chrome, not company site).
- **Evidence:** CYCLOPS standalone outreach page: `"Success Billionaire | Confidence: PROBABLE | CEO | Source: website_scrape, website_scrape, website_scrape"`
- **User impact:** Garbage POC data in report.

---

## 2. FIXES APPLIED

### Notion Page Cleanups (Live — Applied Mar 08, 2026)

#### 2a. Archived 5 redundant standalone OUTREACH TARGET pages

| Company | Archived Page ID | Canonical Page ID |
|---------|-----------------|-------------------|
| CROSSOVER MARKETS | `31cf30f9-bdff-813b` | `31df30f9-bdff-81a7` |
| USD.AI | `31cf30f9-bdff-811a` | `31cf30f9-bdff-81cc` |
| CYCLOPS | `31cf30f9-bdff-811e` | `31bf30f9-bdff-815d` |
| UTEXO | `31cf30f9-bdff-816d` | `31df30f9-bdff-81d6` |
| BLUPRYNT | `31cf30f9-bdff-81bc` | `314f30f9-bdff-8143` |

- Method: Set ENTRY title to include `[ARCHIVED — DUPLICATE OF CANONICAL]`, QA STATUS=SKIP, QA ISSUES=archive reason.
- No pages were deleted (constraint: never delete).

#### 2b. Cleaned canonical page content

| Page | Changes |
|------|---------|
| **USD.AI** (`31cf30f9-bdff-81cc`) | Removed "Archived duplicate:" bullet references. Fixed investor list (removed "David Choi (CEO/co-founder", "Permian Labs)", "Conor Moore", "Individual investors" parsing artifacts). Replaced dedup callout with clean audit callout. |
| **CYCLOPS** (`31bf30f9-bdff-815d`) | Replaced corrupt investor list (removed "Alex Wilson (co-founder", "Cyclops)", "David Johnson (co-founder", "Pat Duffy (co-founder" artifacts). Added Shift4 Payments from duplicate source. Replaced "Dedup Incremental Update" with clean audit callout. |
| **BLUPRYNT** (`314f30f9-bdff-8143`) | Removed "Dedup cleanup" callout referencing archived duplicate. Replaced with clean audit callout. |
| **BASED** (`310f30f9-bdff-8146`) | Removed "Dedup cleanup" callout referencing archived duplicate. Replaced with clean audit callout. |
| **CROSSOVER MARKETS** (`31df30f9-bdff-81a7`) | No content changes needed — page was already clean with proper markers. |
| **UTEXO** (`31df30f9-bdff-81d6`) | No content changes needed — page was already clean. |

#### 2c. Updated audit metadata on all 6 canonical pages

- Set `LAST AUDITED AT` = `2026-03-08T21:30:00.000Z`
- Set `QA ISSUES` = specific audit finding for each page

### Script Patches (Created — Pending Deployment)

Three patch files created in `/home/user/claudecode/patches/`:

| Patch | Target Script | Key Changes |
|-------|--------------|-------------|
| `founder-intel-pipeline-dedup-fix.py` | `founder-intel-pipeline.py` | Disable standalone OUTREACH TARGET page creation; make canonical append the only write path; add REPORT KEY to any fallback pages; add dedup check before page creation |
| `funding-intel-brief-hardening.py` | `funding-intel-brief.py` | Sanitize investor lists (filter person-name artifacts); move "Archived duplicate" notes to QA ISSUES property; normalize REPORT KEY to v3 format; add amount-tolerance matching |
| `hiring_intel_module-idempotency.py` | `hiring_intel_module.py` | Suppress DuckDuckGo transport errors from hiring signals; handle Grok API timeouts gracefully with fallback text |

---

## 3. VERIFICATION

| Check | Result | Notes |
|-------|--------|-------|
| CROSSOVER MARKETS — exactly 1 outreach section | **PASSED** | `[[OUTREACH_INTEL_AUTO_START]]` and `[[OUTREACH_INTEL_AUTO_END]]` each appear exactly once |
| CROSSOVER MARKETS — exactly 1 hiring section | **PASSED** | `[[HIRING_INTEL_AUTO_START]]` and `[[HIRING_INTEL_AUTO_END]]` each appear exactly once |
| CROSSOVER MARKETS — no archived-dup refs | **PASSED** | No "Archived duplicate:" text in body |
| CROSSOVER MARKETS — standalone outreach archived | **PASSED** | `31cf30f9-bdff-813b` title updated with [ARCHIVED], QA STATUS=SKIP |
| USD.AI — investor list clean | **PASSED** | 4 clean investor entries (Arbitrum, Coinbase Ventures, Dragonfly, Framework Ventures) |
| USD.AI — no archived-dup refs in body | **PASSED** | Replaced with clean audit callout |
| CYCLOPS — investor list clean | **PASSED** | 6 clean entries (Castle Island, F-Prime, Mastercard, Stripe, Visa, Shift4 Payments) |
| BLUPRYNT — no archived-dup refs in body | **PASSED** | Replaced with clean audit callout |
| BASED — no archived-dup refs in body | **PASSED** | Replaced with clean audit callout |
| UTEXO — canonical page clean | **PASSED** | No issues found |
| All 6 canonicals have LAST AUDITED AT set | **PASSED** | All set to 2026-03-08T21:30:00Z |
| Script patches created | **PASSED** | 3 patch files in repo |
| Script patches deployed to Tony | **NOT RUN** | SSH unavailable from audit environment |
| Dry-run pipeline validation | **NOT RUN** | Requires SSH to Tony |

### Residual Risk

1. **Script patches not yet deployed** — The root cause (standalone page creation in founder-intel-pipeline.py) is NOT yet fixed in production. New pipeline runs will create new standalone outreach pages. Patches must be deployed to Tony ASAP.
2. **~10+ additional standalone OUTREACH TARGET pages exist** for non-target companies (JPYC, TAPIOCA DAO, EVERYTHING, EUCLID, HAPPYLAND, AKAVE, QFEX, HELIOS, CAMBRIA, OMNIPACT, ARQ, IZUMI, IDOS, LEGEND). These should be archived in a follow-up sweep.
3. **OKX has 3 active pages** with conflicting amounts ($0, $200M, $25B) — needs manual triage to determine which is canonical.
4. **ARQ has 2 canonical pages** (`319f30f9` legacy + `31cf30f9bdff81d8` new) — the legacy page should be archived.

---

## 4. FINAL STATE SUMMARY

### Duplicate Cluster Counts

| Metric | Before | After |
|--------|--------|-------|
| Target companies with redundant standalone outreach pages | 5 | 0 |
| Target canonical pages with archived-dup body references | 4 | 0 |
| Target canonical pages with corrupt investor lists | 2 | 0 |
| Total active OUTREACH TARGET pages (all companies) | ~15 | ~10 (5 archived) |

### Cleaned Canonical Page IDs

1. `31df30f9-bdff-81a7-8c49-e99bb40c5704` — CROSSOVER MARKETS (verified clean)
2. `31cf30f9-bdff-81cc-9419-e358c321c5a6` — USD.AI (investor list + body cleaned)
3. `31bf30f9-bdff-815d-8443-fcd6014b6e67` — CYCLOPS (investor list + body cleaned)
4. `31df30f9-bdff-81d6-a337-e6aaa374dbb1` — UTEXO (verified clean)
5. `314f30f9-bdff-8143-ad51-fb94866a7836` — BLUPRYNT (body cleaned)
6. `310f30f9-bdff-8146-ac77-c59ec0ed0fe4` — BASED (body cleaned)

### Remaining Manual Cleanups

1. **Deploy script patches to Tony** — Critical. See `patches/` directory.
2. **Archive remaining ~10 standalone OUTREACH TARGET pages** for non-target companies.
3. **Triage OKX cluster** — 3 pages with $0, $200M, $25B amounts need manual review.
4. **Triage ARQ cluster** — 2 canonical pages need merge.
5. **Re-run pipeline dry-run** after script deployment to verify no new duplicates created.
