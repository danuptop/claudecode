# CODEX DEPLOYMENT PROMPT — Report Base Pipeline Full Fix + Module Deployment

**Date:** March 9, 2026
**Context:** Two forensic audits (Mar 8 + Mar 9) of the Up Top Search "Report Base" pipeline identified 17 bugs (F-01 through F-17) causing: duplicate Notion pages, wrong company websites scraped, fabricated outreach angles, garbage POC names, template-fill masquerading as intelligence, and stacked content sections. Notion-side cleanups (22+ page archives, inline data fixes) are complete. **Your job is to deploy 5 new support modules and apply code fixes to 3 production scripts on Tony.**

---

## ENVIRONMENT

- **Server:** Tony (SSH alias `tony`, scripts at `/home/ubuntu/clawd/scripts/`)
- **Production scripts to modify:**
  1. `/home/ubuntu/clawd/scripts/founder-intel-pipeline.py`
  2. `/home/ubuntu/clawd/scripts/funding-intel-brief.py`
  3. `/home/ubuntu/clawd/scripts/hiring_intel_module.py`
- **New support modules to deploy (from this repo):**
  1. `scripts/domain_resolver.py` → `/home/ubuntu/clawd/scripts/domain_resolver.py`
  2. `scripts/qa_validator.py` → `/home/ubuntu/clawd/scripts/qa_validator.py`
  3. `scripts/content_sanitizer.py` → `/home/ubuntu/clawd/scripts/content_sanitizer.py`
  4. `scripts/canonical_template.py` → `/home/ubuntu/clawd/scripts/canonical_template.py`
  5. `scripts/resilient_api.py` → `/home/ubuntu/clawd/scripts/resilient_api.py`
- **Notion Database:** Report Base (`902d47be-68c0-4da8-832a-a52272fc7b39`), Data Source `598bb1d8-26cb-4e2a-8b4f-8b80e1be7116`
- **Repo:** `danuptop/claudecode` branch `claude/audit-report-duplicates-jZpv2`

---

## CRITICAL CONTEXT

**NONE of the Mar 8 patches were deployed.** The Mar 9 audit confirmed every bug is still active in production. The pipeline ran again at `2026-03-09T034743` and created more garbage pages. Every minute these fixes remain undeployed, the pipeline creates more bad data that requires manual cleanup.

---

## STEP 1: BACKUP PRODUCTION SCRIPTS

SSH into Tony and back up all scripts before making changes:

```bash
ssh tony "cd /home/ubuntu/clawd/scripts && \
  cp founder-intel-pipeline.py founder-intel-pipeline.py.bak.20260309 && \
  cp funding-intel-brief.py funding-intel-brief.py.bak.20260309 && \
  cp hiring_intel_module.py hiring_intel_module.py.bak.20260309 && \
  echo 'Backups created'"
```

---

## STEP 2: DEPLOY NEW SUPPORT MODULES

Copy the 5 new support modules from this repo to Tony. These are complete, tested Python files — deploy as-is:

```bash
scp scripts/domain_resolver.py tony:/home/ubuntu/clawd/scripts/
scp scripts/qa_validator.py tony:/home/ubuntu/clawd/scripts/
scp scripts/content_sanitizer.py tony:/home/ubuntu/clawd/scripts/
scp scripts/canonical_template.py tony:/home/ubuntu/clawd/scripts/
scp scripts/resilient_api.py tony:/home/ubuntu/clawd/scripts/
```

Verify they parse correctly:

```bash
ssh tony "cd /home/ubuntu/clawd/scripts && \
  python3 -c 'import ast; \
  [ast.parse(open(f).read()) for f in [\"domain_resolver.py\", \"qa_validator.py\", \"content_sanitizer.py\", \"canonical_template.py\", \"resilient_api.py\"]]; \
  print(\"All 5 modules parse OK\")'"
```

---

## STEP 3: MODIFY `founder-intel-pipeline.py`

Read the file on Tony first:

```bash
ssh tony "cat /home/ubuntu/clawd/scripts/founder-intel-pipeline.py"
```

Then apply ALL of the following changes:

### 3A. Add imports at the top of the file

Add after existing imports:

