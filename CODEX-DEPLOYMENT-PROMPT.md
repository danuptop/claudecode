# CODEX DEPLOYMENT PROMPT — Report Base Dedup Audit Fixes

**Date:** March 8, 2026
**Context:** A forensic audit of the Up Top Search "Report Base" pipeline identified 10 bugs causing duplicate Notion pages, stacked content sections, corrupt investor lists, and garbage hiring signals. Notion-side cleanups (22 page archives, inline data fixes) have already been completed. **Your job is to apply the code-level fixes to the 3 production scripts on Tony.**

---

## ENVIRONMENT

- **Server:** Tony (SSH alias `tony`, host at `/home/ubuntu/clawd/scripts/`)
- **Scripts to modify:**
  1. `/home/ubuntu/clawd/scripts/founder-intel-pipeline.py`
  2. `/home/ubuntu/clawd/scripts/funding-intel-brief.py`
  3. `/home/ubuntu/clawd/scripts/hiring_intel_module.py`
- **Notion Database:** Report Base (`902d47be-68c0-4da8-832a-a52272fc7b39`), Data Source `598bb1d8-26cb-4e2a-8b4f-8b80e1be7116`
- **Repo with audit artifacts:** `danuptop/claudecode` branch `claude/audit-search-report-duplication-TNk3J`
  - `audit/AUDIT-REPORT-2026-03-08.md` — full audit report with findings F-01 through F-10
  - `patches/founder-intel-pipeline-dedup-fix.py` — patch spec for founder-intel-pipeline
  - `patches/funding-intel-brief-hardening.py` — patch spec for funding-intel-brief
  - `patches/hiring_intel_module-idempotency.py` — patch spec for hiring_intel_module
  - `scripts/qa_validator.py` — **NEW** post-write QA validator (deploy to Tony)
  - `scripts/content_sanitizer.py` — **NEW** content sanitizer + investor list cleaner (deploy to Tony)
  - `scripts/resilient_api.py` — **NEW** retry/circuit-breaker wrapper for external APIs (deploy to Tony)
  - `scripts/canonical_template.py` — **NEW** canonical page template + structure validator (deploy to Tony)
- **Schema changes already applied to Notion:**
  - Added `COMPANY` (rich_text) property — backfilled on ~30 pages
  - Added `ROUND AMOUNT` (number, dollar format) property — backfilled on ~30 pages
  - Signal Pack pages backfilled with `signal-pack:v1:{date}` REPORT KEYs
  - OKX canonical page upgraded to v3 REPORT KEY

---

## WHAT WAS ALREADY DONE (Notion-side — DO NOT REDO)

These changes were made directly to Notion pages. They are complete. Do not repeat them.

1. **Archived 18 standalone OUTREACH TARGET pages** — All had TYPE=OUTREACH TARGET, empty REPORT KEY, SOURCE SKILL=founder-intel-pipeline. Titles updated to include `[ARCHIVED — DUPLICATE OF CANONICAL]`, QA STATUS set to SKIP. Full manifest in audit report Section 4.

2. **Archived 2 OKX duplicate pages** — $25B bad-parse page and $0 placeholder page both archived with QA STATUS=FAIL. $200M page (`31cf30f9-bdff-817e`) confirmed as canonical.

3. **Cleaned 4 canonical page bodies** — Removed "Archived duplicate:" / "Dedup cleanup:" callout blocks from USD.AI, CYCLOPS, BLUPRYNT, BASED canonical pages. Replaced with clean audit callouts.

4. **Fixed investor lists** — CYCLOPS: removed 6 person-name artifacts (e.g., "Alex Wilson (co-founder"), added Shift4 Payments. USD.AI: removed 3 corrupt entries (e.g., "David Choi (CEO/co-founder").

5. **Migrated ARQ CEO data** — Fernando Terrés (CEO) + HQ (Mexico City) migrated from legacy SIGNAL PACK page to canonical ARQ page (`31cf30f9-bdff-81d8`).

---

