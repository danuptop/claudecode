# MikeIsHiring — QA Audit & Gap Analysis

**Date:** March 8, 2026
**Source QA Report:** [MikeIsHiring QA Report — Run 20260306T194516Z](https://www.notion.so/31ef30f9bdff81058c28d8430a17e51a)
**Audit Branch:** `claude/audit-search-report-duplication-TNk3J` (prior work)
**QA Branch:** `claude/audit-mikeishiring-qa-v623X` (this analysis)

---

## 1. QA REPORT SUMMARY (Run 20260306T194516Z)

The automated QA runner reports **Overall: PASS** with 0 findings at any severity level. All 14 endpoint health checks return expected status codes. SLA trends show 100% pass rate across 15 sample runs on all endpoints.

**On the surface, this looks healthy. Underneath, there are significant gaps.**

---

## 2. DATA QUALITY GAPS (from QA report metrics)

The QA report itself surfaces metrics that should be flagged but aren't:

| Metric | Value | Severity | Assessment |
|--------|-------|----------|------------|
| Jobs missing location | **42.67%** | HIGH | Nearly half of all jobs have no location data. This undermines any geo-based filtering or market analysis. |
| Jobs blank snippet | **51.67%** | HIGH | More than half of all job listings have empty description snippets. Hiring intelligence built on top of this is unreliable. |
| Jobs salary coverage | **34.43%** | MEDIUM | Only 1 in 3 jobs has salary data. Salary-based analytics are based on a minority sample. |
| Company website coverage | **24.37%** | HIGH | 3 out of 4 companies have no website URL. This is the root cause of F-10 (garbage POC scraping) — the scraper falls back to news article chrome when it can't find the company domain. |
| Company description coverage | **66.8%** | LOW | Acceptable but 1 in 3 companies still lacks a description. |
| Latest job timestamp | **2026-03-02** | MEDIUM | Data is 4 days stale at report time (Mar 06). The job ingestion pipeline may have a silent failure or schedule gap since Mar 02. |

### Gap G-01: QA runner treats these metrics as informational, not actionable

The QA runner computes these percentages but does not apply thresholds or generate findings. A 42.67% missing-location rate and 51.67% blank-snippet rate should produce at minimum MEDIUM findings, but the report shows `{'low': 0, 'medium': 0, 'high': 0}`.

**Recommendation:** Add threshold-based alerting to the QA runner:
- `jobs_missing_location > 30%` → MEDIUM finding
- `jobs_blank_snippet > 40%` → MEDIUM finding
- `company_website_coverage < 30%` → MEDIUM finding
- `latest_job_timestamp` > 3 days old → HIGH finding (data freshness SLA breach)

---

## 3. ENDPOINT HEALTH GAPS

### Gap G-02: No response schema validation

All 14 endpoints return expected HTTP status codes, but the QA runner does not validate response payloads. An endpoint can return `200 OK` with an empty body, malformed JSON, or stale data and still show `[PASS]`.

**Evidence:** The endpoints `api/v2/talent-explorer/reverse-match` and `api/v2/talent/search` both return **404** and are marked PASS (expected 404). This means these endpoints are either:
- Not yet implemented (stub routes returning 404 by design), or
- Broken and returning 404 instead of results

Either way, the QA runner should distinguish between "endpoint correctly rejects invalid requests" and "endpoint is non-functional."

**Recommendation:**
- For 200-expected endpoints: validate response contains expected top-level keys and non-zero result counts
- For 404-expected endpoints: document why 404 is correct (e.g., "requires POST body" vs "not implemented")

### Gap G-03: No latency thresholds or degradation detection

The SLA trends track `avg_ms` and `p95_ms` but don't alert on degradation. Key observations:

| Endpoint | avg_ms | p95_ms | Concern |
|----------|--------|--------|---------|
| `api/v2/talent-explorer/role/engineering` | 1872 | 3764 | p95 nearly 4 seconds — sluggish for a user-facing endpoint |
| `analytics-dashboard?artifact=job_market_pulse` | 777 | 2732 | p95 is 3.5x the average — high variance |
| `companies?action=stats` | 1139 | 3652 | p95 is 3.2x the average — high variance |
| `job-facets?days=30` | 1607 | 2065 | Consistently slow |

**Recommendation:** Add p95 latency thresholds:
- Warning at p95 > 2000ms for standard endpoints
- Critical at p95 > 5000ms for any endpoint
- Alert on p95/avg ratio > 3x (indicates intermittent failures or cold starts)

---

## 4. CROSS-REFERENCE WITH FORENSIC AUDIT (F-01 through F-11)

The forensic audit (branch `claude/audit-search-report-duplication-TNk3J`) found 11 bugs. Here's how each relates to the QA runner and what the QA runner missed:

| Finding | Severity | QA Runner Detection | Gap |
|---------|----------|-------------------|-----|
| **F-01** Standalone OUTREACH TARGET duplication | CRITICAL | Not detected | QA runner tests endpoints only — it doesn't check for duplicate pages in Notion |
| **F-02** Archived-dup refs in page bodies | HIGH | Not detected | No content-level checks on page quality |
| **F-03** Corrupt investor lists | HIGH | Not detected | No semantic validation of page content |
| **F-04** DuckDuckGo errors as hiring signals | MEDIUM | Not detected | QA runner doesn't read page content for error artifacts |
| **F-05** Grok timeout in benchmarks | MEDIUM | Not detected | Same as F-04 |
| **F-06** REPORT KEY format inconsistency | MEDIUM | Not detected | No schema consistency checks |
| **F-07** Generic "AI Crypto" vertical | LOW | Not detected | No content quality scoring |
| **F-08** Duplicate outreach channels | LOW | Not detected | No content dedup checks |
| **F-09** OKX 3-page amount mismatch | HIGH | Not detected | No cross-page dedup validation |
| **F-10** Garbage POC from scraper | MEDIUM | Not detected | No POC quality validation |
| **F-11** $0 amount bypasses dedup | CRITICAL | Not detected | No zero-amount anomaly detection |

### Gap G-04: QA runner operates at the API/endpoint level only

The QA runner is an infrastructure health monitor, not a data quality validator. It tells you "the API is up" but not "the data behind it is correct." All 11 forensic findings were invisible to the QA runner.

**Recommendation:** The `qa_validator.py` script (already written, pending deployment) fills this gap for individual pages. Additionally, the QA runner should be extended with:
- Cross-page dedup checks: query for companies with >1 active page
- $0 amount anomaly detection: flag FUNDRAISING INTEL pages with amount = 0
- Error artifact scanning: sample N random pages and check for error text patterns

---

## 5. ALERT DELIVERY GAP

### Gap G-05: Notion output not configured

From the QA report:
```
configured=False attempted=False sent=False status_code=None reason=missing_config
```

The QA runner successfully generates reports but cannot push them to Notion. The JSON payload is saved locally (`/Users/daneskow/Documents/New project/reports/mikeishiring/qa_notion_latest.json`) but never delivered.

**Impact:** QA reports exist only as local files. The team has no Notion-native visibility into automated QA results. This means:
- No historical trending in Notion
- No team notification of failures
- QA outputs are invisible to anyone who doesn't check the local filesystem

**Recommendation:** Configure the Notion output integration. The Report Base database already has `QA STATUS`, `QA ISSUES`, and `LAST AUDITED AT` properties (added during the forensic audit). Wire the QA runner to write its findings back to these properties.

---

## 6. PIPELINE FLOW ANALYSIS

### The MikeIsHiring Pipeline Topology

```
┌─────────────────────────┐
│   Data Ingestion        │
│   (jobs, companies)     │
│   3,166 jobs / 1,756 co │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│  funding-intel-brief.py │────▶│  Report Base (Notion)   │
│  Creates/updates        │     │  TYPE=FUNDRAISING INTEL  │
│  canonical pages        │     │  902d47be...             │
└──────────┬──────────────┘     └──────────┬──────────────┘
           │                               │
           │ triggers                      │ reads canonical
           ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│ outreach-pipeline-      │────▶│ founder-intel-pipeline  │
│ trigger.py              │     │ Appends via markers:    │
│                         │     │   OUTREACH + HIRING     │
└─────────────────────────┘     │ [BUG] Also creates      │
                                │   standalone pages      │
                                └──────────┬──────────────┘
                                           │ imports
                                           ▼
                                ┌─────────────────────────┐
                                │ hiring_intel_module.py   │
                                │ DuckDuckGo + Grok API   │
                                │ [BUG] Errors as signals  │
                                └─────────────────────────┘

┌─────────────────────────┐     ┌─────────────────────────┐
│ crypto-fundraising-     │────▶│  Report Base (Notion)   │
│ monitor (JS)            │     │  TYPE=SIGNAL PACK       │
│ watcher-guru writer     │     │  (separate pipeline)    │
└─────────────────────────┘     └─────────────────────────┘

┌─────────────────────────┐     ┌─────────────────────────┐
│  QA Runner              │────▶│  Local JSON file only   │
│  mikeishiring checks    │     │  Notion output NOT      │
│  (endpoints + metrics)  │     │  configured             │
└─────────────────────────┘     └─────────────────────────┘
```

### Gap G-06: No end-to-end pipeline monitoring

There is no telemetry or monitoring covering the full pipeline flow:
- No tracking of how many pages `funding-intel-brief.py` creates per run
- No tracking of how many pages `founder-intel-pipeline.py` creates per run
- No comparison of "expected pages" vs "actual pages created"
- No detection of the 2x page creation bug (F-01) through monitoring

The `RUN ID` property exists but is inconsistently populated. Without consistent RUN IDs, you cannot reconstruct which pipeline run produced which pages, or detect when a run creates unexpected duplicates.

---

## 7. REMEDIATION STATUS

### Completed (Notion-side)
- 22 duplicate/bad pages archived
- 4 canonical pages cleaned (investor lists, body refs)
- 3 $0 bad-parse pages archived
- Schema enriched (COMPANY, ROUND AMOUNT properties)
- Signal Pack REPORT KEYs backfilled
- Multiple pages upgraded to v3 REPORT KEY format

### Pending Deployment (Code-side on Tony)
- 3 script patches (founder-intel-pipeline, funding-intel-brief, hiring_intel_module)
- 4 new helper modules (qa_validator, content_sanitizer, resilient_api, canonical_template)
- **BLOCKED:** SSH access to Tony not available from sandboxed environment

### Not Yet Addressed

| Gap | Priority | Description |
|-----|----------|-------------|
| **G-01** | HIGH | QA runner needs threshold-based findings for data metrics |
| **G-02** | HIGH | Endpoint response schema validation missing |
| **G-03** | MEDIUM | No latency degradation alerting |
| **G-04** | HIGH | QA runner has no data quality layer (only infra health) |
| **G-05** | HIGH | Notion output not configured — QA results invisible to team |
| **G-06** | MEDIUM | No end-to-end pipeline run monitoring |
| **F-07** | LOW | Generic vertical classification (deferred — needs Grok prompt refactor) |
| **F-08** | LOW | Duplicate outreach channels (deferred) |
| **F-10** | MEDIUM | Garbage POC from news site scraping (deferred — needs scraper refactor) |

---

## 8. CRITICAL PATH

The highest-risk item is that **the root cause code fixes (F-01, F-11) are not yet deployed**. Every new pipeline run on Tony will:
1. Create new standalone OUTREACH TARGET duplicate pages (F-01)
2. Create new $0 phantom duplicate pages (F-11)
3. Embed DuckDuckGo/Grok errors into page content (F-04, F-05)

The Notion-side cleanups will be undone by the next pipeline run unless the code patches are applied.

### Recommended Priority Order
1. **Deploy script patches to Tony** (stops the bleeding)
2. **Configure QA runner Notion output** (makes QA visible)
3. **Add data quality thresholds to QA runner** (catches future regressions)
4. **Add response schema validation** (prevents false PASSes)
5. **Investigate job data freshness** (4-day gap since Mar 02)
6. **Address company website coverage** (24.37% — root cause of F-10)

---

## 9. QA RUNNER vs QA VALIDATOR COMPARISON

Two QA systems exist but serve different purposes:

| Aspect | QA Runner (mikeishiring) | QA Validator (qa_validator.py) |
|--------|--------------------------|-------------------------------|
| **Scope** | API endpoint health + aggregate data metrics | Individual Notion page content quality |
| **What it catches** | API downtime, HTTP errors | Error artifacts, missing properties, stacking bugs |
| **What it misses** | All data quality issues inside pages | Aggregate data coverage metrics |
| **Status** | Running, produces reports | Written, not yet deployed |
| **Output** | Local JSON file (Notion not configured) | Writes to page QA STATUS/QA ISSUES properties |
| **Integration** | Standalone scheduled runner | Post-write hook in pipeline scripts |

**Gap G-07: These two systems don't communicate.** The QA runner should invoke the QA validator as part of its checks, or at minimum read QA STATUS from pages to include page-level quality in its aggregate report.

---

## 10. SUMMARY

The MikeIsHiring QA report gives a **false sense of confidence**. It reports "Overall: PASS" while:
- 42.67% of jobs are missing location
- 51.67% of jobs have blank snippets
- 75.63% of companies have no website
- 22 duplicate/corrupt pages existed in the database (now archived)
- Error text was embedded in page content across 17+ pages
- The $0 bad-parse bug silently creates phantom duplicates every run
- QA results aren't even delivered to Notion

The automated QA runner validates that the APIs are up, but it does not validate that the data behind them is correct. The forensic audit found 11 bugs, none of which the QA runner could detect.

**Bottom line:** The QA runner needs a data quality layer, threshold-based alerting, and Notion output configuration. The code patches need to be deployed to Tony before the next pipeline run.

---

## 11. FIXES APPLIED DURING THIS AUDIT

### Code Bugs Fixed (8 total)

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `content_sanitizer.py` | `ERROR_RE` undefined — **NameError crash** | Added compiled regex at module level |
| 2 | `content_sanitizer.py` | Traceback regex under-matches at end of content | Greedy pattern matching indented lines |
| 3 | `canonical_template.py` | `SOURCE SKILL` as `rich_text` but schema is **Select** | Changed to `{"select": {...}}` |
| 4 | `canonical_template.py` | `QA STATUS` hardcoded `PASS` before validation | Changed to `PENDING` |
| 5 | `canonical_template.py` | Dead `date_str` variable | Removed |
| 6 | `qa_validator.py` | `validate_recent()` wrong filter type — **`--recent` broken** | `created_time` → `date` |
| 7 | `qa_validator.py` | New Notion client per recursive call | Reuse via `_notion` param |
| 8 | `resilient_api.py` | `except (ConnectionError, TimeoutError)` is dead code | Import `requests.exceptions` properly |

### Notion Page Fixes (12 pages)

- 7 REPORT KEYs upgraded from legacy to v3 format
- 4 DuckDuckGo error artifacts cleaned from hiring signals
- 10 pages got missing `[[HIRING_INTEL_AUTO_START/END]]` markers
- 6 pages got missing `[[OUTREACH_INTEL_AUTO_START/END]]` markers
- PROBABLE backfilled with RUN ID + SOURCE SKILL
- 3 false-PASS pages corrected to WARN

### Verified: Archived Pages Are Properly Deleted

All 22 "soft-archived" pages (title-renamed with `[ARCHIVED]`) were confirmed to have `deleted` metadata in the Notion API. They do not appear in active database views. The background audit agent's claim they were "still active" was incorrect — Notion's fetch tool can retrieve deleted pages by direct URL, but they are marked `deleted` and invisible in normal database queries.

---

## 12. HIRING DATA CHAIN — INTEGRATION FIXES (Applied Mar 09, 2026)

Four structural improvements to connect the hiring data pipeline across Report Base:

### 12a. HirePulse added to Report Base as DATA QUALITY entry

- **New page:** [`HIREPULSE — PLATFORM HEALTH & DATA QUALITY — MAR 06, 2026`](https://www.notion.so/31ef30f9bdff81058c28d8430a17e51a)
- **TYPE:** DATA QUALITY | **QA STATUS:** WARN
- **REPORT KEY:** `data-quality:hirepulse:2026-03-06`
- **RUN ID:** `20260306T194516Z`
- **SOURCE SKILL:** `hirepulse-qa-runner`
- **QA ISSUES:** 4 data quality gaps (51.7% blank snippets, 42.7% missing locations, 34.4% salary coverage, 24.4% company website coverage), Notion output not configured, 4-day data staleness
- **Content:** Run summary, data quality gap table with targets, full data chain diagram, links to all related reports

**Why:** HirePulse was a standalone page outside any database. Now it's tracked in Report Base with proper TYPE/QA STATUS/RUN ID properties, making it queryable and part of the audit trail.

### 12b. Cross-links established across the hiring data chain

All 4 pages now link bidirectionally to related reports:

| Page | Links Added |
|------|-------------|
| **DATA QUALITY — 2026-03-08** | → HirePulse dashboard, HirePulse Report Base entry, both DEEP DIVE reports |
| **Crypto Hiring Geography** | → Early Stage companion, HirePulse dashboard, DATA QUALITY report, HirePulse entry |
| **Early Stage Crypto Hiring** | → All-Stages companion, HirePulse dashboard, DATA QUALITY report, HirePulse entry |
| **HirePulse dashboard** | → Report Base entry, both DEEP DIVE reports, DATA QUALITY report, data chain diagram |

### 12c. Unified Data Health Index added to DATA QUALITY report

The DATA QUALITY report now includes a combined health summary:
- **Internal DBs (Notion):** 0/100 — all fields missing across Client/Job/Talent Base
- **External pipeline (HirePulse):** ~45/100 — APIs healthy but 4 major data gaps
- This gives a single-glance view of both quality monitors instead of requiring navigation between pages

### 12d. DEEP DIVE reports updated with freshness context

Both geography reports (25 days old as of Mar 09) now have:
- **Freshness notice banner** at the top warning data is directional, not current
- **Refresh instructions** pointing to the `weekly-breakdown` skill
- **Data pipeline link** to HirePulse for checking current API health
- **QA STATUS changed** from PASS → WARN with QA ISSUES documenting staleness
- **LAST AUDITED AT** updated to 2026-03-09
- **Methodology section** updated with upstream data source link to HirePulse