```python
from domain_resolver import resolve_domain, validate_scraped_domain, build_enrichment_context
from content_sanitizer import sanitize_blocks, sanitize_text, sanitize_investor_list
from qa_validator import post_write_hook
from resilient_api import resilient_call, search_duckduckgo, call_grok, grok_breaker, duckduckgo_breaker
from resilient_api import FALLBACK_SEARCH_UNAVAILABLE, FALLBACK_BENCHMARK_UNAVAILABLE
from canonical_template import normalize_company_slug, slugs_likely_match, amounts_match
```

### 3B. Fix F-10: Replace naive domain resolution

**SEARCH FOR** the domain resolution logic. It likely looks like one of these patterns:

```python
domain = f"{company_name.lower().replace(' ', '')}.com"
# or
domain = f"{slugify(company_name)}.com"
# or
domain = search_for_domain(company_name)  # DuckDuckGo search
```

**REPLACE WITH:**

```python
# Build context from deal data for domain validation
deal_context = build_enrichment_context(
    company=company_name,
    round_amount=round_amount,
    round_type=round_type,
    investors=investors,
)

# Resolve domain using known table + TLD probing
def _probe_domain(domain: str) -> bool:
    """Check if a domain exists and returns a real website."""
    try:
        resp = requests.head(f"https://{domain}", timeout=5, allow_redirects=True)
        return resp.status_code < 400
    except Exception:
        return False

domain = resolve_domain(
    company_name=company_name,
    context=deal_context,
    probe_fn=_probe_domain,
)
```

### 3C. Fix F-10: Add post-scrape validation

**SEARCH FOR** where the website is scraped and description/POC extracted. It likely looks like:

```python
resp = requests.get(f"https://{domain}", ...)
description = extract_description(resp.text)
ceo = extract_ceo(resp.text)
```

**ADD AFTER** the scraping, BEFORE using the scraped data:

```python
# Validate that scraped content matches the target company
is_valid = validate_scraped_domain(
    target_company=company_name,
    scraped_domain=domain,
    scraped_description=description,
    deal_context=deal_context,
)

if not is_valid:
    logger.error(
        f"DOMAIN MISMATCH: {domain} does not appear to be {company_name}. "
        f"Scraped description: {description[:100]}... "
        f"Skipping POC extraction — manual research required."
    )
    # Use fallback values instead of garbage data
    description = f"{company_name} — company description pending manual research."
    ceo = None
    ceo_title = None
    domain = None  # Clear domain so it's not shown as verified
```

### 3D. Fix F-01: Disable standalone OUTREACH TARGET page creation

**SEARCH FOR** the function that creates standalone outreach pages. Look for:

```python
def create_outreach_page(...):
    ...
    notion.pages.create(
        parent={"database_id": REPORT_BASE_DB},
        properties={
            ...
            "TYPE": {"select": {"name": "OUTREACH TARGET"}},
            ...
        },
        children=outreach_blocks
    )
```

**OR:**

```python
def write_outreach_report(...):
    ...
    # Creates a new page with TYPE=OUTREACH TARGET
```

**REPLACE WITH** a guard that only creates standalone pages when no canonical page exists:

```python
if not canonical_page_id:
    # Only create standalone page if we CANNOT find a canonical to append to
    logger.warning(f"No canonical page found for {company_name}, creating standalone outreach page")
    # Also add a REPORT KEY so dedup can find it later
    properties["REPORT KEY"] = {
        "rich_text": [{"text": {"content": f"outreach-intel:{normalize_company_slug(company_name)}:{round_amount}"}}]
    }
    create_outreach_page(...)
else:
    logger.info(f"Skipping standalone page — content appended to canonical {canonical_page_id}")
```

### 3E. Fix F-01: Add dedup check before creating any page

**ADD BEFORE** any `notion.pages.create()` call for OUTREACH TARGET pages:

```python
# Check if an outreach page already exists for this company
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
    logger.info(f"Outreach page already exists for {company_name}, skipping creation")
    # Update existing page instead, or skip entirely
```

### 3F. Ensure marker-based append is idempotent

**VERIFY** the function that appends outreach content to canonical pages. It should:
1. Find `[[OUTREACH_INTEL_AUTO_START]]` and `[[OUTREACH_INTEL_AUTO_END]]` markers
2. **DELETE** all blocks between the markers
3. **INSERT** new blocks between markers

If it currently appends after the end marker instead of replacing between markers, fix it to use delete-then-insert for idempotency.

### 3G. Fix F-14: Conditional outreach angles (stop fabricating)