## DEPLOYMENT CHECKLIST

> **All commands below run ON TONY** unless marked `[LOCAL]`.
> Transfer files from local repo to Tony via: `scp <local-path> tony:/home/ubuntu/clawd/scripts/`

| # | Step | Where | Command / Action |
|---|------|-------|-----------------|
| 0 | **PAUSE PIPELINE** | Tony | `crontab -e` — comment out all `founder-intel-pipeline`, `funding-intel-brief`, `outreach-pipeline-trigger` cron entries. This prevents new buggy runs between cleanup and patch deployment. |
| 1 | Back up scripts | Tony | See STEP 0 below |
| 2 | Transfer new modules | Local | `scp scripts/{qa_validator,content_sanitizer,resilient_api,canonical_template}.py tony:/home/ubuntu/clawd/scripts/` |
| 3 | Apply patches to 3 scripts | Tony | See SCRIPT 1/2/3 sections below |
| 4 | Syntax validation | Tony | `python3 -c 'import ast; ast.parse(open("script.py").read())'` for each |
| 5 | Dry-run test | Tony | `python3 scripts/funding-intel-brief.py --dry-run --company "CROSSOVER MARKETS"` |
| 6 | **RESUME PIPELINE** | Tony | `crontab -e` — uncomment the cron entries from step 0 |
| 7 | Commit & sync | Tony | `git add ... && git commit ...` then run `up top sync` |

---

## WHAT YOU NEED TO DO — Script Patches on Tony

### STEP 0: BACK UP ALL SCRIPTS FIRST

```bash
cd /home/ubuntu/clawd/scripts
cp founder-intel-pipeline.py founder-intel-pipeline.py.bak.20260308
cp funding-intel-brief.py funding-intel-brief.py.bak.20260308
cp hiring_intel_module.py hiring_intel_module.py.bak.20260308
```

---

### SCRIPT 1: `founder-intel-pipeline.py` — [CRITICAL] Stop creating standalone pages

**Bug (F-01):** For every fundraising event, this script does TWO things:
1. Appends outreach + hiring sections into the canonical FUNDRAISING INTEL page via `[[OUTREACH_INTEL_AUTO_START]]`/`[[OUTREACH_INTEL_AUTO_END]]` markers — this is CORRECT
2. Creates a brand new standalone page with `TYPE=OUTREACH TARGET` — this is WRONG and causes duplicates

All 20 archived pages came from path #2.

**Required changes:**

#### CHANGE 1: Guard standalone page creation
Find the function that creates standalone outreach pages (likely called `create_outreach_page()` or `write_outreach_report()` or similar). It will contain:
- `notion.pages.create(...)` with `"TYPE": {"select": {"name": "OUTREACH TARGET"}}`
- A `parent={"database_id": REPORT_BASE_DB}` argument

**Fix:** Wrap it so it ONLY fires when no canonical page exists:
```python
if not canonical_page_id:
    # Only create standalone page if we CANNOT find a canonical to append to
    logger.warning(f"No canonical page found for {company}, creating standalone outreach page")
    # ... existing page creation code ...
else:
    logger.info(f"Skipping standalone page — content appended to canonical {canonical_page_id}")
```

#### CHANGE 2: Verify marker-based replacement is idempotent
Find the function that appends outreach content to canonical pages (likely `append_outreach_to_canonical()` or similar). It uses `[[OUTREACH_INTEL_AUTO_START]]` / `[[OUTREACH_INTEL_AUTO_END]]` markers.

**Verify:** It REPLACES content between markers (not just appends after END marker). If it does incremental append, change to:
1. Delete all blocks between START and END markers
2. Insert new blocks between markers
3. This ensures reruns replace content, not stack it

#### CHANGE 3: Add REPORT KEY to any fallback standalone pages
If standalone pages DO get created (because no canonical was found), they MUST have a REPORT KEY:
```python
report_key = f"outreach-intel:{company_slug}:{amount}"
```
Currently all standalone pages had REPORT KEY="" (empty), which bypasses all dedup logic.

