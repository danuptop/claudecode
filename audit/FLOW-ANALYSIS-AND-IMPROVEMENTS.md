# REPORT BASE — COMPLETE FLOW ANALYSIS & IMPROVEMENT OPPORTUNITIES

**Date:** March 9, 2026
**Scope:** All scripts, modules, and flows that generate or modify Report Base records

---

## 1. PIPELINE TOPOLOGY (CURRENT STATE)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    DATA SOURCES (External)                          │
│  crypto-fundraising.info, CoinDesk, Decrypt, The Block,            │
│  Fortune, Phemex, CoinTelegraph, WatcherGuru                       │
└──────┬──────────────────────────────────────────┬───────────────────┘
       │                                          │
       ▼                                          ▼
┌──────────────────────┐              ┌──────────────────────────┐
│ funding-intel-brief  │              │ crypto-fundraising-      │
│ .py                  │              │ monitor (JS, legacy)     │
│ [ON TONY]            │              │ watcher-guru-notion-     │
│                      │              │ writer.js [ON TONY]      │
│ • Detects deals      │              │                          │
│ • Creates canonical  │              │ • Monitors WatcherGuru   │
│   FUNDRAISING INTEL  │              │ • Creates SIGNAL PACK    │
│   pages              │              │   pages                  │
│ • Sets REPORT KEY,   │              │                          │
│   properties         │              │                          │
└──────┬───────────────┘              └──────────────────────────┘
       │
       ▼
┌──────────────────────────┐
│ outreach-pipeline-       │
│ trigger.py [ON TONY]     │
│                          │
│ • Triggers enrichment    │
│   for new canonical      │
│   pages                  │
└──────┬───────────────────┘
       │
       ▼