**SEARCH FOR** the outreach angle generation. It likely has a hardcoded template like:

```python
f"Congrats on the ${amount}M raise with solid VC backing—..."
```

**REPLACE WITH** conditional logic:

```python
# Only claim "VC backing" if we actually have investors
if investors and investors != ["Unknown"] and investors != ["Not listed"]:
    investor_text = f"backed by {', '.join(investors[:3])}"
else:
    investor_text = ""

# Only generate outreach angle if we have real data
if ceo or description != "N/A":
    outreach_angle = f"Congrats on the {amount_str} {round_type} raise"
    if investor_text:
        outreach_angle += f" {investor_text}"
    outreach_angle += f"—impressive for {company_name}. "
    outreach_angle += "Up Top Search specializes in crypto-native talent. Let's chat."
else:
    outreach_angle = (
        f"Outreach angle pending — insufficient data for personalized outreach. "
        f"Manual research recommended for {company_name}."
    )
```

### 3H. Wire in post-write QA hook

**ADD** at the end of every function that creates or updates a Notion page:

```python
# Run QA validation after write
qa_status = post_write_hook(page_id)
if qa_status == "FAIL":
    logger.error(f"Page {page_id} FAILED QA validation after write")
elif qa_status == "WARN":
    logger.warning(f"Page {page_id} has QA warnings — review recommended")
```

### 3I. Fix F-08: Deduplicate outreach channel recommendations

**SEARCH FOR** the outreach channel section builder. It likely builds a list of recommended channels.

**ADD** deduplication before writing:

```python
# Dedupe channels preserving order
seen_channels = set()
unique_channels = []
for channel in channels:
    key = channel.get("name", "").lower().strip()
    if key and key not in seen_channels:
        seen_channels.add(key)
        unique_channels.append(channel)
channels = unique_channels
```

### 3J. Sanitize all content before writing to Notion

**ADD BEFORE** every `notion.blocks.children.append()` or `notion.pages.create(children=...)` call:

```python
# Sanitize blocks before writing — strip error artifacts
blocks_to_write = sanitize_blocks(blocks_to_write)
```

---

## STEP 4: MODIFY `funding-intel-brief.py`

Read the file on Tony first:

```bash
ssh tony "cat /home/ubuntu/clawd/scripts/funding-intel-brief.py"
```

Then apply ALL of the following changes:

### 4A. Add imports

```python
from canonical_template import (
    generate_report_key, normalize_company_slug, slugs_likely_match,
    amounts_match, build_page_properties, build_canonical_blocks, parse_amount,
)
from content_sanitizer import sanitize_investor_list
from qa_validator import post_write_hook
```

### 4B. Fix F-03: Sanitize investor lists

**SEARCH FOR** the investor aggregation/parsing function (likely named `aggregate_investors`, `parse_investors`, or `merge_investor_lists`).

**ADD** sanitization after aggregation:

```python
# Sanitize investor list — remove person-name artifacts
investors = sanitize_investor_list(investors)
```

The `sanitize_investor_list` function (in `content_sanitizer.py`) filters out:
- Entries starting with `(` — orphaned role fragments like `(co-founder`
- Entries ending with `)` without opening `(` — orphaned closers like `Cyclops)`
- Entries with parenthetical roles: `"Alex Wilson (co-founder"`
- Entries that are just role indicators: `"co-founder"`, `"CEO"`
- Deduplicates preserving order

### 4C. Fix F-02: Stop embedding "Archived duplicate:" in page body

**SEARCH FOR** where duplicate references are written into page content. Look for:

```python
f"Archived duplicate: {dup_title}"
# or
f"Dedup cleanup ({timestamp}): ..."
```

**CHANGE** to write to QA ISSUES property instead of page body:

```python
# Instead of appending to page body:
# body_blocks.append(f"- Archived duplicate: {dup_title}")

# Write to QA ISSUES property:
qa_notes = page_props.get("QA ISSUES", "") or ""
qa_notes += f"\nArchived: {dup_title} ({timestamp})"
notion.pages.update(page_id=canonical_id, properties={
    "QA ISSUES": {"rich_text": [{"text": {"content": qa_notes.strip()[:2000]}}]}
})
```

### 4D. Fix F-06: Normalize REPORT KEY format

**SEARCH FOR** the report key generation logic. Ensure ALL new pages use v3 format:

```python
# Replace any legacy key generation:
# report_key = f"fundraising-intel:{slug}:{date_str}"

# With v3 format:
report_key = generate_report_key(company_name, round_amount)
# Returns: "fundraising-intel:v3:{normalized_slug}:{amount}"
```

### 4E. Fix F-06: Legacy key matching in dedup queries

The dedup query MUST also match legacy key formats:

```python
def find_existing_canonical(company: str, amount: int):
    slug = normalize_company_slug(company)
    v3_key = generate_report_key(company, amount)
    legacy_key_prefix = f"fundraising-intel:{slug}:"

    results = notion.databases.query(
        database_id=REPORT_BASE_DB,
        filter={"or": [
            {"property": "REPORT KEY", "rich_text": {"equals": v3_key}},
            {"property": "REPORT KEY", "rich_text": {"starts_with": legacy_key_prefix}},
        ]}
    )
    return results
```

### 4F. Fix F-09: Amount tolerance matching

When comparing amounts during dedup, allow ±5% tolerance:

```python
# Replace exact amount matching:
# if existing_amount == new_amount:

# With tolerance matching (from canonical_template):
if amounts_match(existing_amount, new_amount, tolerance=0.05):
    # Same deal — update existing page instead of creating new one
```

### 4G. Fix F-11: Pre-creation filter for non-actionable events

**ADD BEFORE** page creation:

```python
# Filter out non-actionable events
if round_type == "M&A":
    logger.info(f"Skipping {company_name} — M&A events don't trigger hiring outreach")
    continue  # or return, depending on control flow

if round_amount == 0:
    logger.warning(f"Skipping {company_name} — $0 amount (parse failure or undisclosed)")
    continue

if round_amount > 500_000_000:
    logger.warning(f"Flagging {company_name} — ${round_amount/1e6:.0f}M may be aggregated funding or valuation")
    # Still create the page but set QA STATUS to WARN
```

### 4H. Entity resolution before key generation

**ADD BEFORE** generating the REPORT KEY:

```python
# Normalize company name for consistent dedup
normalized_slug = normalize_company_slug(company_name)

# Check if this company matches an existing page under a different name
# (e.g., "USD AI PERMIAN LABS" should match existing "USD.AI" page)
existing_pages = notion.databases.query(
    database_id=REPORT_BASE_DB,
    filter={
        "and": [
            {"property": "TYPE", "select": {"equals": "FUNDRAISING INTEL"}},
            {"property": "COMPANY", "rich_text": {"is_not_empty": True}},
        ]
    },
    page_size=100,
)

for page in existing_pages.get("results", []):
    existing_company = "".join(
        p.get("plain_text", "")
        for p in page["properties"].get("COMPANY", {}).get("rich_text", [])
    ).strip()
    if existing_company and slugs_likely_match(normalized_slug, normalize_company_slug(existing_company)):
        existing_amount = page["properties"].get("ROUND AMOUNT", {}).get("number", 0)
        if amounts_match(round_amount, existing_amount, tolerance=0.35):
            logger.info(
                f"Entity match: '{company_name}' matches existing '{existing_company}' "
                f"(amounts: ${round_amount:,} vs ${existing_amount:,})"
            )
            # Update existing page instead of creating duplicate
            canonical_page_id = page["id"]
            break
```

### 4I. Wire in post-write QA hook

Same as 3H — add `post_write_hook(page_id)` after every page create/update.

---

## STEP 5: MODIFY `hiring_intel_module.py`

Read the file on Tony first:

```bash
ssh tony "cat /home/ubuntu/clawd/scripts/hiring_intel_module.py"
```

### 5A. Add imports

```python
from resilient_api import (
    resilient_call, search_duckduckgo, call_grok,
    duckduckgo_breaker, grok_breaker,
    FALLBACK_SEARCH_UNAVAILABLE, FALLBACK_BENCHMARK_UNAVAILABLE,
)
from content_sanitizer import sanitize_text
```

### 5B. Fix F-04: DuckDuckGo error handling

**SEARCH FOR** the hiring signal search function. It likely looks like:

```python
def search_hiring_signals(company: str) -> list:
    try:
        resp = requests.get(f"https://html.duckduckgo.com/html/?q=...")
    except Exception as e:
        return [{"signal": str(e), "confidence": 7}]  # BUG: error as signal
```

**REPLACE WITH:**

