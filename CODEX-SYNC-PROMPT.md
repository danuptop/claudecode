# CODEX SYNC PROMPT — Full Repository Handoff

**Date:** March 9, 2026
**Source branch:** `claude/audit-handoff-package-cp4He` on `danuptop/claudecode`
**Commits:** 12 (634e8e8..cfc1503)
**Test suite:** 104 tests, all passing

---

## WHAT THIS IS

This branch contains the complete output of a forensic audit + hardening sprint on the Up Top Search "Report Base" pipeline. It covers:

1. **Audit artifacts** — two audit reports documenting 11 original findings (F-01–F-11) and 12 follow-up findings (A-01–A-12), plus a final system audit with 12 additional findings
2. **Bug fixes** — patches to 3 production scripts on Tony + fixes to 4 existing support scripts
3. **12 new modules** — a complete pipeline rewrite with dedup, locking, monitoring, and a unified single-pass architecture
4. **104 unit tests** — covering all pure-function logic across 10 test files
5. **Deployment documentation** — step-by-step instructions for Tony deployment

---

## HOW TO SYNC

### Option A: Pull the branch directly

```bash
cd /path/to/claudecode
git fetch origin claude/audit-handoff-package-cp4He
git merge origin/claude/audit-handoff-package-cp4He
```

### Option B: Cherry-pick commit groups

```bash
# Group 1: Audit reports (Notion-side cleanups documented, no code)
git cherry-pick 634e8e8..ba8dde6

# Group 2: Deployment prompt + production scripts + patch specs
git cherry-pick cb62734..fabeb74

# Group 3: Independent audit review + all code fixes
git cherry-pick bf0de69..d4d8172

# Group 4: 12 new modules + tests
git cherry-pick eda1cf0

# Group 5: Final audit + docs fixes
git cherry-pick c5de2d2..cfc1503
```

---

## FILE MANIFEST

### Audit Documents (read-only reference)

| File | Lines | Purpose |
|------|-------|---------|
| `audit/AUDIT-REPORT-2026-03-08.md` | 271 | Original forensic audit: 11 findings (F-01–F-11), 22 page archives, data fixes |
| `audit/HANDOFF-AUDIT-REVIEW-2026-03-09.md` | 210 | Independent review: 12 findings (A-01–A-12) on the handoff package itself |
| `audit/FINAL-SYSTEM-AUDIT-2026-03-09.md` | 153 | Final system audit: 12 findings across code/tests/docs, 6 fixed, 6 acknowledged |

### Deployment Instructions

| File | Lines | Purpose |
|------|-------|---------|
| `CODEX-DEPLOYMENT-PROMPT.md` | 553 | Step-by-step deployment guide for Tony. Includes patch specs for 3 production scripts, integration snippets for all new modules, validation commands, and Notion schema reference. |

### Patch Specs (descriptive — apply manually to Tony's scripts)

| File | Lines | Target Script on Tony | Fixes |
|------|-------|-----------------------|-------|
| `patches/founder-intel-pipeline-dedup-fix.py` | 105 | `founder-intel-pipeline.py` | F-01: stop standalone page creation, add dedup, add REPORT KEY |
| `patches/funding-intel-brief-hardening.py` | 144 | `funding-intel-brief.py` | F-02, F-03, F-06, F-09, F-11: investor sanitization, v3 REPORT KEY, amount tolerance, $0 guard |
| `patches/hiring_intel_module-idempotency.py` | 85 | `hiring_intel_module.py` | F-04, F-05: suppress DuckDuckGo/Grok error-as-signal |

### Production Scripts (deploy to `/home/ubuntu/clawd/scripts/`)

**Core pipeline modules (existing, fixed):**