┌──────────────────────────┐          ┌──────────────────────────┐
│ founder-intel-pipeline   │  imports │ hiring_intel_module       │
│ .py [ON TONY]            │◄─────────│ .py [ON TONY]            │
│                          │          │                          │
│ • Reads canonical pages  │          │ • DuckDuckGo job search  │
│ • Scrapes company domain │          │ • Grok API benchmarks    │
│ • Generates outreach     │          │ • Static hiring template │
│ • Appends via markers    │          │                          │
│ • [BUG] Also creates     │          │                          │
│   standalone pages       │          │                          │
└──────────────────────────┘          └──────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    SUPPORT MODULES (IN REPO, NOT YET DEPLOYED)      │
│                                                                     │
│  scripts/canonical_template.py  — Page structure + report key gen   │
│  scripts/qa_validator.py        — Post-write QA gate                │
│  scripts/content_sanitizer.py   — Error text stripper               │
│  scripts/resilient_api.py       — Retry + circuit breaker wrapper   │
│                                                                     │
│  patches/founder-intel-pipeline-dedup-fix.py   — Patch description  │
│  patches/funding-intel-brief-hardening.py      — Patch description  │
│  patches/hiring_intel_module-idempotency.py    — Patch description  │
└─────────────────────────────────────────────────────────────────────┘
```

**Key observation:** There are TWO codebases:
1. **Production scripts on Tony** (`/home/ubuntu/clawd/scripts/`) — the actual running code. We cannot see or edit these directly.
2. **This repo** (`/home/user/claudecode/`) — contains support modules, patches, and audit artifacts. None of this code runs in production yet.

---

## 2. FILE-BY-FILE ANALYSIS

### 2.1 `scripts/canonical_template.py` (389 lines)

**Purpose:** Defines the page structure contract for FUNDRAISING INTEL pages.

**What it provides:**
- `TEMPLATE_SECTIONS` — ordered list of expected sections with marker definitions
- `slugify()` — company name to URL-safe slug
- `normalize_company_slug()` — alias resolution + suffix stripping (added Mar 09)
- `COMPANY_ALIASES` — known entity mappings (4 entries)
- `generate_report_key()` — v3 format: `fundraising-intel:v3:{slug}:{amount}`
- `parse_amount()` — dollar string to integer
- `amounts_match()` — ±5% tolerance comparison
- `build_canonical_blocks()` — creates Notion block skeleton with markers
- `build_page_properties()` — creates property dict for `pages.create()`
- `validate_page_structure()` — checks page content against template

**Improvement opportunities:**

| # | Category | Issue | Recommendation |
|---|----------|-------|----------------|
| CT-1 | **Entity resolution** | `COMPANY_ALIASES` is a manual table with only 4 entries. Every new duplicate requires a human to add an alias. | Add fuzzy matching: query Notion for existing pages whose COMPANY property contains the new company name as a substring. E.g., search for "USD" would find "USD.AI". Also consider Levenshtein distance on slugs. |
| CT-2 | **Suffix stripping is lossy** | `normalize_company_slug("crema-finance")` → `"crema"` (strips `-finance`). But "Crema Finance" IS the company name — stripping changes the REPORT KEY. | Only strip suffixes when generating dedup lookup candidates, not when creating the primary REPORT KEY. Use the stripped form as a secondary search term. |
| CT-3 | **No `create_canonical_page()` function** | Docstring advertises `create_canonical_page()` but the function doesn't exist. `build_canonical_blocks()` + `build_page_properties()` require the caller to assemble the Notion API call. | Add the end-to-end `create_canonical_page()` function that handles the full create-or-update flow including dedup check. This becomes the single entry point for page creation. |
| CT-4 | **No round type normalization** | Round types come in as "FUNDING", "PUBLIC SALE", "SEED", "SERIES B", "STRATEGIC", "M&A" — but also "UNKNOWN". No validation or normalization. | Add `VALID_ROUND_TYPES` set. Map "FUNDING" → "UNKNOWN" (it's a non-specific label). Reject M&A events from the fundraising pipeline. |
| CT-5 | **No event type filtering** | The template doesn't distinguish fundraising events from M&A, token sales, or public offerings. KUTT M&A and USD.AI PUBLIC SALE are fundamentally different from CROSSOVER MARKETS SERIES B. | Add `is_actionable_fundraising()` function that returns False for M&A, public sales, and token generation events. These shouldn't generate outreach pages. |
| CT-6 | **`_format_amount` edge cases** | `_format_amount(0)` → `"$0"` which propagates into titles. Should signal an error state, not produce a displayable amount. | Return `"Undisclosed"` for amount=0 instead of `"$0"`, or raise ValueError. |
| CT-7 | **Missing `DATE` property** | `build_page_properties()` doesn't set the `DATE` property. The pipeline must set it separately, but this splits responsibility. | Include `DATE` in the property builder for consistency. |

### 2.2 `scripts/qa_validator.py` (517 lines)

**Purpose:** Post-write QA gate. Validates pages and sets QA STATUS.

**What it checks:**
- Properties: REPORT KEY, SOURCE SKILL, RUN ID, COMPANY (empty checks)
- Content: Error regex patterns, marker pairs, stacking, content length
- Quality: POC identified, investors present, fabricated claims, API failures
- Special: $0 amounts, UNKNOWN round type

**Improvement opportunities:**

| # | Category | Issue | Recommendation |
|---|----------|-------|----------------|
| QA-1 | **Not integrated with pipeline** | The validator exists as a standalone script. It's not called by the production pipeline. Pages get QA STATUS=PASS by default from `build_page_properties()`, and the validator never runs. | The validator MUST be called as a `post_write_hook()` at the end of every pipeline script. This is the single most impactful change — without it, all other QA logic is dead code. |
| QA-2 | **`validate_recent()` uses wrong filter** | Line 434: filters on `"created_time"` but the property name is `"DATE"`. The Notion API filter type `created_time` is a system property, but `DATE` is a user-defined date property. These don't match. | Use `"date": {"after": ...}` filter on the `DATE` property, or use `"timestamp": "last_edited_time"` filter. |
| QA-3 | **No duplicate detection** | The validator checks page quality but doesn't check if the page is a duplicate of an existing page. This is the #1 problem. | Add a dedup check: query for other active pages with matching COMPANY or overlapping REPORT KEY prefix. Flag as FAIL if a duplicate canonical exists. |
| QA-4 | **No event type validation** | Doesn't flag M&A events, public sales, or token generation events that shouldn't be in the FUNDRAISING INTEL pipeline. | Add check: if page title contains "M&A" or round amount is 0, flag appropriately. |
| QA-5 | **Error pattern list incomplete** | Missing patterns: `"Clay: not attempted"`, `"MANUAL RESEARCH NEEDED"`, `"Sources pending"`, `"Outreach enrichment pending"`. These are all failure states that currently pass QA. | Expand `ERROR_PATTERNS` or add a separate `INCOMPLETE_PATTERNS` list for content that signals unfilled placeholders. |
| QA-6 | **Fabrication detection too narrow** | Only checks for "solid VC backing" — but the outreach template has other fabricated phrases. E.g., "huge milestone", "dominate the [X] market", "crush it in crypto/fintech". | Generalize: check if outreach angle contains investor-specific language when investors are "Not listed". Or: compare outreach angle length to actual data points — a 200-char angle with 0 data points is always slop. |
| QA-7 | **`extract_page_text` creates new client per block** | Line 110: `get_notion_client()` is called for EVERY child block recursion. This creates a new Client instance each time. | Pass the client as a parameter, or cache it module-level. |
| QA-8 | **No severity scoring** | Binary PASS/WARN/FAIL doesn't capture the difference between "missing 1 investor" (minor) and "wrong company, $0 amount, fabricated outreach" (useless). | Add a numeric quality score (0-100) alongside the status. Pages below 30 should be auto-archived or at least hidden from the team view. |

### 2.3 `scripts/content_sanitizer.py` (404 lines)

**Purpose:** Pre-write filter that strips error artifacts from Notion blocks.

**What it does:**
- Regex-based line removal for DuckDuckGo/Grok/Python errors
- Inline replacement of error text with clean fallback messages
- `sanitize_investor_list()` — filters person-name artifacts
- `clean_mcp_unavailable_section()` — replaces broken Icebreaker sections
- CLI mode: clean a specific page by ID

**Improvement opportunities:**

| # | Category | Issue | Recommendation |
|---|----------|-------|----------------|
| CS-1 | **Reactive, not preventive** | Sanitizer runs AFTER errors are written. Better to prevent errors from entering the content in the first place. | Move sanitization to the point of generation (in hiring_intel_module and founder-intel-pipeline), not post-write. The sanitizer should be a safety net, not the primary defense. |
| CS-2 | **Lossy formatting** | Line 224-231: When cleaning a block, it rebuilds `rich_text` as a single plain text span, losing all formatting (bold, italic, colors, links). | Preserve the original rich_text structure and only modify the affected text spans. |
| CS-3 | **`_is_error_only` patterns don't match multiline** | `re.match` only checks the start of the string. A block with "Some text\nSearch error: HTTPSConnectionPool..." would NOT be caught as error-only. | Use `re.search` instead of `re.match`, or split on newlines and check each line. |
| CS-4 | **`clean_page` references undefined `ERROR_RE`** | Line 331: `ERROR_RE.findall(full_text)` but `ERROR_RE` is defined in `qa_validator.py`, not `content_sanitizer.py`. This will crash at runtime. | Either import `ERROR_RE` from `qa_validator`, or define a local equivalent. |
| CS-5 | **No investor name validation** | `sanitize_investor_list()` filters out person-name artifacts but doesn't validate that remaining entries are actual company/fund names. "Individual investors" passes through but isn't actionable. | Add a positive validation step: check against a known investor database, or flag entries that don't look like company names (no capitals, too short, generic phrases). |

### 2.4 `scripts/resilient_api.py` (271 lines)

**Purpose:** Retry + circuit breaker wrapper for external API calls.

**What it provides:**
- `CircuitBreaker` class with closed/open/half-open states
- Pre-configured breakers for DuckDuckGo, Grok, Clay, Icebreaker
- `resilient_call()` — retry with exponential backoff + circuit breaking
- `search_duckduckgo()` — wrapped DuckDuckGo HTML search
- `call_grok()` — wrapped Grok API call

**Improvement opportunities:**

| # | Category | Issue | Recommendation |
|---|----------|-------|----------------|
| RA-1 | **Circuit breakers are per-process** | Breaker state lives in memory. If the pipeline script restarts (which it does on every run), breaker state is lost. A failing API gets hammered from scratch every time. | Persist breaker state to a file or Redis. At minimum, write `last_failure_time` + `failure_count` to `/tmp/circuit_breaker_{name}.json`. |
| RA-2 | **DuckDuckGo is blocked** | Evidence from 17+ pages shows DuckDuckGo ALWAYS times out. The breaker resets after 600s but the API never works from Tony's IP. | Remove DuckDuckGo as a hiring signal source entirely. Replace with: (a) direct job board scrape (Lever, Greenhouse, Ashby APIs), (b) LinkedIn job search API, or (c) skip hiring signals when no job board is found. |
| RA-3 | **Grok API timeouts are the norm** | Competitor benchmarks show "API timeout" on every Mar 09 page. 30s timeout may be too short for Grok, or the API key may be rate-limited. | Increase timeout to 60s. Add API key rotation. Or: cache Grok responses by company vertical — most benchmarks are generic enough to reuse. |
| RA-4 | **No metrics/observability** | Retry counts, circuit breaker trips, and fallback rates are only logged. No aggregation or alerting. | Add a simple counter file: append `{timestamp},{service},{success|failure}` to a CSV. Surface in a weekly dashboard. |
| RA-5 | **`call_grok` hardcodes model** | Line 258: `"model": "grok-2-latest"` is hardcoded. | Make configurable via env var `GROK_MODEL`. |

### 2.5 `patches/*.py` (3 files, ~100-110 lines each)

**Purpose:** Prose descriptions of changes to make to production scripts on Tony.

**Critical problem:** These are NOT patches. They are comments describing what to search for and what to change. They require a human to:
1. SSH into Tony
2. Open the production script
3. Find the described patterns
4. Manually edit the code

**Improvement opportunity:**

| # | Category | Issue | Recommendation |
|---|----------|-------|----------------|
| PA-1 | **Not deployable** | Patch files can't be applied programmatically. | Create actual production-ready replacement scripts (or at minimum, proper `diff`/`patch` files). The repo should contain the full modified scripts, not descriptions of changes. |
| PA-2 | **No CI/CD** | All deployment is manual SSH + copy. | Set up a deploy script: `./deploy.sh tony` that SCP's the scripts to Tony, runs syntax validation, and creates backups. Even a simple bash script would break the audit loop. |
| PA-3 | **No version pinning** | Production scripts on Tony have no version tracking. We don't know what version is running. | Add a `__version__` string to each script. Log it on startup. Store it as a Notion DB property on each page (e.g., `PIPELINE VERSION`). |

### 2.6 `CODEX-DEPLOYMENT-PROMPT.md` (525 lines)

**Purpose:** Self-contained deployment guide for an AI agent (Codex) to apply patches to Tony.

**This is actually a good idea** — it's the closest thing to an automated deployment. But it has gaps:

| # | Category | Issue | Recommendation |
|---|----------|-------|----------------|
| CD-1 | **Stale branch reference** | References branch `claude/audit-search-report-duplication-TNk3J` — not the current branch. | Update to current branch. Or better: the deployment prompt should reference specific commit hashes, not branches. |
| CD-2 | **No rollback plan** | Backs up scripts but doesn't describe how to rollback if deployment fails. | Add: `cp *.bak.20260308 *.py` as explicit rollback command. |
| CD-3 | **Doesn't include new Mar 09 fixes** | Only covers F-01 through F-11. Doesn't include F-12 through F-17 (QA validator content checks, entity normalization). | Needs a refresh with the full fix set. |

---

## 3. CROSS-CUTTING IMPROVEMENT OPPORTUNITIES

### 3.1 The Enrichment Pipeline is Fake Intelligence

This is the fundamental quality problem. The enrichment pipeline (`founder-intel-pipeline.py` + `hiring_intel_module.py`) consistently fails to produce real intelligence:

| Data Point | Expected Source | Actual Result | Failure Rate |
|------------|----------------|---------------|--------------|
| Company description | Domain scrape | Wrong company or "N/A" | ~70% |
| Primary POC | Domain scrape + LinkedIn | "NOT IDENTIFIED" | ~90% |
| Contact details | Clay enrichment | "not attempted" | 100% |
| Investors | Source article parsing | "Not listed" or corrupted | ~60% |
| Hiring signals | DuckDuckGo search | Transport error or timeout | ~100% |
| Competitor benchmark | Grok API | "API timeout" | ~80% |
| Outreach angle | Template fill | Generic slop with fabricated claims | ~100% |
| Hiring predictions | Static template | Same 6 roles regardless of company | ~100% |
| Warm intro map | Icebreaker MCP | "mcp_unavailable" | 100% |

**The enrichment step adds negative value.** A bare skeleton with just deal summary + investors is MORE useful than a skeleton with fabricated outreach angles, wrong POCs, and templated hiring predictions — because the filler content creates a false sense of completeness.

**Recommendation: Implement a minimum-data gate**

```python
def should_enrich(page_data: dict) -> bool:
    """Only run enrichment if we have enough seed data to produce real output."""
    has_investors = page_data.get("investors") and page_data["investors"] != ["Unknown"]
    has_domain = page_data.get("domain") and not page_data["domain"].endswith(".info")
    has_amount = page_data.get("amount", 0) > 0
    has_round_type = page_data.get("round_type") not in ("UNKNOWN", "FUNDING", "M&A")

    # Require at least 3 of 4 seed data points before enriching
    score = sum([has_investors, has_domain, has_amount, has_round_type])
    return score >= 3
```

If the gate fails, leave the canonical page as a bare skeleton with QA STATUS=WARN and a note: "Insufficient seed data for enrichment — manual research required."

### 3.2 The Outreach Angle Template Needs Guardrails

The current outreach angle is always:
```
"Congrats on the $XM raise with solid VC backing—[adjective] for [COMPANY].
Up Top Search specializes in crypto-native talent... Let's chat."
```

Problems:
- Claims "solid VC backing" even when no investors are listed
- Says "crypto-native talent" for non-crypto companies (NOVIG is sports betting)
- Generic to the point of being useless as outreach
- Same template for $1M seed and $261M mega-round

**Recommendation: Conditional outreach templates**

```python
OUTREACH_TEMPLATES = {
    "high_data": {  # Has investors, POC, round type
        "min_data_points": 4,
        "template": "..."  # Personalized with actual investor names, POC name, etc.
    },
    "medium_data": {  # Has some data
        "min_data_points": 2,
        "template": "..."  # Generic but doesn't fabricate
    },
    "no_data": {  # Nothing found
        "min_data_points": 0,
        "template": None,  # DON'T GENERATE AN OUTREACH ANGLE
    }
}
```

### 3.3 The Vertical Classifier is a Constant

Every company is classified as "Institutional Crypto" regardless of actual business. The hiring predictions cascade from this wrong vertical into nonsensical role suggestions.

**Recommendation:** Use the domain scrape description (when available) to classify:

```python
VERTICAL_KEYWORDS = {
    "DeFi": ["defi", "lending", "borrowing", "swap", "liquidity", "yield"],
    "Infrastructure": ["layer", "bridge", "oracle", "interoperability", "rollup"],
    "Payments": ["payment", "settlement", "stablecoin", "remittance"],
    "Gaming": ["game", "metaverse", "nft", "play-to-earn"],
    "Security": ["audit", "compliance", "identity", "kyc"],
    "Non-Crypto": ["prediction market", "betting", "sports", "power", "hardware"],
}

def classify_vertical(description: str, company_name: str) -> str:
    desc_lower = (description or "").lower()
    for vertical, keywords in VERTICAL_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            return vertical
    return "Unclassified"  # NOT "Institutional Crypto"
```

### 3.4 The Dedup System Needs a Multi-Signal Approach

Current dedup: exact match on REPORT KEY (`{slug}:{amount}`).

This misses:
- Company name variations (USD.AI vs USD AI PERMIAN LABS)
- Amount variations ($19.4M vs $13.4M)
- Aggregated amounts ($261.3M cumulative for LayerZero)
- Cross-format matches (v3 vs legacy keys)

**Recommendation: Layered dedup with scoring**

```python
def find_potential_duplicates(company: str, amount: int) -> list[dict]:
    """Multi-signal duplicate detection."""
    candidates = []

    # Layer 1: Exact REPORT KEY match
    v3_key = generate_report_key(company, amount)
    exact = query_by_report_key(v3_key)
    for page in exact:
        candidates.append({"page": page, "confidence": 1.0, "match": "exact_key"})

    # Layer 2: Legacy key prefix match
    slug = normalize_company_slug(company)
    legacy = query_by_report_key_prefix(f"fundraising-intel:{slug}:")
    for page in legacy:
        candidates.append({"page": page, "confidence": 0.9, "match": "legacy_key"})

    # Layer 3: COMPANY property substring match
    company_matches = query_by_company_contains(slug.replace("-", " "))
    for page in company_matches:
        page_amount = page["properties"].get("ROUND AMOUNT", {}).get("number", 0)
        if amounts_match(amount, page_amount, tolerance=0.35):
            candidates.append({"page": page, "confidence": 0.7, "match": "fuzzy_company+amount"})

    # Layer 4: Amount-only match (same amount ± 5%, same week)
    amount_matches = query_by_amount_range(amount, tolerance=0.05, days=7)
    for page in amount_matches:
        candidates.append({"page": page, "confidence": 0.5, "match": "amount_window"})

    return sorted(candidates, key=lambda c: -c["confidence"])
```

### 3.5 Pipeline Should NOT Create Pages for Non-Actionable Events

Events that should be filtered BEFORE page creation:

| Event Type | Example | Why Not Actionable |
|------------|---------|-------------------|
| M&A | KUTT acquired by Bitcoin Depot | No hiring — company was acquired |
| Public token sale | USD.AI $19.4M PUBLIC SALE | No VCs, no exec hiring — it's retail investors |
| $0 / Undisclosed | PROBABLE $0 M&A | No amount means no signal |
| Aggregated funding | LayerZero $261.3M cumulative | Not a new round — misleading |
| Non-crypto company | NOVIG (sports betting) | Outside Up Top's vertical |

**Recommendation: Pre-creation filter**

```python
def should_create_page(event: dict) -> tuple[bool, str]:
    """Gate function — returns (create, reason)."""
    if event.get("round_type") == "M&A":
        return False, "M&A events don't trigger hiring"
    if event.get("amount", 0) == 0:
        return False, "$0 amount — parse failure or undisclosed"
    if event.get("round_type") == "PUBLIC SALE":
        return False, "Public sales are retail — no exec hiring signal"
    if event.get("amount", 0) > 500_000_000:
        return False, f"${event['amount']/1e6:.0f}M likely aggregated — verify manually"
    return True, "OK"
```

### 3.6 The Support Modules Need Integration Points

Four support modules exist but none are wired into the production pipeline:

| Module | Purpose | Integration Status |
|--------|---------|-------------------|
| `canonical_template.py` | Page structure | NOT INTEGRATED — `funding-intel-brief.py` on Tony doesn't import it |
| `qa_validator.py` | Post-write QA | NOT INTEGRATED — never called by any pipeline script |
| `content_sanitizer.py` | Error stripping | NOT INTEGRATED — not imported by any pipeline script |
| `resilient_api.py` | Retry/circuit breaker | NOT INTEGRATED — pipeline scripts use raw `requests` |

**Recommendation: Create a single integration wrapper**

```python
# pipeline_harness.py — wraps all pipeline operations

from canonical_template import generate_report_key, build_canonical_blocks, normalize_company_slug
from content_sanitizer import sanitize_blocks, sanitize_investor_list
from qa_validator import post_write_hook
from resilient_api import search_duckduckgo, call_grok

def create_or_update_report(event: dict) -> str:
    """Single entry point for creating/updating a report page."""

    # 1. Pre-creation filter
    ok, reason = should_create_page(event)
    if not ok:
        logger.info(f"Skipping {event['company']}: {reason}")
        return None

    # 2. Entity normalization
    slug = normalize_company_slug(event["company"])

    # 3. Dedup check
    dupes = find_potential_duplicates(event["company"], event["amount"])
    if dupes and dupes[0]["confidence"] >= 0.7:
        # Update existing page instead of creating
        return update_existing(dupes[0]["page"], event)

    # 4. Create canonical page
    investors = sanitize_investor_list(event.get("investors", []))
    blocks = sanitize_blocks(build_canonical_blocks(...))
    page_id = notion.pages.create(...)

    # 5. Post-write QA
    status = post_write_hook(page_id)
    if status == "FAIL":
        logger.error(f"Page {page_id} failed QA after creation")

    return page_id
```

### 3.7 Deployment Must Be Automated

The audit→patch→can't-deploy→re-audit loop has cycled 3 times. Manual SSH deployment is the bottleneck.

**Options (simplest first):**

1. **Deploy script in repo:**
   ```bash
   #!/bin/bash
   # deploy.sh — push scripts to Tony
   scp scripts/*.py tony:/home/ubuntu/clawd/scripts/
   ssh tony "cd /home/ubuntu/clawd/scripts && python3 -c 'import ast; [ast.parse(open(f).read()) for f in [\"funding-intel-brief.py\", \"founder-intel-pipeline.py\", \"hiring_intel_module.py\"]]' && echo 'ALL OK'"
   ```

2. **Git pull on Tony:**
   Tony clones this repo and pulls on a cron schedule. Production scripts import from the repo modules.

3. **GitHub Actions deployment:**
   Push to `main` → GHA SSH's into Tony and deploys. Requires SSH key in GitHub secrets.

### 3.8 Observability Is Missing

There is no way to know:
- How many pages were created in the last run
- How many failed QA
- How many are duplicates
- Which APIs are failing
- What the overall pipeline success rate is

**Recommendation: Add a run summary page**

After each pipeline run, create or update a `PIPELINE RUN SUMMARY` page in Report Base:

```
RUN ID: funding-intel-20260309T034743
Pages created: 6
Pages updated: 0
Pages skipped (dedup): 2
Pages skipped (filter): 3
QA results: 2 PASS, 3 WARN, 1 FAIL
API health: DuckDuckGo=DOWN, Grok=TIMEOUT, Clay=NOT_CONFIGURED
Duration: 4m 32s
```

---

## 4. PRIORITY MATRIX

| Priority | Item | Impact | Effort | Dependency |
|----------|------|--------|--------|------------|
| **P0** | Deploy existing patches to Tony | Stops new duplicates | Low | SSH access |
| **P0** | Wire `post_write_hook()` into pipeline | Makes QA visible | Low | Tony deploy |
| **P1** | Pre-creation event filter | Stops M&A/$0/public sale noise | Low | Tony deploy |
| **P1** | Minimum-data gate for enrichment | Stops template slop | Medium | Tony deploy |
| **P2** | Multi-signal dedup | Catches name/amount variations | Medium | `canonical_template.py` |
| **P2** | Conditional outreach templates | Stops fabricated claims | Medium | Tony deploy |
| **P2** | Vertical classifier from description | Stops "Institutional Crypto" default | Low | Tony deploy |
| **P3** | Replace DuckDuckGo with job board APIs | Gets real hiring signals | High | API keys |
| **P3** | Deploy script / CI/CD | Breaks the audit loop | Medium | SSH key setup |
| **P3** | Run summary observability | Pipeline visibility | Medium | Tony deploy |
| **P4** | Grok response caching by vertical | Reduces API costs/timeouts | Medium | Redis/file cache |
| **P4** | Investor name validation | Cleaner investor lists | Medium | Reference DB |
| **P4** | Known domains table | Stops wrong domain scrapes | Low | Manual curation |

---

## 5. SUGGESTED IMPLEMENTATION ORDER

**Phase 1 — Stop the bleeding (this week)**
1. Deploy patches + 4 support modules to Tony
2. Wire `post_write_hook()` into pipeline scripts
3. Add pre-creation filter for M&A/$0/public sales
4. Add minimum-data gate before enrichment

**Phase 2 — Improve quality (next week)**
5. Multi-signal dedup with fuzzy company matching
6. Conditional outreach templates (no-data → no outreach angle)
7. Vertical classifier from domain description
8. Deploy script for automated future deploys

**Phase 3 — Real intelligence (2-3 weeks)**
9. Replace DuckDuckGo with Lever/Greenhouse/Ashby job board APIs
10. Build known-domains table for top 200 crypto companies
11. Grok response caching
12. Pipeline run summary observability