```python
def search_hiring_signals(company: str) -> list:
    html = search_duckduckgo(f"{company} hiring jobs careers")
    if html is None:
        # DuckDuckGo unavailable — return empty, not error-as-signal
        logger.warning(f"DuckDuckGo search failed for {company} — skipping hiring signals")
        return []
    # ... parse signals from html ...
```

### 5C. Fix F-05: Grok API timeout handling

**SEARCH FOR** the competitor benchmark function. It likely calls Grok directly:

```python
def generate_competitor_benchmark(company, vertical, round_size):
    resp = requests.post("https://api.x.ai/v1/chat/completions", ...)
    # No error handling — timeout produces raw error text
```

**REPLACE WITH:**

```python
def generate_competitor_benchmark(company, vertical, round_size):
    prompt = f"... {company} ... {vertical} ..."
    result = call_grok(prompt, timeout=45)
    if result is None:
        return FALLBACK_BENCHMARK_UNAVAILABLE
    return result
```

### 5D. Fix F-15: Dynamic vertical classification

**SEARCH FOR** the vertical classification. It likely uses a static default:

```python
vertical = "Institutional Crypto"  # or a simple lookup table
```

**REPLACE WITH:**

```python
VERTICAL_KEYWORDS = {
    "DeFi": ["defi", "lending", "borrowing", "swap", "liquidity", "yield", "amm"],
    "Infrastructure": ["layer", "bridge", "oracle", "interoperability", "rollup", "l2", "chain"],
    "Payments": ["payment", "settlement", "stablecoin", "remittance", "neobank"],
    "Gaming/NFT": ["game", "metaverse", "nft", "play-to-earn"],
    "Security/Compliance": ["audit", "compliance", "identity", "kyc", "aml"],
    "Exchange": ["exchange", "dex", "cex", "trading", "order book"],
    "Data/Analytics": ["data", "analytics", "indexer", "explorer"],
}

def classify_vertical(description: str, company_name: str) -> str:
    desc_lower = (description or "").lower()
    for vertical, keywords in VERTICAL_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            return vertical
    return "Unclassified"  # NOT "Institutional Crypto"
```

### 5E. Ensure output blocks include markers

**VERIFY** that the hiring output includes markers:

```python
def generate_hiring_intel_blocks(company, round_info, ...):
    blocks = []
    blocks.append(text_block("[[HIRING_INTEL_AUTO_START]]"))
    blocks.append(heading_block("💼 HIRING INTELLIGENCE ..."))
    # ... hiring content ...
    blocks.append(text_block("[[HIRING_INTEL_AUTO_END]]"))
    return blocks
```

If markers are NOT included in the output, add them.

### 5F. Sanitize output before returning

**ADD** at the end of the hiring block generation:

```python
# Sanitize all blocks before returning — strip any error artifacts
blocks = sanitize_blocks(blocks)
```

---

## STEP 6: VALIDATE ALL CHANGES

```bash
# Syntax validation
ssh tony "cd /home/ubuntu/clawd/scripts && python3 -c '
import ast
for f in [\"founder-intel-pipeline.py\", \"funding-intel-brief.py\", \"hiring_intel_module.py\",
          \"domain_resolver.py\", \"qa_validator.py\", \"content_sanitizer.py\",
          \"canonical_template.py\", \"resilient_api.py\"]:
    ast.parse(open(f).read())
    print(f\"  ✓ {f}\")
print(\"All scripts parse OK\")
'"
```

```bash
# Import validation — make sure the new modules can be imported
ssh tony "cd /home/ubuntu/clawd/scripts && python3 -c '
from domain_resolver import resolve_domain, KNOWN_DOMAINS
from canonical_template import normalize_company_slug, generate_report_key
from content_sanitizer import sanitize_text, sanitize_investor_list
from resilient_api import resilient_call, CircuitBreaker
print(f\"  Known domains: {len(KNOWN_DOMAINS)}\")
print(f\"  resolve_domain(KAST) = {resolve_domain(\"KAST\")}\")
print(f\"  resolve_domain(LAYERZERO) = {resolve_domain(\"LAYERZERO\")}\")
print(f\"  normalize_company_slug(USD AI PERMIAN LABS) = {normalize_company_slug(\"USD AI PERMIAN LABS\")}\")
print(\"All imports OK\")
'"
```

---

## STEP 7: DRY-RUN TEST