| File | Lines | What Changed |
|------|-------|-------------|
| `scripts/qa_validator.py` | 510 | Fixed Notion API filter format (A-07), added `pre_write_validate()` gate (A-02) |
| `scripts/content_sanitizer.py` | 422 | Added local `ERROR_RE` definition (A-11), was previously undefined |
| `scripts/resilient_api.py` | 267 | Fixed exception handling to catch `requests.exceptions` types (A-08) |
| `scripts/canonical_template.py` | 423 | Added company alias resolution via `canonicalize_company()`, updated `generate_report_key()` |

**New infrastructure modules:**

| File | Lines | Purpose |
|------|-------|---------|
| `scripts/pipeline_lock.py` | 199 | `flock`-based single-instance pipeline guard. Prevents concurrent runs. `@single_instance` decorator + `pipeline_lock()` context manager. |
| `scripts/source_dedup_cache.py` | 179 | JSON persistent cache tracking processed source URLs and company+amount fingerprints. Auto-prunes entries >90 days. |
| `scripts/page_registry.py` | 134 | Maps deterministic fingerprints `(company, round_type, amount_bucket)` → Notion page IDs for idempotent page creation. |
| `scripts/event_queue.py` | 253 | File-based persistent event queue with pending/active/done/failed directories, at-least-once delivery, 3-attempt retry, stale event recovery. |
| `scripts/company_aliases.json` | 38 | Maps company name variants to canonical slugs (e.g., "okx exchange" → "okx", "usd.ai" → "usdai"). |

**New data quality modules:**

| File | Lines | Purpose |
|------|-------|---------|
| `scripts/amount_parser.py` | 210 | Two-pass amount extraction: regex for standard formats ($31M, $1.5B), LLM fallback (Claude Haiku) for ambiguous cases (valuation vs round, undisclosed). |
| `scripts/notion_client_wrapper.py` | 223 | Read/write Notion token separation (`NOTION_READ_TOKEN`/`NOTION_WRITE_TOKEN`), JSONL audit log for all write operations. |

**New operations modules:**

| File | Lines | Purpose |
|------|-------|---------|
| `scripts/qa_dashboard.py` | 247 | SQLite-based QA monitoring. `record()`, `summary()`, `failures()`, `error_trends()`, `api_health_summary()`. CLI interface for ad-hoc queries. |
| `scripts/enrichment_backfill.py` | 195 | Scans Report Base for FUNDRAISING INTEL pages missing outreach/hiring markers older than N hours. Optionally enqueues them for re-enrichment. |

**New architecture:**

| File | Lines | Purpose |
|------|-------|---------|
| `scripts/unified_pipeline.py` | 552 | Single-pass pipeline replacing the two-phase `funding-intel-brief → founder-intel-pipeline` flow. Steps: dedup → sanitize → enrich → build → QA gate → atomic write → post-write bookkeeping. Supports `--dry-run`, `--from-queue`, `--skip-enrichment`. |

### Test Suite

| File | Lines | Module Tested | Tests |
|------|-------|--------------|-------|
| `tests/test_canonical_template.py` | 229 | canonical_template.py | 27 (slugify, report key, amount parsing, alias resolution, page structure, block/property building) |
| `tests/test_content_sanitizer.py` | 159 | content_sanitizer.py | 16 (investor sanitization, text/block cleaning, error detection) |
| `tests/test_qa_validator.py` | 186 | qa_validator.py | 14 (QAResult, page validation, pre-write gate, markers) |
| `tests/test_resilient_api.py` | 94 | resilient_api.py | 8 (CircuitBreaker states, retry/fallback, open circuit) |
| `tests/test_amount_parser.py` | 68 | amount_parser.py | 10 (all regex patterns, edge cases, round type extraction) |
| `tests/test_event_queue.py` | 68 | event_queue.py | 6 (push/pop/ack/nack, empty queue, status) |
| `tests/test_page_registry.py` | 82 | page_registry.py | 7 (register/get/unregister, persistence, fingerprint) |
| `tests/test_source_dedup_cache.py` | 65 | source_dedup_cache.py | 6 (URL/fingerprint dedup, persistence, size) |
| `tests/test_pipeline_lock.py` | 44 | pipeline_lock.py | 4 (acquire/release, reentrant block, manual release) |
| `tests/test_qa_dashboard.py` | 49 | qa_dashboard.py | 4 (record, failures, empty state) |