#### CHANGE 4: Add dedup check before creating any page
Before creating any new page, query Report Base for existing pages:
```python
existing = notion.databases.query(
    database_id=REPORT_BASE_DB,
    filter={
        "and": [
            {"property": "TYPE", "select": {"equals": "OUTREACH TARGET"}},
            {"property": "ENTRY", "rich_text": {"contains": company_name}}
        ]
    }
)
if existing["results"]:
    logger.info(f"Outreach page already exists for {company_name}, updating instead of creating")
    # Update existing page instead of creating new one
```

---

### SCRIPT 2: `funding-intel-brief.py` — Harden dedup and investor parsing

**Bugs:** F-02 (archived-dup body refs), F-03 (corrupt investor lists), F-06 (REPORT KEY format), F-09 (amount mismatch)

#### CHANGE 1: Add investor list sanitization
Find the investor aggregation function (likely `aggregate_investors()`, `parse_investors()`, or `merge_investor_lists()`).

**Add this sanitization function** and call it on the final investor list before writing to Notion:
```python
def sanitize_investor_list(investors: list[str]) -> list[str]:
    """Remove person-name artifacts that leaked into investor list."""
    role_indicators = ['co-founder', 'ceo', 'cto', 'cfo', 'founder', 'partner']
    cleaned = []
    for inv in investors:
        inv_lower = inv.strip().lower()
        # Skip entries like "(co-founder" or "CompanyName)"
        if inv_lower.startswith('(') or inv_lower.endswith(')'):
            continue
        # Skip entries containing role indicators with parentheses
        if any(f'({role}' in inv_lower or f'{role})' in inv_lower for role in role_indicators):
            continue
        # Skip entries with parenthetical role
        if '(' in inv and any(role in inv.lower() for role in role_indicators):
            continue
        cleaned.append(inv.strip())
    return list(dict.fromkeys(cleaned))  # dedupe preserving order
```

#### CHANGE 2: Stop embedding "Archived duplicate:" text in page bodies
Find the merge/dedup function that writes to canonical pages. Look for strings like:
- `f"Archived duplicate: {dup_title}"`
- `f"Dedup cleanup ({timestamp}): ..."`

**Fix:** Move these to the QA ISSUES property instead of page body:
```python
# INSTEAD OF appending to page body:
# body_blocks.append(f"- Archived duplicate: {dup_title}")

# WRITE TO QA ISSUES property:
qa_notes = page_props.get("QA ISSUES", "") or ""
qa_notes += f"\nArchived: {dup_title} ({timestamp})"
notion.pages.update(page_id=canonical_id, properties={
    "QA ISSUES": {"rich_text": [{"text": {"content": qa_notes.strip()}}]}
})
```

#### CHANGE 3: Normalize REPORT KEY to v3 format
Ensure ALL new pages use the v3 format:
```python
def generate_report_key(company: str, amount: int) -> str:
    slug = slugify(company)  # lowercase, hyphens, no special chars
    return f"fundraising-intel:v3:{slug}:{amount}"
```

**Also update the dedup query** to match BOTH v3 and legacy formats:
```python
def find_existing_canonical(company: str, amount: int):
    v3_key = generate_report_key(company, amount)
    legacy_key_prefix = f"fundraising-intel:{slugify(company)}:"
    results = notion.databases.query(
        database_id=REPORT_BASE_DB,
        filter={"or": [
            {"property": "REPORT KEY", "rich_text": {"equals": v3_key}},
            {"property": "REPORT KEY", "rich_text": {"starts_with": legacy_key_prefix}},
        ]}
    )
    return results
```

#### CHANGE 4: Add amount-tolerance matching (±5%)
When comparing amounts for dedup, allow ±5% tolerance:
```python
def amounts_match(a: int, b: int, tolerance: float = 0.05) -> bool:
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(a, b) <= tolerance
```

This prevents duplication when the same round is reported as $4.2M vs $4.25M (BLUPRYNT case).

---