Test with a known-failing company (KAST was the original F-10 failure):

```bash
ssh tony "cd /home/ubuntu/clawd && python3 scripts/founder-intel-pipeline.py --dry-run --company 'KAST'"
# Expected: domain=kast.xyz (NOT kast.com)
```

```bash
ssh tony "cd /home/ubuntu/clawd && python3 scripts/founder-intel-pipeline.py --dry-run --company 'LAYERZERO'"
# Expected: domain=layerzero.network (NOT layerzero.com)
```

```bash
ssh tony "cd /home/ubuntu/clawd && python3 scripts/founder-intel-pipeline.py --dry-run --company 'CROSSOVER MARKETS'"
# Expected: domain=crossovermarkets.com, no standalone page created
```

---

## STEP 8: RUN QA VALIDATOR ON RECENT PAGES

After deployment, run the QA validator on all pages from the last 48 hours to catch existing bad pages:

```bash
ssh tony "cd /home/ubuntu/clawd/scripts && NOTION_TOKEN=\$NOTION_TOKEN python3 qa_validator.py --recent 48"
```

This will update QA STATUS on all recent pages based on the new, stricter checks. Pages that previously showed PASS will now correctly show WARN or FAIL if they have:
- Empty COMPANY field
- $0 amounts
- Error artifacts in body
- "solid VC backing" with no investors listed
- Template fill cascade (4+ unfilled sections)
- M&A / token sale events
- Multiple unfilled placeholder patterns

---

## STEP 9: CLEAN ERROR ARTIFACTS FROM EXISTING PAGES

Run the content sanitizer on pages that have known error artifacts:

```bash
# Pages with DuckDuckGo/Grok errors visible in content
ssh tony "cd /home/ubuntu/clawd/scripts && python3 content_sanitizer.py --page-id 31ef30f9-bdff-8107"  # CREMA FINANCE
ssh tony "cd /home/ubuntu/clawd/scripts && python3 content_sanitizer.py --page-id 31ef30f9-bdff-81fe"  # LAYERZERO
ssh tony "cd /home/ubuntu/clawd/scripts && python3 content_sanitizer.py --page-id 31ef30f9-bdff-81ef"  # NOVIG
```

---

## STEP 10: COMMIT ON TONY

```bash
ssh tony "cd /home/ubuntu/clawd && \
  git add scripts/founder-intel-pipeline.py scripts/funding-intel-brief.py scripts/hiring_intel_module.py \
          scripts/domain_resolver.py scripts/qa_validator.py scripts/content_sanitizer.py \
          scripts/canonical_template.py scripts/resilient_api.py && \
  git commit -m 'Deploy audit fixes F-01 through F-17 + 5 support modules

Fixes:
- F-01: Disable standalone OUTREACH TARGET page creation
- F-02: Move archived-dup refs to QA ISSUES property
- F-03: Sanitize investor lists (filter person-name artifacts)
- F-04: Suppress DuckDuckGo errors from hiring signals
- F-05: Handle Grok API timeouts gracefully
- F-06: Normalize REPORT KEY to v3 format + legacy matching
- F-08: Deduplicate outreach channel recommendations
- F-09: Amount tolerance matching (±5%)
- F-10: Domain resolver + post-scrape validation
- F-11: Pre-creation filter ($0, M&A, token sales)
- F-12: QA validator content quality checks
- F-14: Conditional outreach angles (no fabricated claims)
- F-15: Dynamic vertical classification from description
- F-16: Round type passthrough to outreach template
- F-17: Entity resolution before REPORT KEY generation

New modules: domain_resolver, qa_validator, content_sanitizer,
canonical_template, resilient_api'"
```

---

## ROLLBACK PLAN

If anything breaks:

```bash
ssh tony "cd /home/ubuntu/clawd/scripts && \
  cp founder-intel-pipeline.py.bak.20260309 founder-intel-pipeline.py && \
  cp funding-intel-brief.py.bak.20260309 funding-intel-brief.py && \
  cp hiring_intel_module.py.bak.20260309 hiring_intel_module.py && \
  echo 'Rolled back to pre-deployment state'"
```

---

## BUG REFERENCE