### Configuration

| File | Lines | Purpose |
|------|-------|---------|
| `.gitignore` | 13 | Excludes `__pycache__/`, `*.pyc`, `.pytest_cache/`, `*.bak`, `.env`, `*.db`, `*.log`, `/data/`, `/queues/`, `venv/` |

---

## DEPLOYMENT SEQUENCE FOR TONY

This is the condensed deployment order. Full details are in `CODEX-DEPLOYMENT-PROMPT.md`.

### Phase 1: Pause + Backup

```bash
# On Tony — pause all cron jobs
crontab -e  # Comment out founder-intel-pipeline, funding-intel-brief, outreach-pipeline-trigger

# Back up existing scripts
cd /home/ubuntu/clawd/scripts
for f in founder-intel-pipeline.py funding-intel-brief.py hiring_intel_module.py; do
    cp "$f" "${f}.bak.20260309"
done
```

### Phase 2: Transfer all new modules

```bash
# From local machine
scp scripts/{qa_validator,content_sanitizer,resilient_api,canonical_template}.py \
    tony:/home/ubuntu/clawd/scripts/

scp scripts/{unified_pipeline,enrichment_backfill,event_queue,amount_parser}.py \
    tony:/home/ubuntu/clawd/scripts/

scp scripts/{page_registry,source_dedup_cache,pipeline_lock,qa_dashboard}.py \
    tony:/home/ubuntu/clawd/scripts/

scp scripts/{notion_client_wrapper}.py \
    tony:/home/ubuntu/clawd/scripts/

scp scripts/company_aliases.json \
    tony:/home/ubuntu/clawd/scripts/
```

### Phase 3: Apply patches to 3 production scripts

Apply the changes described in `CODEX-DEPLOYMENT-PROMPT.md` (SCRIPT 1/2/3 sections) to:
- `founder-intel-pipeline.py` — 4 changes (guard standalone creation, idempotent markers, add REPORT KEY, dedup check)
- `funding-intel-brief.py` — 5 changes (investor sanitization, QA ISSUES for dedup notes, v3 REPORT KEY, amount tolerance, $0 guard)
- `hiring_intel_module.py` — 2 changes (suppress DuckDuckGo errors, handle Grok timeouts)

### Phase 4: Validate

```bash
# Syntax check all scripts
for f in /home/ubuntu/clawd/scripts/*.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK: $f" || echo "FAIL: $f"
done

# Dry-run legacy pipeline
python3 scripts/funding-intel-brief.py --dry-run --company "CROSSOVER MARKETS"

# Dry-run unified pipeline
python3 scripts/unified_pipeline.py --company "CROSSOVER MARKETS" --amount 31000000 \
    --round-type SEED --dry-run

# Run test suite (if pytest available)
python3 -m pytest tests/ -v
```

### Phase 5: Resume + Monitor

```bash
# Re-enable cron jobs
crontab -e  # Uncomment the pipeline cron entries

# Monitor first run
python3 scripts/qa_dashboard.py summary
python3 scripts/qa_validator.py --recent 2
```

---

## ENVIRONMENT VARIABLES

The new modules expect these environment variables (on Tony):

