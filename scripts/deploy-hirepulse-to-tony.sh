#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# deploy-hirepulse-to-tony.sh
#
# Deploys the HirePulse pipeline + existing patches to Tony.
# Run from local machine: bash scripts/deploy-hirepulse-to-tony.sh
#
# Prerequisites:
#   - SSH alias 'tony' configured
#   - Notion token available
# =============================================================================

TONY="tony"
REMOTE_BASE="/home/ubuntu/clawd"
REMOTE_SCRIPTS="$REMOTE_BASE/scripts"
LOCAL_BASE="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo " HirePulse Deployment to Tony"
echo " $(date)"
echo "=========================================="
echo ""

# ---------------------------------------------------------------------------
# Step 0: Verify SSH connectivity
# ---------------------------------------------------------------------------
echo "[0/7] Checking SSH connectivity to Tony..."
if ! ssh -o ConnectTimeout=10 "$TONY" "echo 'Tony is reachable'" 2>/dev/null; then
    echo "ERROR: Cannot reach Tony via SSH. Check your SSH config."
    echo "  Expected: SSH alias 'tony' -> /home/ubuntu/clawd/"
    exit 1
fi
echo "  OK"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Back up existing scripts on Tony
# ---------------------------------------------------------------------------
echo "[1/7] Backing up existing scripts on Tony..."
ssh "$TONY" bash <<BACKUP
set -e
cd "$REMOTE_SCRIPTS"
BACKUP_DIR="$REMOTE_BASE/backups/$TIMESTAMP"
mkdir -p "\$BACKUP_DIR"

# Back up existing pipeline scripts (if they exist)
for script in founder-intel-pipeline.py funding-intel-brief.py hiring_intel_module.py; do
    if [ -f "\$script" ]; then
        cp "\$script" "\$BACKUP_DIR/\$script"
        echo "  Backed up \$script"
    fi
done

# Back up existing shared modules (if they exist)
for module in qa_validator.py content_sanitizer.py resilient_api.py canonical_template.py; do
    if [ -f "\$module" ]; then
        cp "\$module" "\$BACKUP_DIR/\$module"
        echo "  Backed up \$module"
    fi
done

echo "  Backups at: \$BACKUP_DIR"
BACKUP
echo ""

# ---------------------------------------------------------------------------
# Step 2: Deploy shared modules (already in repo)
# ---------------------------------------------------------------------------
echo "[2/7] Deploying shared modules to Tony..."
for module in qa_validator.py content_sanitizer.py resilient_api.py canonical_template.py; do
    if [ -f "$LOCAL_BASE/scripts/$module" ]; then
        scp "$LOCAL_BASE/scripts/$module" "$TONY:$REMOTE_SCRIPTS/$module"
        echo "  Deployed $module"
    fi
done
echo ""

# ---------------------------------------------------------------------------
# Step 3: Deploy HirePulse package
# ---------------------------------------------------------------------------
echo "[3/7] Deploying HirePulse package to Tony..."
ssh "$TONY" "mkdir -p $REMOTE_SCRIPTS/hirepulse"

for pyfile in __init__.py models.py pipeline_orchestrator.py hirepulse_ingest.py \
              hiring_intent_intelligence.py hirepulse_qa.py run_hirepulse_pipeline.py; do
    if [ -f "$LOCAL_BASE/scripts/hirepulse/$pyfile" ]; then
        scp "$LOCAL_BASE/scripts/hirepulse/$pyfile" "$TONY:$REMOTE_SCRIPTS/hirepulse/$pyfile"
        echo "  Deployed hirepulse/$pyfile"
    fi
done
echo ""

# ---------------------------------------------------------------------------
# Step 4: Deploy config
# ---------------------------------------------------------------------------
echo "[4/7] Deploying config to Tony..."
ssh "$TONY" "mkdir -p $REMOTE_BASE/config"
scp "$LOCAL_BASE/config/companies.yaml" "$TONY:$REMOTE_BASE/config/companies.yaml"
echo "  Deployed config/companies.yaml"