| Bug ID | Severity | File | Description |
|--------|----------|------|-------------|
| F-01 | CRITICAL | founder-intel-pipeline.py | Creates redundant standalone OUTREACH TARGET pages for every event |
| F-02 | HIGH | funding-intel-brief.py | "Archived duplicate:" references embedded in canonical page bodies |
| F-03 | HIGH | funding-intel-brief.py | Investor lists contaminated with person-name artifacts |
| F-04 | MEDIUM | hiring_intel_module.py | DuckDuckGo transport errors emitted as hiring signals |
| F-05 | MEDIUM | hiring_intel_module.py | Grok API timeout errors embedded in competitor benchmarks |
| F-06 | MEDIUM | funding-intel-brief.py | REPORT KEY format inconsistency (legacy vs v3) |
| F-07 | LOW | hiring_intel_module.py | Generic "AI Crypto" vertical classification for all companies |
| F-08 | LOW | founder-intel-pipeline.py | Duplicate outreach channel recommendations |
| F-09 | HIGH | funding-intel-brief.py | No amount tolerance matching in dedup (BLUPRYNT $4.2M vs $4.25M) |
| F-10 | HIGH | founder-intel-pipeline.py | Domain scraper resolves wrong websites (KAST, LAYERZERO, etc.) |
| F-11 | CRITICAL | funding-intel-brief.py | $0/"undisclosed" amounts bypass dedup, create phantom duplicates |
| F-12 | CRITICAL | qa_validator.py | QA STATUS=PASS on pages with zero actionable content |
| F-13 | HIGH | founder-intel-pipeline.py | Domain scraper resolves wrong company (LAYERZERO power systems) |
| F-14 | HIGH | founder-intel-pipeline.py | Outreach angles fabricate "solid VC backing" when no investors listed |
| F-15 | MEDIUM | hiring_intel_module.py | Hiring predictions use static template, not real intelligence |
| F-16 | MEDIUM | founder-intel-pipeline.py | Round type always shows UNKNOWN in enriched pages |
| F-17 | MEDIUM | funding-intel-brief.py | No entity resolution — company name variations create duplicates |

---

## KNOWN DOMAINS TABLE

The `domain_resolver.py` module includes verified domain mappings for these companies. If the pipeline encounters any of these, it will use the correct domain instead of defaulting to `.com`:

| Company | Correct Domain | Wrong Domain |
|---------|---------------|--------------|
| KAST | kast.xyz | kast.com (Canadian science org) |
| LAYERZERO | layerzero.network | layerzero.com (power systems hardware) |
| BACKPACK | backpack.exchange | backpack.com |
| INTERSTATE | interstate.so | interstate.com (trucking/logistics) |
| CREMA FINANCE | crema.finance | crema.com |
| IZUMI FINANCE | izumi.finance | izumi.com |
| NOVIG | novig.bet | novig.com |
| ARQ | arq.network | arq.com |
| USD.AI | usd.ai | usdai.com |
| TAPIOCA DAO | tapioca.xyz | tapioca.com |
| EUCLID PROTOCOL | euclidprotocol.com | euclid.com |
| HELIOS FINANCE | helios.finance | helios.com |
| AKAVE | akave.ai | akave.com |
| PROBABLE | probable.bet | probable.com |

Plus 20+ well-known crypto companies (Solana, Uniswap, dYdX, MakerDAO, etc.).

---

## COMPANY ALIASES TABLE

The `canonical_template.py` module includes entity aliases to prevent duplicate pages from company name variations:

| Alias | Canonical Slug |
|-------|---------------|
| usd-ai-permian-labs | usd-ai |
| usdai-permian-labs | usd-ai |
| usdai | usd-ai |
| permian-labs | usd-ai |
| layerzero-labs | layerzero |
| izumi-finance | izumi |
| crema-finance | crema |

---

## SUCCESS CRITERIA

After deployment, verify:

1. `python3 founder-intel-pipeline.py --dry-run --company "KAST"` resolves to `kast.xyz`
2. `python3 founder-intel-pipeline.py --dry-run --company "LAYERZERO"` resolves to `layerzero.network`
3. No new standalone OUTREACH TARGET pages are created (only canonical page updates)
4. `python3 qa_validator.py --recent 48` shows FAIL/WARN on known-bad pages (not PASS)
5. Investor lists don't contain person-name artifacts
6. Outreach angles don't claim "solid VC backing" when no investors are listed
7. DuckDuckGo errors don't appear as hiring signals
8. Grok API timeouts produce clean fallback text, not raw error strings
