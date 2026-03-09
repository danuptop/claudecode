# Tony Setup Prompt — HirePulse + Hiring Intent Intelligence Pipeline

**Date:** March 9, 2026
**Target:** Tony (`/home/ubuntu/clawd/`)
**Source repo:** `danuptop/claudecode` branch `claude/customize-for-our-stack-aX1ZG`

---

## MISSION

Pull the HirePulse pipeline from the repo, install it on Tony, configure credentials, validate with a dry-run, and then execute the full pipeline. This is a Tony-only implementation — no Sylvio.

## HARD CONSTRAINTS

1. **Tony-only.** Do not use Sylvio at all.
2. **Do not modify existing production scripts** (`founder-intel-pipeline.py`, `funding-intel-brief.py`, `hiring_intel_module.py`) during this setup. Those have separate patches pending — this is additive only.
3. **Back up before overwriting** any file that already exists.
4. **Do not delete or force-push** anything in the repo.
5. If any Notion write is involved, enforce the audit-log requirements (Activity/Talent/Client/Job/Score protected targets + audit sink).
6. Maintain terminology: use "interview" not "intake/screening" in generated content.

---

## WHAT YOU ARE INSTALLING

The HirePulse pipeline is a new package at `scripts/hirepulse/` that monitors hiring intent for companies in the Report Base + Client Base universe. It has three stages:

```
Stage 0: INGEST (hirepulse_ingest.py)
  → Loads companies from: companies.yaml + Report Base (Notion) + Client Base (Notion)
  → Domain-aware dedup: blocked domain filtering, brand-match scoring, multi-bucket split
  → Output: reports/hirepulse/seed_universe_latest.json

Stage 1: HIRING INTENT (hiring_intent_intelligence.py)
  → Enriches each company with hiring signals from HirePulse data + DuckDuckGo + Grok
  → Domain-aware matching (primary) + normalized name fallback
  → Error classification: transport errors → empty list, NEVER emitted as data
  → Output: reports/hiring_intent/hiring_intent_latest.json

Stage 2: QA (hirepulse_qa.py)
  → Pre-write contract validation (stage contracts with forbidden patterns)
  → Freshness checks, domain dedup quality, intent quality monitoring
  → Output: reports/hirepulse/qa_latest.json
```

Orchestrated by `run_hirepulse_pipeline.py` with checkpoint/resume support.

---

## STEP-BY-STEP SETUP

### Step 1: Pull the repo

```bash
cd /home/ubuntu/clawd
git fetch origin claude/customize-for-our-stack-aX1ZG
git checkout claude/customize-for-our-stack-aX1ZG -- \
  scripts/hirepulse/__init__.py \
  scripts/hirepulse/models.py \
  scripts/hirepulse/pipeline_orchestrator.py \
  scripts/hirepulse/hirepulse_ingest.py \
  scripts/hirepulse/hiring_intent_intelligence.py \
  scripts/hirepulse/hirepulse_qa.py \
  scripts/hirepulse/run_hirepulse_pipeline.py \
  config/companies.yaml \
  config/.env.hirepulse.template \
  tests/__init__.py \
  tests/test_models.py \
  tests/test_domain_dedupe.py \
  tests/test_pipeline_contracts.py \
  tests/test_qa.py
```

If `/home/ubuntu/clawd` is NOT a clone of `danuptop/claudecode`, clone it or copy the files manually:

```bash
# Alternative: clone fresh
cd /home/ubuntu
git clone https://github.com/danuptop/claudecode.git clawd-hirepulse
cd clawd-hirepulse
git checkout claude/customize-for-our-stack-aX1ZG

# Then copy into clawd
cp -r scripts/hirepulse/ /home/ubuntu/clawd/scripts/hirepulse/
cp config/companies.yaml /home/ubuntu/clawd/config/companies.yaml
cp config/.env.hirepulse.template /home/ubuntu/clawd/.env.hirepulse
cp -r tests/ /home/ubuntu/clawd/tests/
```

### Step 2: Back up any existing files that would be overwritten

```bash
cd /home/ubuntu/clawd
BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Back up shared modules if they exist
for f in scripts/qa_validator.py scripts/content_sanitizer.py \
         scripts/resilient_api.py scripts/canonical_template.py; do
    [ -f "$f" ] && cp "$f" "$BACKUP_DIR/" && echo "Backed up $f"
done
echo "Backups at: $BACKUP_DIR"
```

### Step 3: Install Python dependencies

```bash
pip3 install pydantic pyyaml pytest notion-client requests
```

Verify:
```bash
python3 -c "import pydantic; print(f'pydantic {pydantic.__version__}')"
python3 -c "import yaml; print('pyyaml OK')"
python3 -c "import notion_client; print('notion-client OK')"
```

### Step 4: Create output directories

```bash
mkdir -p /home/ubuntu/clawd/reports/hirepulse
mkdir -p /home/ubuntu/clawd/reports/hiring_intent
mkdir -p /home/ubuntu/clawd/config
```