### SCRIPT 3: `hiring_intel_module.py` — Fix error-as-signal bugs

**Bugs:** F-04 (DuckDuckGo errors as signals), F-05 (Grok timeout as benchmark text)

#### CHANGE 1: Suppress DuckDuckGo transport errors
Find the hiring signal search function (likely `search_hiring_signals()`). It will have a `requests.get()` call to `html.duckduckgo.com`.

**Current bug:** The except block returns the error string AS a signal:
```python
# BUGGY:
except Exception as e:
    return [{"signal": str(e), "confidence": 7}]  # <-- error text becomes a "hiring signal"
```

**Fix:**
```python
except (requests.ConnectionError, requests.Timeout) as e:
    logger.warning(f"DuckDuckGo search failed for {company}: {e}")
    return []  # Empty list, not error-as-signal
except Exception as e:
    logger.error(f"Unexpected error searching hiring signals for {company}: {e}")
    return []
```

#### CHANGE 2: Handle Grok API timeout in competitor benchmarks
Find the competitor benchmark function (likely `generate_competitor_benchmark()`). It calls the Grok API at `api.x.ai`.

**Fix:**
```python
try:
    benchmark = call_grok_api(...)
except (requests.Timeout, requests.ConnectionError) as e:
    logger.warning(f"Grok API timeout for {company} benchmark: {e}")
    benchmark = "Competitor benchmark unavailable — API timeout. Manual review recommended."
return benchmark
```

---

## VALIDATION

After applying all changes, run these checks:

```bash
# 1. Syntax validation
python3 -c 'import ast; ast.parse(open("/home/ubuntu/clawd/scripts/founder-intel-pipeline.py").read()); print("founder-intel-pipeline: OK")'
python3 -c 'import ast; ast.parse(open("/home/ubuntu/clawd/scripts/funding-intel-brief.py").read()); print("funding-intel-brief: OK")'
python3 -c 'import ast; ast.parse(open("/home/ubuntu/clawd/scripts/hiring_intel_module.py").read()); print("hiring_intel_module: OK")'

# 2. Dry-run test (if --dry-run flag exists)
cd /home/ubuntu/clawd
python3 scripts/funding-intel-brief.py --dry-run --company "CROSSOVER MARKETS"

# 3. Check: no new standalone OUTREACH TARGET pages should be created
# After running a real pipeline execution, query Notion for:
#   TYPE=OUTREACH TARGET, LAST EDITED within 1 hour
# Result should be empty (no new standalone pages)
```

---

## SYNC STEP

After applying and validating the patches on Tony, sync the changes:

```bash
# From Tony, commit the modified scripts
cd /home/ubuntu/clawd
git add scripts/founder-intel-pipeline.py scripts/funding-intel-brief.py scripts/hiring_intel_module.py
git commit -m "Apply audit fixes: stop standalone outreach pages, harden dedup, fix error-as-signal

Fixes F-01 through F-06, F-09:
- founder-intel-pipeline: guard standalone page creation behind canonical-not-found check
- funding-intel-brief: sanitize investor lists, QA ISSUES for dedup notes, v3 REPORT KEY, amount tolerance
- hiring_intel_module: suppress DuckDuckGo/Grok transport errors from signals

Audit: claude/audit-search-report-duplication-TNk3J"
```

Then run "up top sync" per your standard deployment workflow to propagate to codex/claude/tony.

---

## REFERENCE: NOTION SCHEMA (Report Base properties)

| Property | Type | Purpose |
|----------|------|---------|
| ENTRY | Title | Page title |
| TYPE | Select | FUNDRAISING INTEL, OUTREACH TARGET, SIGNAL PACK |
| SOURCE SKILL | Select | funding-intel-brief, founder-intel-pipeline, crypto-fundraising-monitor |
| REPORT KEY | Rich Text | Dedup key. v3: `fundraising-intel:v3:{slug}:{amount}`. Legacy: `fundraising-intel:{slug}:{date}` |
| RUN ID | Rich Text | Pipeline execution ID |
| QA STATUS | Select | PASS, WARN, FAIL, SKIP |
| QA ISSUES | Rich Text | Audit notes, dedup notes |
| POC | People | Team members assigned |
| DATE | Date | Event detection date |
| LAST AUDITED AT | Date | Last audit timestamp |