| Variable | Required By | Purpose |
|----------|-------------|---------|
| `NOTION_TOKEN` | qa_validator, content_sanitizer, enrichment_backfill | Existing Notion API token (already set on Tony) |
| `NOTION_READ_TOKEN` | notion_client_wrapper | Read-only Notion token (optional — falls back to `NOTION_TOKEN`) |
| `NOTION_WRITE_TOKEN` | notion_client_wrapper | Write Notion token (optional — falls back to `NOTION_TOKEN`) |
| `GROK_API_KEY` | resilient_api, amount_parser | Grok/xAI API key (already set on Tony) |
| `ANTHROPIC_API_KEY` | amount_parser | Claude API key for LLM amount parsing fallback (optional — regex-only if unset) |
| `REPORT_BASE_DB` | qa_validator, enrichment_backfill | Notion database ID `902d47be-68c0-4da8-832a-a52272fc7b39` (already set on Tony) |

---

## ARCHITECTURE OVERVIEW

### Before (two-phase, buggy)

```
funding-intel-brief.py ──creates──→ FUNDRAISING INTEL page (skeleton)
                                         ↓ (cron delay)
founder-intel-pipeline.py ──enriches──→ markers replaced
                          ──creates──→ OUTREACH TARGET page (BUG: duplicates)
```

### After (single-pass, unified)

```
unified_pipeline.py
  ├─ source_dedup_cache ──→ skip if URL/fingerprint seen
  ├─ page_registry ──→ skip if page already exists for this deal
  ├─ pipeline_lock ──→ single instance only
  ├─ amount_parser ──→ extract amount (regex + LLM fallback)
  ├─ canonical_template ──→ build full page (skeleton + enrichment)
  ├─ content_sanitizer ──→ strip error artifacts
  ├─ qa_validator ──→ pre-write QA gate (FAIL = abort)
  ├─ notion_client_wrapper ──→ atomic write with audit log
  └─ qa_dashboard ──→ record result for monitoring
```

The legacy two-phase flow (`funding-intel-brief.py` + `founder-intel-pipeline.py`) continues to work with the patches applied. The unified pipeline is an **opt-in replacement** that can run alongside or fully replace the legacy flow.

---

## KNOWN LIMITATIONS (from final audit)

These are documented in `audit/FINAL-SYSTEM-AUDIT-2026-03-09.md`:

1. **External API functions untested** — `search_duckduckgo()`, `call_grok()`, Notion API calls require live connections; should be mocked in future
2. **CLI entry points untested** — 9/10 `main()` functions not covered
3. **Thread safety** — `canonical_template.py` alias cache is single-threaded only (fine for current cron-based usage)
4. **Non-atomic Notion operations** — `content_sanitizer.py` block ops are separate API calls (inherent Notion limitation)
5. **Grok response validation** — `call_grok()` assumes specific JSON structure; needs try/except guard
6. **`unified_pipeline.py` untested** — requires full stack integration test with Notion

---

## COMMIT LOG

```
cfc1503 Fix critical deployment gap: SCP command missing 9 modules
c5de2d2 Final system audit: fix 4 defects, add audit report (104 tests passing)
eda1cf0 Add 12 pipeline improvements with tests (101 passing)
d4d8172 Add .gitignore for Python cache files
e65ab1b Fix all 12 audit findings (A-01 through A-12)
bf0de69 Add independent audit review of outreach pipeline handoff package
fabeb74 Add F-11: $0 bad-parse amount bypasses all dedup logic
d16ec02 Add 4 production scripts + schema/data fixes across Report Base
cb62734 Add comprehensive Codex deployment prompt for Tony script patches
ba8dde6 Audit: Archive AKAVE outreach page, migrate ARQ CEO data, update counts
994a6f2 Audit: Complete full database sweep — 19 pages archived, OKX/ARQ triage
634e8e8 Audit: Report Base FUNDRAISING INTEL dedup + redundancy fixes
```

---

## VERIFICATION

After syncing, confirm:

```bash
# All 104 tests pass
python3 -m pytest tests/ -v

# All scripts parse cleanly
for f in scripts/*.py; do
    python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK: $f" || echo "FAIL: $f"
done

# JSON valid
python3 -m json.tool scripts/company_aliases.json > /dev/null && echo "aliases OK"
```