# Deploy env template (don't overwrite existing .env)
if ! ssh "$TONY" "test -f $REMOTE_BASE/.env.hirepulse" 2>/dev/null; then
    scp "$LOCAL_BASE/config/.env.hirepulse.template" "$TONY:$REMOTE_BASE/.env.hirepulse"
    echo "  Deployed .env.hirepulse (template — FILL IN NOTION_TOKEN!)"
else
    echo "  .env.hirepulse already exists on Tony — not overwriting"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 5: Deploy tests
# ---------------------------------------------------------------------------
echo "[5/7] Deploying tests to Tony..."
ssh "$TONY" "mkdir -p $REMOTE_BASE/tests"
for testfile in __init__.py test_models.py test_domain_dedupe.py \
                test_pipeline_contracts.py test_qa.py; do
    if [ -f "$LOCAL_BASE/tests/$testfile" ]; then
        scp "$LOCAL_BASE/tests/$testfile" "$TONY:$REMOTE_BASE/tests/$testfile"
        echo "  Deployed tests/$testfile"
    fi
done
echo ""

# ---------------------------------------------------------------------------
# Step 6: Install dependencies + syntax validation on Tony
# ---------------------------------------------------------------------------
echo "[6/7] Installing dependencies and validating syntax on Tony..."
ssh "$TONY" bash <<VALIDATE
set -e
cd "$REMOTE_BASE"

# Install Python dependencies
pip3 install --quiet pydantic pyyaml pytest 2>/dev/null || \
pip install --quiet pydantic pyyaml pytest 2>/dev/null || \
echo "  WARNING: pip install failed — may need manual install"

# Syntax validation for all Python files
echo "  Syntax check:"
for pyfile in scripts/hirepulse/*.py scripts/qa_validator.py scripts/content_sanitizer.py \
              scripts/resilient_api.py scripts/canonical_template.py; do
    if [ -f "\$pyfile" ]; then
        python3 -c "import ast; ast.parse(open('\$pyfile').read())" && \
            echo "    \$pyfile: OK" || \
            echo "    \$pyfile: SYNTAX ERROR"
    fi
done
VALIDATE
echo ""

# ---------------------------------------------------------------------------
# Step 7: Create output directories + run tests on Tony
# ---------------------------------------------------------------------------
echo "[7/7] Creating output dirs and running tests on Tony..."
ssh "$TONY" bash <<TESTS
set -e
cd "$REMOTE_BASE"

# Create output directories
mkdir -p reports/hirepulse reports/hiring_intent

# Run tests
echo "  Running tests..."
cd "$REMOTE_BASE"
PYTHONPATH="$REMOTE_SCRIPTS:\$PYTHONPATH" python3 -m pytest tests/ -v --tb=short 2>&1 | tail -20
TESTS
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=========================================="
echo " Deployment Complete"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. SSH into Tony and fill in NOTION_TOKEN:"
echo "     ssh tony"
echo "     nano $REMOTE_BASE/.env.hirepulse"
echo ""
echo "  2. Dry-run the pipeline:"
echo "     cd $REMOTE_BASE"
echo "     PYTHONPATH=$REMOTE_SCRIPTS python3 -m hirepulse.run_hirepulse_pipeline \\"
echo "       --output-dir reports/hirepulse \\"
echo "       --hiring-intent-output-dir reports/hiring_intent \\"
echo "       --companies-yaml config/companies.yaml \\"
echo "       --env-file .env.hirepulse \\"
echo "       --dry-run -v"
echo ""
echo "  3. If dry-run passes, run for real:"
echo "     PYTHONPATH=$REMOTE_SCRIPTS python3 -m hirepulse.run_hirepulse_pipeline \\"
echo "       --output-dir reports/hirepulse \\"
echo "       --hiring-intent-output-dir reports/hiring_intent \\"
echo "       --companies-yaml config/companies.yaml \\"
echo "       --env-file .env.hirepulse \\"
echo "       --run-overlap-audit -v"
echo ""
echo "  4. Check outputs:"
echo "     cat reports/hirepulse/qa_latest.json | python3 -m json.tool"
echo "     cat reports/hiring_intent/hiring_intent_latest.json | python3 -m json.tool"
echo ""
echo "BACKUPS: $REMOTE_BASE/backups/$TIMESTAMP/"