## REFERENCE: PIPELINE TOPOLOGY

```
funding-intel-brief.py
  ↓ creates/updates canonical TYPE=FUNDRAISING INTEL pages
  ↓ sets REPORT KEY, RUN ID, SOURCE SKILL=funding-intel-brief

founder-intel-pipeline.py (triggered by outreach-pipeline-trigger.py)
  ↓ reads canonical pages
  ↓ generates outreach content → appends via [[OUTREACH_INTEL_AUTO_START/END]] markers
  ↓ generates hiring content → appends via [[HIRING_INTEL_AUTO_START/END]] markers
  ↓ [BUG] ALSO creates standalone TYPE=OUTREACH TARGET pages ← DISABLE THIS

hiring_intel_module.py (imported by founder-intel-pipeline.py)
  ↓ generates hiring intel blocks consumed by founder-intel-pipeline
  ↓ uses DuckDuckGo + Grok API for signals

crypto-fundraising-monitor (watcher-guru-notion-writer.js)
  ↓ creates TYPE=SIGNAL PACK pages (legacy, separate pipeline, not modified here)
```

## REFERENCE: MARKER FORMAT

Content auto-replacement uses these delimiters in canonical page bodies:
```
[[OUTREACH_INTEL_AUTO_START]]
... outreach content (replaced on each run) ...
[[OUTREACH_INTEL_AUTO_END]]

[[HIRING_INTEL_AUTO_START]]
... hiring content (replaced on each run) ...
[[HIRING_INTEL_AUTO_END]]
```

The correct behavior is **replace between markers** (idempotent). The bug was **create new page alongside** (non-idempotent).

---

## SUMMARY OF ALL FINDINGS

| ID | Severity | Script | Status |
|----|----------|--------|--------|
| F-01 | CRITICAL | founder-intel-pipeline.py | **PATCH READY** — disable standalone page creation |
| F-02 | HIGH | funding-intel-brief.py | **PATCH READY** — move dedup notes to QA ISSUES |
| F-03 | HIGH | funding-intel-brief.py | **PATCH READY** — sanitize investor lists |
| F-04 | MEDIUM | hiring_intel_module.py | **PATCH READY** — suppress DuckDuckGo errors |
| F-05 | MEDIUM | hiring_intel_module.py | **PATCH READY** — handle Grok timeouts |
| F-06 | MEDIUM | funding-intel-brief.py | **PATCH READY** — normalize REPORT KEY to v3 |
| F-07 | LOW | hiring_intel_module.py | Not patched — generic vertical classification |
| F-08 | LOW | founder-intel-pipeline.py | Not patched — duplicate outreach channels |
| F-09 | HIGH | funding-intel-brief.py | **PATCH READY** — amount-tolerance matching |
| F-10 | MEDIUM | founder-intel-pipeline.py | Not patched — garbage POC from scraper |
| F-11 | CRITICAL | funding-intel-brief.py | **PATCH READY** — $0 bad-parse bypasses all dedup |

F-07, F-08, F-10 are deferred — they require deeper refactoring of the Grok prompt templates and website scraper logic.

### F-11: $0 / "undisclosed" amount creates phantom duplicates

**Severity:** CRITICAL — pipeline keeps creating new $0 pages every run
**Root cause:** When the pipeline fails to parse a dollar amount from a source article, it writes `amount=0` and `round_type=STRATEGIC`. This generates REPORT KEYs like `fundraising-intel:v3:axiym:0` which never match the real page's key (`fundraising-intel:v3:axiym:8180000`). The dedup system sees them as separate deals.

