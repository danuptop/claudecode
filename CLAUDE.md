# CLAUDE.md — Up Top Search Pipeline

## Project Overview

This repo (`danuptop/claudecode`) contains the production pipeline scripts, patches, and tooling for **Up Top Search's Report Base** — a Notion-backed intelligence system that monitors crypto/fintech fundraising events and generates enriched reports with outreach intel, hiring signals, and investor analysis.

## Architecture

### Pipeline Topology

```
funding-intel-brief.py (ENTRY POINT)
  → Creates/updates canonical TYPE=FUNDRAISING INTEL pages in Notion
  → Sets REPORT KEY, RUN ID, SOURCE SKILL
  → Uses: canonical_template.py, content_sanitizer.py, qa_validator.py

founder-intel-pipeline.py (triggered by outreach-pipeline-trigger.py)
  → Reads canonical pages from funding-intel-brief
  → Appends outreach content via [[OUTREACH_INTEL_AUTO_START/END]] markers
  → Appends hiring content via [[HIRING_INTEL_AUTO_START/END]] markers
  → Uses: hiring_intel_module.py, content_sanitizer.py, qa_validator.py

hiring_intel_module.py (imported by founder-intel-pipeline)
  → Generates hiring intel blocks
  → Calls DuckDuckGo + Grok API for signals
  → Uses: resilient_api.py, content_sanitizer.py

crypto-fundraising-monitor (watcher-guru-notion-writer.js)
  → Creates TYPE=SIGNAL PACK pages (separate pipeline, Node.js)
```

### Infrastructure

- **Production server:** Tony (SSH alias `tony`, scripts at `/home/ubuntu/clawd/scripts/`)
- **Database:** Notion — Report Base (`902d47be-68c0-4da8-832a-a52272fc7b39`)
- **Data Source ID:** `598bb1d8-26cb-4e2a-8b4f-8b80e1be7116`
- **External APIs:** DuckDuckGo (hiring search), Grok/X.AI (analysis), Clay (enrichment), Icebreaker MCP (warm intros)
- **Language:** Python 3.x (pipeline scripts), Node.js (crypto monitor)

## Notion Schema (Report Base)

| Property | Type | Purpose |
|----------|------|---------|
| ENTRY | Title | Page title |
| TYPE | Select | `FUNDRAISING INTEL`, `OUTREACH TARGET`, `SIGNAL PACK` |
| SOURCE SKILL | Rich Text | Pipeline that created the page |
| REPORT KEY | Rich Text | Dedup key. v3: `fundraising-intel:v3:{slug}:{amount}` |
| RUN ID | Rich Text | Pipeline execution ID |
| QA STATUS | Select | `PASS`, `WARN`, `FAIL`, `SKIP` |
| QA ISSUES | Rich Text | Audit notes, dedup notes |
| POC | People | Team members assigned |
| DATE | Date | Event detection date |
| LAST AUDITED AT | Date | Last audit timestamp |
| COMPANY | Rich Text | Company name (for dedup) |
| ROUND AMOUNT | Number | Dollar amount (for dedup) |

## Coding Conventions

### REPORT KEY Format (v3 — ALWAYS use this)
```
fundraising-intel:v3:{slug}:{amount}
signal-pack:v1:{YYYY-MM-DD}
outreach-intel:{slug}:{amount}
```
Generate slugs with `canonical_template.slugify()` — lowercase, hyphens, no special chars.

### Marker-Based Content Replacement
Auto-generated sections use marker pairs. Content between markers is **replaced on each run** (idempotent), never appended:
```
[[OUTREACH_INTEL_AUTO_START]]
... replaced content ...
[[OUTREACH_INTEL_AUTO_END]]

[[HIRING_INTEL_AUTO_START]]
... replaced content ...
[[HIRING_INTEL_AUTO_END]]
```

### Error Handling
- **NEVER** emit transport errors as data/signals (e.g., DuckDuckGo timeout as a "hiring signal")
- Use `resilient_api.py` wrappers (`search_duckduckgo()`, `call_grok()`) with circuit breakers
- Return empty lists or clean fallback strings on API failure, never raw exception text
- Run `content_sanitizer.sanitize_blocks()` before any Notion write

