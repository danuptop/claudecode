#!/usr/bin/env python3
"""
HirePulse QA — Pre-write and post-write quality assurance.

Key architectural difference from qa_validator.py:
- qa_validator.py is POST-WRITE — it validates after Notion write
- hirepulse_qa.py is PRE-WRITE — it validates BEFORE persistence using stage contracts

This is the Web3 scraper's StageContract pattern applied to the Report Base pipeline.

Usage:
    from hirepulse.hirepulse_qa import run_qa, validate_ingest_output

CLI:
    python3 -m hirepulse.hirepulse_qa \\
        --output-dir reports/hirepulse \\
        --env-file .env.hirepulse

Deployment:
    Tony: /home/ubuntu/clawd/scripts/hirepulse/hirepulse_qa.py
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hirepulse.models import (
    CompanyEntity,
    HiringIntentReport,
    HiringSignal,
    QASeverity,
    QAStatus,
)
from hirepulse.pipeline_orchestrator import FieldContract, StageContract

logger = logging.getLogger("hirepulse.qa")


# ---------------------------------------------------------------------------
# Stage contracts — these are the PRE-WRITE gates
# ---------------------------------------------------------------------------

INGEST_CONTRACT = StageContract(
    stage_name="ingest",
    min_output_count=1,
    required_fields=[
        FieldContract(name="total_companies", required=True, min_value=1),
        FieldContract(name="generated_at", required=True, min_length=10),
        FieldContract(name="run_id", required=True, min_length=4),
    ],
)

HIRING_INTENT_CONTRACT = StageContract(
    stage_name="hiring_intent",
    min_output_count=0,
    required_fields=[
        FieldContract(name="total_companies", required=True, min_value=0),
        FieldContract(name="generated_at", required=True, min_length=10),
        FieldContract(
            name="reports",
            required=True,
            forbidden_patterns=[
                r"HTTPSConnectionPool",
                r"Max retries exceeded",
                r"Traceback \(most recent call last\)",
                r"\[Grok error:",
                r"ConnectionError\(",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# QA Finding
# ---------------------------------------------------------------------------

class QAFinding:
    """A single QA issue."""
    def __init__(self, severity: QASeverity, category: str, message: str, details: str = ""):
        self.severity = severity
        self.category = category
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        d = {
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
        }
        if self.details:
            d["details"] = self.details
        return d


# ---------------------------------------------------------------------------
# QA checks
# ---------------------------------------------------------------------------

def validate_ingest_output(ingest_path: str) -> list[QAFinding]:
    """Validate ingest output against contract."""
    findings = []

    if not os.path.exists(ingest_path):
        findings.append(QAFinding(
            QASeverity.HIGH, "ingest", "Seed universe file not found",
            details=ingest_path,
        ))
        return findings

    with open(ingest_path) as f:
        data = json.load(f)

    # Run stage contract
    violations = INGEST_CONTRACT.validate(data)
    for v in violations:
        findings.append(QAFinding(QASeverity.HIGH, "ingest_contract", v))

    # Check for empty companies
    companies = data.get("companies", [])
    if not companies:
        findings.append(QAFinding(
            QASeverity.HIGH, "ingest", "Seed universe has 0 companies",
        ))
        return findings

    # Check for companies with no domain
    no_domain = [c for c in companies if not c.get("canonical_domain")]
    if no_domain:
        pct = len(no_domain) / len(companies) * 100
        if pct > 50:
            findings.append(QAFinding(
                QASeverity.MEDIUM, "ingest_domains",
                f"{len(no_domain)}/{len(companies)} ({pct:.0f}%) companies have no canonical domain",
            ))

    # Check for duplicated normalized names
    name_counts: dict[str, int] = {}
    for c in companies:
        n = c.get("normalized_name", "")
        name_counts[n] = name_counts.get(n, 0) + 1
    dupes = {n: cnt for n, cnt in name_counts.items() if cnt > 1}
    if dupes:
        findings.append(QAFinding(
            QASeverity.MEDIUM, "ingest_dedup",
            f"{len(dupes)} normalized names appear multiple times after dedup",
            details=json.dumps(dict(list(dupes.items())[:10])),
        ))

    # Check freshness
    generated_at = data.get("generated_at", "")
    if generated_at:
        try:
            gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
            if age_hours > 48:
                findings.append(QAFinding(
                    QASeverity.MEDIUM, "freshness",
                    f"Seed universe is {age_hours:.0f}h old (>48h threshold)",
                ))
        except (ValueError, TypeError):
            findings.append(QAFinding(
                QASeverity.LOW, "freshness",
                "Could not parse generated_at timestamp",
            ))

    return findings


def validate_hiring_intent_output(intent_path: str) -> list[QAFinding]:
    """Validate hiring intent output against contract."""
    findings = []

    if not os.path.exists(intent_path):
        findings.append(QAFinding(
            QASeverity.MEDIUM, "hiring_intent", "Hiring intent file not found",
            details=intent_path,
        ))
        return findings

    with open(intent_path) as f:
        data = json.load(f)

    # Run stage contract
    violations = HIRING_INTENT_CONTRACT.validate(data)
    for v in violations:
        findings.append(QAFinding(QASeverity.HIGH, "intent_contract", v))

    # Check for error signals leaking into reports (the F-04/F-05 check)
    reports = data.get("reports", [])
    error_patterns = [
        "HTTPSConnectionPool", "Max retries exceeded", "Traceback",
        "[Grok error:", "ConnectionError(", "TimeoutError",
    ]
    for report in reports:
        for signal in report.get("signals", []):
            text = signal.get("signal_text", "")
            for pattern in error_patterns:
                if pattern in text:
                    findings.append(QAFinding(
                        QASeverity.HIGH, "error_as_data",
                        f"Transport error emitted as hiring signal for {report.get('company_name')}",
                        details=f"Pattern: {pattern}, Text: {text[:100]}",
                    ))

    # Check intent distribution (sanity check)
    breakdown = data.get("intent_breakdown", {})
    total = sum(breakdown.values())
    if total > 0:
        none_pct = breakdown.get("none", 0) / total * 100
        if none_pct > 95:
            findings.append(QAFinding(
                QASeverity.MEDIUM, "intent_quality",
                f"{none_pct:.0f}% of companies have NONE intent — enrichment may be failing",
            ))

    # Check errors
    errors = data.get("errors", [])
    if errors:
        fatal_errors = [e for e in errors if e.get("kind") == "fatal"]
        if fatal_errors:
            findings.append(QAFinding(
                QASeverity.HIGH, "pipeline_errors",
                f"{len(fatal_errors)} fatal errors during enrichment",
                details=json.dumps(fatal_errors[:3]),
            ))
        elif len(errors) > len(reports) * 0.5:
            findings.append(QAFinding(
                QASeverity.MEDIUM, "pipeline_errors",
                f"{len(errors)} errors ({len(errors)/max(len(reports),1)*100:.0f}% error rate)",
            ))

    # Check freshness
    generated_at = data.get("generated_at", "")
    if generated_at:
        try:
            gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
            if age_hours > 72:
                findings.append(QAFinding(
                    QASeverity.MEDIUM, "freshness",
                    f"Hiring intent is {age_hours:.0f}h old (>72h threshold)",
                ))
        except (ValueError, TypeError):
            pass

    return findings


def validate_domain_dedup(dedup_path: str) -> list[QAFinding]:
    """Validate domain dedup audit output."""
    findings = []

    if not os.path.exists(dedup_path):
        return findings  # Optional artifact

    with open(dedup_path) as f:
        data = json.load(f)

    decisions = data.get("decisions", [])
    splits = [d for d in decisions if d.get("decision") == "split"]
    if splits:
        findings.append(QAFinding(
            QASeverity.LOW, "domain_dedup",
            f"{len(splits)} companies were split due to conflicting domains",
            details=json.dumps([
                {"name": s["normalized_name"], "split_into": s.get("split_into", [])}
                for s in splits[:5]
            ]),
        ))

    return findings


# ---------------------------------------------------------------------------
# Main QA runner
# ---------------------------------------------------------------------------

def run_qa(
    output_dir: str = "reports/hirepulse",
    hiring_intent_dir: str = "reports/hiring_intent",
    env_file: Optional[str] = None,
    fail_on_severity: str = "high",
    alert_on: str = "fail",
    notion_sync: str = "never",
) -> dict[str, Any]:
    """
    Run all QA checks and produce qa_latest.json.

    Args:
        output_dir: Directory containing ingest outputs.
        hiring_intent_dir: Directory containing hiring intent outputs.
        fail_on_severity: "high", "medium", "low", or "none" — exit code threshold.
        alert_on: "fail", "warn", or "never" — when to send alerts.
        notion_sync: "always", "on_fail", or "never" — when to sync QA to Notion.

    Returns:
        Dict with overall_status, findings, and artifact path.
    """
    if env_file and os.path.exists(env_file):
        _load_env_file(env_file)

    all_findings: list[QAFinding] = []

    # Validate ingest
    ingest_path = os.path.join(output_dir, "seed_universe_latest.json")
    all_findings.extend(validate_ingest_output(ingest_path))

    # Validate hiring intent
    intent_path = os.path.join(hiring_intent_dir, "hiring_intent_latest.json")
    all_findings.extend(validate_hiring_intent_output(intent_path))

    # Validate domain dedup
    dedup_path = os.path.join(output_dir, "domain_dedupe_audit_latest.json")
    all_findings.extend(validate_domain_dedup(dedup_path))

    # Compute overall status
    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        severity_counts[f.severity.value] += 1

    if severity_counts["high"] > 0:
        overall_status = QAStatus.FAIL
    elif severity_counts["medium"] > 0:
        overall_status = QAStatus.WARN
    else:
        overall_status = QAStatus.PASS

    # Group findings by category for trend summary
    by_category: dict[str, list[QAFinding]] = {}
    for f in all_findings:
        by_category.setdefault(f.category, []).append(f)

    # Write QA output
    generated_at = datetime.now(timezone.utc).isoformat()
    qa_output = {
        "generated_at": generated_at,
        "overall_status": overall_status.value,
        "findings": {
            "high": severity_counts["high"],
            "medium": severity_counts["medium"],
            "low": severity_counts["low"],
        },
        "details": [f.to_dict() for f in all_findings],
        "by_category": {
            cat: [f.to_dict() for f in findings]
            for cat, findings in by_category.items()
        },
    }

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    qa_path = os.path.join(output_dir, "qa_latest.json")
    with open(qa_path, "w") as f:
        json.dump(qa_output, f, indent=2)
    logger.info(f"QA report written: {qa_path}")

    # Log summary
    logger.info(f"QA Status: {overall_status.value.upper()}")
    for finding in all_findings:
        log_fn = logger.warning if finding.severity == QASeverity.HIGH else logger.info
        log_fn(f"  [{finding.severity.value.upper()}] {finding.category}: {finding.message}")

    return {
        "overall_status": overall_status,
        "findings": all_findings,
        "severity_counts": severity_counts,
        "artifact": qa_path,
    }


def _load_env_file(path: str):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HirePulse QA")
    parser.add_argument("--output-dir", default="reports/hirepulse")
    parser.add_argument("--hiring-intent-dir", default="reports/hiring_intent")
    parser.add_argument("--env-file")
    parser.add_argument(
        "--fail-on-severity",
        choices=["high", "medium", "low", "none"],
        default="high",
    )
    parser.add_argument(
        "--alert-on",
        choices=["fail", "warn", "never"],
        default="fail",
    )
    parser.add_argument(
        "--notion-sync",
        choices=["always", "on_fail", "never"],
        default="never",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = run_qa(
        output_dir=args.output_dir,
        hiring_intent_dir=args.hiring_intent_dir,
        env_file=args.env_file,
        fail_on_severity=args.fail_on_severity,
        alert_on=args.alert_on,
        notion_sync=args.notion_sync,
    )

    # Exit code based on severity threshold
    severity_thresholds = {"high": QAStatus.FAIL, "medium": QAStatus.WARN, "low": QAStatus.PASS, "none": QAStatus.PASS}
    threshold = severity_thresholds.get(args.fail_on_severity, QAStatus.FAIL)
    if result["overall_status"].value == "fail" and args.fail_on_severity != "none":
        sys.exit(1)
    if result["overall_status"].value == "warn" and args.fail_on_severity in ("medium", "low"):
        sys.exit(1)


if __name__ == "__main__":
    main()