**Already archived (3 pages):**
- AXIYM — USD 0 STRATEGIC (`31df30f9-bdff-8112`) → canonical is $8.2M SEED (`31df30f9-bdff-81f4`)
- IZUMI FINANCE — USD 0 STRATEGIC (`31df30f9-bdff-818c`) → canonical is $27.6M STRATEGIC (`31bf30f9-bdff-81ac`)
- SATS TERMINAL — $0 STRATEGIC (`31df30f9-bdff-819d`) → canonical is Signal Pack (`319f30f9-bdff-8154`)

**Required fix in `funding-intel-brief.py`:**
```python
# BEFORE generating REPORT KEY, check for $0 / undisclosed amounts:
if round_amount == 0 or round_amount is None:
    # Search by company name alone — $0 means the amount wasn't parseable
    existing = notion.databases.query(
        database_id=REPORT_BASE_DB,
        filter={
            "and": [
                {"property": "COMPANY", "rich_text": {"contains": company_name}},
                {"property": "TYPE", "select": {"equals": "FUNDRAISING INTEL"}},
            ]
        }
    )
    if existing["results"]:
        # Found existing page for this company — skip, do not create $0 duplicate
        logger.warning(
            f"Skipping $0 page for {company_name} — "
            f"existing page found: {existing['results'][0]['id']}"
        )
        return existing["results"][0]["id"]

    # No existing page AND amount is $0 — create but flag it
    logger.warning(f"Creating $0 page for {company_name} — no existing page found, amount parse failed")
```

This must be applied BEFORE the `generate_report_key()` call and BEFORE `notion.pages.create()`.

---

## NEW SCRIPTS TO DEPLOY (copy from repo to Tony)

In addition to patching the 3 existing scripts, deploy these 4 new modules:

### 1. `qa_validator.py` — Post-write QA gate

**Deploy to:** `/home/ubuntu/clawd/scripts/qa_validator.py`

Automated QA validator that runs after every pipeline write. Sets QA STATUS based on:
- FAIL: Error text in body, missing REPORT KEY, empty content
- WARN: Missing outreach/hiring markers, <3 investors, no POC found, mcp_unavailable noise
- PASS: All checks pass

**Integration (PRE-WRITE GATE — add BEFORE `notion.pages.create()`):**
```python
from qa_validator import pre_write_validate
qa = pre_write_validate(props, blocks, page_type="FUNDRAISING INTEL")
if qa.status == "FAIL":
    logger.error(f"Pre-write QA FAIL: {qa.summary} — aborting write")
    return None  # Do NOT create the page
```

**Integration (POST-WRITE HOOK — add AFTER successful write):**
```python
from qa_validator import post_write_hook
status = post_write_hook(page_id)
if status == "FAIL":
    logger.error(f"Page {page_id} failed QA — check QA ISSUES property")
```

**CLI usage:**
```bash
python3 qa_validator.py --page-id <id>           # Validate one page
python3 qa_validator.py --recent 24               # Validate last 24h of pages
python3 qa_validator.py --recent 24 --dry-run     # Report without updating
```

### 2. `content_sanitizer.py` — Error text stripper + investor cleaner

**Deploy to:** `/home/ubuntu/clawd/scripts/content_sanitizer.py`

Pre-write filter that strips error artifacts before content reaches Notion. Also provides `sanitize_investor_list()` to clean person-name artifacts from investor lists.

**Integration:** In `funding-intel-brief.py`:
```python
from content_sanitizer import sanitize_blocks, sanitize_investor_list

# Before writing blocks to Notion:
clean_blocks = sanitize_blocks(raw_blocks)

# Before writing investor list:
clean_investors = sanitize_investor_list(raw_investors)
```

In `hiring_intel_module.py`:
```python
from content_sanitizer import sanitize_blocks
# Sanitize hiring blocks before returning:
return sanitize_blocks(hiring_blocks)
```

### 3. `resilient_api.py` — Retry + circuit breaker for external APIs

**Deploy to:** `/home/ubuntu/clawd/scripts/resilient_api.py`

Wraps DuckDuckGo, Grok, Clay, and Icebreaker calls with retry logic + circuit breaking.