### Dedup Rules
1. Always query by REPORT KEY before creating a page
2. Match both v3 and legacy key formats in dedup queries
3. Use `amounts_match(a, b, tolerance=0.05)` for ±5% amount tolerance
4. $0 / undisclosed amounts → search by COMPANY name alone, skip if match found
5. Never create a page with empty REPORT KEY — bypasses all dedup

### QA Validation
- Call `qa_validator.post_write_hook(page_id)` after every pipeline write
- QA checks: REPORT KEY populated, no error text in body, markers paired, investor count, content length
- FAIL = error text in body, missing REPORT KEY, empty content
- WARN = missing markers, <3 investors, no POC, mcp_unavailable noise
- Move dedup/archive notes to QA ISSUES property, never page body

### Investor List Sanitization
Always run `content_sanitizer.sanitize_investor_list()` before writing investor lists. Filters:
- Person-name artifacts: `"Alex Wilson (co-founder"` → removed
- Orphaned fragments: `"Cyclops)"` → removed
- Role-only entries: `"co-founder"` → removed
- Deduplicates preserving order

## Module Reference

| Module | Purpose | Import From |
|--------|---------|-------------|
| `canonical_template.py` | Page template, REPORT KEY generation, amount parsing | funding-intel-brief |
| `content_sanitizer.py` | Error stripping, investor cleaning, MCP section cleanup | all pipeline scripts |
| `qa_validator.py` | Post-write QA gate, sets QA STATUS | funding-intel-brief, founder-intel-pipeline |
| `resilient_api.py` | Retry + circuit breaker for DuckDuckGo, Grok, Clay, Icebreaker | hiring_intel_module, founder-intel-pipeline |

## Deployment Flow

1. Develop/patch in this repo (`danuptop/claudecode`)
2. Back up existing scripts on Tony before applying changes
3. Apply patches to `/home/ubuntu/clawd/scripts/` on Tony
4. Validate syntax: `python3 -c 'import ast; ast.parse(open("script.py").read())'`
5. Dry-run test: `python3 scripts/funding-intel-brief.py --dry-run --company "TEST"`
6. Run "up top sync" to propagate across codex/claude/tony

## Common Tasks

### Adding a new pipeline script
1. Follow the module pattern in `scripts/` — docstring with usage, deployment path, imports
2. Use `resilient_api` for any external API calls
3. Use `content_sanitizer` before any Notion writes
4. Call `qa_validator.post_write_hook()` after writes
5. Generate REPORT KEYs via `canonical_template.generate_report_key()`
6. Build page properties via `canonical_template.build_page_properties()`

### Auditing Report Base
1. Query recent pages and run `qa_validator.py --recent 24`
2. Check for duplicate REPORT KEYs, stacked markers, error artifacts
3. Archive duplicates: update title with `[ARCHIVED — DUPLICATE OF CANONICAL]`, set QA STATUS=SKIP
4. Document findings in `audit/` directory

### Debugging dedup failures
1. Check REPORT KEY format — is it v3?
2. Check if amount is $0 (bad parse) — triggers F-11 bypass
3. Check if legacy key format doesn't match v3 query
4. Use `amounts_match()` to verify tolerance matching

## Known Issues & Fixes Applied

See `audit/AUDIT-REPORT-2026-03-08.md` for full details. Key fixes:
- **F-01** (CRITICAL): founder-intel-pipeline was creating standalone OUTREACH TARGET pages alongside canonical append — disabled
- **F-02/F-03**: Investor lists had person-name artifacts — sanitizer added
- **F-04/F-05**: DuckDuckGo/Grok errors were being emitted as data — suppressed
- **F-06**: REPORT KEY format inconsistency — normalized to v3
- **F-09**: Amount mismatches causing dupes — ±5% tolerance added
- **F-11** (CRITICAL): $0 bad-parse amounts bypass all dedup — company-name fallback added

## Git Conventions

- Branch naming: `claude/{description}-{sessionId}`
- Commit messages: describe the fix, reference finding IDs (F-01, F-02, etc.)
- Always commit patch specs, new modules, and audit reports together
- Push with `-u origin <branch-name>`