### Step 5: Configure environment

```bash
cd /home/ubuntu/clawd

# If .env.hirepulse doesn't exist yet, create from template
[ ! -f .env.hirepulse ] && cp config/.env.hirepulse.template .env.hirepulse

# Edit and fill in NOTION_TOKEN (REQUIRED)
nano .env.hirepulse
```

The file should contain at minimum:
```
NOTION_TOKEN=ntn_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
REPORT_BASE_DB=902d47be-68c0-4da8-832a-a52272fc7b39
CLIENT_BASE_DB=14dba737-d3d2-4b1d-b1d9-ba9ef4f4141a
```

Optional (enables Grok-based role prediction enrichment):
```
GROK_API_KEY=xai-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Step 6: Validate syntax

```bash
cd /home/ubuntu/clawd
for pyfile in scripts/hirepulse/*.py; do
    python3 -c "import ast; ast.parse(open('$pyfile').read())" && \
        echo "$pyfile: OK" || echo "$pyfile: SYNTAX ERROR"
done
```

All 7 files must say OK. If any show SYNTAX ERROR, stop and investigate.

### Step 7: Run tests

```bash
cd /home/ubuntu/clawd
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH python3 -m pytest tests/ -v
```

**Expected: 100 passed.** If any fail, check the error output. Common issues:
- `ModuleNotFoundError: pydantic` → Step 3 wasn't completed
- `ModuleNotFoundError: yaml` → `pip3 install pyyaml`
- Import errors in `hirepulse.models` → PYTHONPATH not set correctly

### Step 8: Dry-run the pipeline

```bash
cd /home/ubuntu/clawd
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.run_hirepulse_pipeline \
  --output-dir reports/hirepulse \
  --hiring-intent-output-dir reports/hiring_intent \
  --companies-yaml config/companies.yaml \
  --env-file .env.hirepulse \
  --dry-run -v
```

**Expected output:**
```
Pipeline 'hirepulse' starting (run_id=XXXXXXXX)
  [ingest] Starting...
  [ingest] Completed in 0.0s
  [hiring_intent] Starting...
  [hiring_intent] Completed in 0.0s
  [qa] Starting...
  [qa] Completed in 0.0s
Pipeline 'hirepulse' finished: {'completed': 3, 'failed': 0, 'skipped': 0}
```

Dry-run validates orchestration without hitting Notion or external APIs.

### Step 9: Run for real (ingest only first)

Test Notion connectivity by running just the ingest stage:

```bash
cd /home/ubuntu/clawd
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.run_hirepulse_pipeline \
  --output-dir reports/hirepulse \
  --hiring-intent-output-dir reports/hiring_intent \
  --companies-yaml config/companies.yaml \
  --env-file .env.hirepulse \
  --start-from ingest \
  -v
```

Check the output:
```bash
python3 -m json.tool reports/hirepulse/seed_universe_latest.json | head -20
python3 -m json.tool reports/hirepulse/domain_dedupe_audit_latest.json | head -20
```

You should see companies loaded from Report Base + Client Base + companies.yaml, with domain dedup applied.

### Step 10: Full pipeline run with overlap audit

```bash
cd /home/ubuntu/clawd
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.run_hirepulse_pipeline \
  --output-dir reports/hirepulse \
  --hiring-intent-output-dir reports/hiring_intent \
  --companies-yaml config/companies.yaml \
  --env-file .env.hirepulse \
  --run-overlap-audit \
  -v
```

### Step 11: Verify outputs

```bash
# QA status (should be pass or warn, not fail)
python3 -c "import json; d=json.load(open('reports/hirepulse/qa_latest.json')); print(f\"QA: {d['overall_status']} | high={d['findings']['high']} med={d['findings']['medium']} low={d['findings']['low']}\")"

# Hiring intent summary
python3 -c "import json; d=json.load(open('reports/hiring_intent/hiring_intent_latest.json')); print(f\"Intent: {d['intent_breakdown']} | Companies: {d['total_companies']}\")"

# Seed universe count
python3 -c "import json; d=json.load(open('reports/hirepulse/seed_universe_latest.json')); print(f\"Universe: {d['total_companies']} companies from {d['source_counts']}\")"

# Overlap audit (if --run-overlap-audit was used)
[ -f reports/hiring_intent/overlap_audit_latest.md ] && cat reports/hiring_intent/overlap_audit_latest.md

# Execution report
ls -la reports/hiring_intent/execution_report_*.md
```

---

## EXPECTED FINAL STATE

After completion, Tony should have:

```
/home/ubuntu/clawd/
├── .env.hirepulse                              # Credentials (NOTION_TOKEN filled in)
├── config/
│   ├── companies.yaml                          # Seed universe config
│   └── .env.hirepulse.template                 # Template for reference
├── scripts/
│   ├── hirepulse/                              # NEW — the pipeline package
│   │   ├── __init__.py
│   │   ├── models.py                           # Pydantic models + domain utils
│   │   ├── pipeline_orchestrator.py            # Stage contracts + checkpoint/resume
│   │   ├── hirepulse_ingest.py                 # Seed universe builder
│   │   ├── hiring_intent_intelligence.py       # Hiring signal enrichment
│   │   ├── hirepulse_qa.py                     # Pre-write QA validation
│   │   └── run_hirepulse_pipeline.py           # Pipeline orchestrator
│   ├── qa_validator.py                         # Existing shared module (not modified)
│   ├── content_sanitizer.py                    # Existing shared module (not modified)
│   ├── resilient_api.py                        # Existing shared module (not modified)
│   └── canonical_template.py                   # Existing shared module (not modified)
├── tests/
│   ├── __init__.py
│   ├── test_models.py                          # 32 tests
│   ├── test_domain_dedupe.py                   # 14 tests
│   ├── test_pipeline_contracts.py              # 20 tests
│   └── test_qa.py                              # 10 tests (+ contract tests)
├── reports/
│   ├── hirepulse/
│   │   ├── seed_universe_latest.json           # Generated
│   │   ├── domain_dedupe_audit_latest.json     # Generated
│   │   ├── ingest_record_latest.json           # Generated
│   │   ├── qa_latest.json                      # Generated
│   │   └── hirepulse_checkpoint.json           # Pipeline checkpoint
│   └── hiring_intent/
│       ├── hiring_intent_latest.json           # Generated
│       ├── overlap_audit_latest.json           # Generated (if --run-overlap-audit)
│       ├── overlap_audit_latest.md             # Generated (if --run-overlap-audit)
│       └── execution_report_2026-03-09.md      # Generated
└── backups/
    └── YYYYMMDD_HHMMSS/                        # Pre-deployment backups
```

---

## TROUBLESHOOTING

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: hirepulse` | PYTHONPATH not set | `export PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH` |
| `ModuleNotFoundError: pydantic` | Missing dependency | `pip3 install pydantic` |
| `RuntimeError: NOTION_TOKEN required` | .env.hirepulse not configured | Fill in `NOTION_TOKEN` in `.env.hirepulse` |
| Ingest returns 0 companies from report_base | Notion token lacks DB access | Verify token has access to Report Base DB `902d47be...` |
| QA status is `fail` with `error_as_data` | Transport error leaked into signal | This is the F-04/F-05 check working correctly — the signal should have been rejected by the model |
| Pipeline hangs on hiring_intent stage | DuckDuckGo/Grok API timeout | Circuit breaker will trip after 3 failures; wait 5-10 min for reset |
| Checkpoint shows stage already completed | Previous run cached | Delete `reports/hirepulse/hirepulse_checkpoint.json` and re-run, or use `--start-from <stage>` |

---

## USEFUL COMMANDS AFTER SETUP

```bash
# Full pipeline (standard run)
cd /home/ubuntu/clawd
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.run_hirepulse_pipeline \
  --output-dir reports/hirepulse \
  --hiring-intent-output-dir reports/hiring_intent \
  --companies-yaml config/companies.yaml \
  --env-file .env.hirepulse \
  --run-overlap-audit -v

# Resume from crash (skips completed stages)
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.run_hirepulse_pipeline \
  --output-dir reports/hirepulse \
  --hiring-intent-output-dir reports/hiring_intent \
  --companies-yaml config/companies.yaml \
  --env-file .env.hirepulse \
  --resume -v

# Rebuild only hiring intent (skip ingest)
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.hiring_intent_intelligence \
  --hirepulse-output-dir reports/hirepulse \
  --output-dir reports/hiring_intent \
  --companies-yaml config/companies.yaml \
  --run-overlap-audit -v

# QA only (non-blocking)
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH \
python3 -m hirepulse.hirepulse_qa \
  --output-dir reports/hirepulse \
  --hiring-intent-dir reports/hiring_intent \
  --env-file .env.hirepulse \
  --fail-on-severity none

# Run tests
PYTHONPATH=/home/ubuntu/clawd/scripts:$PYTHONPATH python3 -m pytest tests/ -v
```

---

## DEFINITION OF DONE

You are done when:
- [ ] All files are in place at the paths listed above
- [ ] `pip3 install` completed without errors
- [ ] All 100 tests pass
- [ ] Dry-run completes with 3 stages completed, 0 failed
- [ ] Real ingest pulls companies from Report Base + Client Base
- [ ] `qa_latest.json` shows `overall_status` of `pass` or `warn` (not `fail` with `high > 0`)
- [ ] All output artifacts exist under `reports/`
- [ ] Execution report markdown is generated

Report back with:
1. Test results (pass count)
2. Seed universe company count + source breakdown
3. QA status + any findings
4. Hiring intent breakdown (high/medium/low/none)
5. Any errors or warnings encountered