**Integration:** In `hiring_intel_module.py`:
```python
from resilient_api import search_duckduckgo, call_grok

# Replace direct requests.get("https://html.duckduckgo.com/...") with:
html = search_duckduckgo(f'"{company}" "we\'re hiring"')
if html is None:
    return []  # Clean fallback, not error-as-signal

# Replace direct Grok API calls with:
benchmark = call_grok(prompt)
if benchmark is None:
    benchmark = "Competitor benchmark unavailable — API timeout. Manual review recommended."
```

### 4. `canonical_template.py` — Page structure contract

**Deploy to:** `/home/ubuntu/clawd/scripts/canonical_template.py`

Defines the canonical page template structure. Provides:
- `build_canonical_blocks()` — creates skeleton pages with proper markers
- `build_page_properties()` — creates properties including COMPANY, ROUND AMOUNT, v3 REPORT KEY
- `validate_page_structure()` — checks if a page follows the template
- `generate_report_key()` — generates v3 REPORT KEYs
- `parse_amount()` / `amounts_match()` — amount parsing with ±5% tolerance
- `slugify()` — consistent company slug generation

**Integration:** In `funding-intel-brief.py`:
```python
from canonical_template import (
    build_canonical_blocks,
    build_page_properties,
    generate_report_key,
    amounts_match,
    parse_amount,
)

# When creating a new page:
blocks = build_canonical_blocks(company, amount, round_type, investors, sources)
props = build_page_properties(company, amount, round_type, run_id)
page = notion.pages.create(parent={"database_id": DB_ID}, properties=props, children=blocks)

# When checking for duplicates:
if amounts_match(existing_amount, new_amount):
    logger.info("Duplicate detected — updating existing page")
```

---

## ADDITIONAL NOTION-SIDE CHANGES ALREADY COMPLETED

These were applied directly during the audit — do NOT repeat:

1. **Schema: COMPANY property added** (rich_text) — backfilled on ~30 active pages
2. **Schema: ROUND AMOUNT property added** (number, dollar format) — backfilled
3. **Signal Pack REPORT KEYs backfilled** — format `signal-pack:v1:{YYYY-MM-DD}` on Mar 3-8 packs
4. **OKX REPORT KEY upgraded** to `fundraising-intel:v3:okx:200000000`
5. **Helios Finance markers fixed** — added `[[HIRING_INTEL_AUTO_START/END]]` around hiring section
6. **Helios Finance errors cleaned** — DuckDuckGo + Grok error artifacts replaced with clean fallback text
7. **Helios Finance REPORT KEY upgraded** to v3 format
8. **AXIYM QA STATUS** changed PASS → WARN (bare skeleton, no enrichment)
9. **QFEX QA STATUS** changed PASS → WARN (merge format, no enrichment, unverified investors)
10. **OMNIPACT QA STATUS** already WARN (correct)
11. **LEGEND QA ISSUES** updated with structural note (inline founder intel, non-standard but valid)
12. **AXIYM $0 STRATEGIC archived** (`31df30f9-bdff-8112`) — F-11 $0 bad-parse duplicate of $8.2M SEED canonical
13. **IZUMI FINANCE $0 STRATEGIC archived** (`31df30f9-bdff-818c`) — F-11 $0 bad-parse duplicate of $27.6M STRATEGIC canonical
14. **IZUMI FINANCE REPORT KEY upgraded** to `fundraising-intel:v3:izumi-finance:27600000` (was legacy date format)
15. **IZUMI FINANCE hiring markers added** — `[[HIRING_INTEL_AUTO_START/END]]` wrapped around hiring section + DuckDuckGo error cleaned
16. **SATS TERMINAL $0 STRATEGIC archived** (`31df30f9-bdff-819d`) — F-11 $0 bad-parse duplicate of Signal Pack canonical
17. **PROBABLE $0 M&A QA STATUS** changed PASS → WARN (missing RUN ID, SOURCE SKILL; CZ entries are noise)
