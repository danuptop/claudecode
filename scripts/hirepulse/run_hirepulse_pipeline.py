#!/usr/bin/env python3
"""
HirePulse Pipeline Runner — Orchestrates ingest -> hiring_intent -> QA.

Applies the Web3 scraper's 17-stage pipeline pattern to the HirePulse system:
- Stage definitions with dependency graph
- Stage contracts validated before persistence
- Checkpoint/resume for crash recovery
- Error classification driving retry/skip/halt decisions

Usage:
    python3 -m hirepulse.run_hirepulse_pipeline \\
        --output-dir reports/hirepulse \\
        --hiring-intent-output-dir reports/hiring_intent \\
        --companies-yaml config/companies.yaml \\
        --run-overlap-audit

Deployment:
    Tony: /home/ubuntu/clawd/scripts/hirepulse/run_hirepulse_pipeline.py
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hirepulse.models import QAStatus
from hirepulse.pipeline_orchestrator import (
    FieldContract,
    Pipeline,
    Stage,
    StageContract,
)

logger = logging.getLogger("hirepulse.runner")


# ---------------------------------------------------------------------------
# Stage functions — each receives and returns context dict
# ---------------------------------------------------------------------------

def stage_ingest(context: dict[str, Any]) -> dict[str, Any]:
    """Stage 0: Build seed universe from all sources."""
    from hirepulse.hirepulse_ingest import run_ingest

    result = run_ingest(
        companies_yaml=context.get("companies_yaml", "config/companies.yaml"),
        output_dir=context.get("output_dir", "reports/hirepulse"),
        report_base_db_id=context.get("report_base_db_id"),
        client_base_db_id=context.get("client_base_db_id"),
        env_file=context.get("env_file"),
    )

    context["seed_universe"] = result["seed_universe"]
    context["ingest_artifacts"] = result["artifacts"]

    return {
        "total_companies": result["ingest_record"].companies_ingested,
        "source_counts": result["ingest_record"].source_counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def stage_hiring_intent(context: dict[str, Any]) -> dict[str, Any]:
    """Stage 1: Enrich seed universe with hiring signals."""
    from hirepulse.hiring_intent_intelligence import run_hiring_intent

    result = run_hiring_intent(
        hirepulse_output_dir=context.get("output_dir", "reports/hirepulse"),
        output_dir=context.get("hiring_intent_output_dir", "reports/hiring_intent"),
        companies_yaml=context.get("companies_yaml", "config/companies.yaml"),
        report_base_db_id=context.get("report_base_db_id"),
        client_base_db_id=context.get("client_base_db_id"),
        run_overlap_audit_flag=context.get("run_overlap_audit", False),
    )

    context["intent_reports"] = result["reports"]
    context["intent_artifacts"] = result["artifacts"]

    reports = result["reports"]
    return {
        "total_companies": len(reports),
        "intent_breakdown": {
            level: len([r for r in reports if r.intent_level.value == level])
            for level in ("high", "medium", "low", "none")
        },
        "errors": len(result.get("errors", [])),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports": json.dumps([r.model_dump(mode="json") for r in reports])[:100],  # Truncated for contract check
    }


def stage_qa(context: dict[str, Any]) -> dict[str, Any]:
    """Stage 2: Run QA checks on all outputs."""
    from hirepulse.hirepulse_qa import run_qa

    result = run_qa(
        output_dir=context.get("output_dir", "reports/hirepulse"),
        hiring_intent_dir=context.get("hiring_intent_output_dir", "reports/hiring_intent"),
        env_file=context.get("env_file"),
        fail_on_severity=context.get("qa_fail_on_severity", "high"),
        alert_on=context.get("qa_alert_on", "fail"),
        notion_sync=context.get("qa_notion_sync", "never"),
    )

    context["qa_status"] = result["overall_status"]

    return {
        "overall_status": result["overall_status"].value,
        "findings": result["severity_counts"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Stage contracts
# ---------------------------------------------------------------------------

INGEST_STAGE_CONTRACT = StageContract(
    stage_name="ingest",
    required_fields=[
        FieldContract(name="total_companies", required=True, min_value=1),
        FieldContract(name="generated_at", required=True, min_length=10),
    ],
)

HIRING_INTENT_STAGE_CONTRACT = StageContract(
    stage_name="hiring_intent",
    required_fields=[
        FieldContract(name="total_companies", required=True, min_value=0),
        FieldContract(name="generated_at", required=True, min_length=10),
    ],
)

QA_STAGE_CONTRACT = StageContract(
    stage_name="qa",
    required_fields=[
        FieldContract(name="overall_status", required=True, min_length=3),
    ],
)


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

def build_pipeline(checkpoint_dir: str = "reports/hirepulse") -> Pipeline:
    """Build the HirePulse pipeline with stages, contracts, and dependencies."""
    stages = [
        Stage(
            name="ingest",
            fn=stage_ingest,
            depends_on=[],
            contract=INGEST_STAGE_CONTRACT,
            max_retries=2,
            description="Build seed universe from Report Base + Client Base + companies.yaml",
        ),
        Stage(
            name="hiring_intent",
            fn=stage_hiring_intent,
            depends_on=["ingest"],
            contract=HIRING_INTENT_STAGE_CONTRACT,
            max_retries=1,
            description="Enrich seed universe with hiring signals from HirePulse + DuckDuckGo + Grok",
        ),
        Stage(
            name="qa",
            fn=stage_qa,
            depends_on=["ingest"],  # QA can run even if hiring_intent fails
            contract=QA_STAGE_CONTRACT,
            max_retries=0,  # QA should not retry
            description="Validate all outputs against stage contracts and quality checks",
        ),
    ]

    return Pipeline(
        name="hirepulse",
        stages=stages,
        checkpoint_dir=checkpoint_dir,
    )


# ---------------------------------------------------------------------------
# Execution report
# ---------------------------------------------------------------------------

def write_execution_report(
    pipeline_results: dict,
    context: dict,
    output_dir: str,
):
    """Write a human-readable execution report."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    report_path = os.path.join(output_dir, f"execution_report_{date_str}.md")

    lines = [
        f"# HirePulse Pipeline Execution Report",
        f"",
        f"**Date:** {now.isoformat()}",
        f"**Run ID:** {context.get('run_id', 'unknown')}",
        f"",
        f"## Stage Results",
        f"",
    ]

    for stage_name, result in pipeline_results.items():
        status_emoji = {"completed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(result.status, "?")
        lines.append(f"### {stage_name} — {status_emoji}")
        lines.append(f"- Status: {result.status}")
        lines.append(f"- Duration: {result.duration_seconds:.1f}s")
        if result.error:
            lines.append(f"- Error: {result.error}")
        if result.contract_violations:
            lines.append(f"- Contract violations:")
            for v in result.contract_violations:
                lines.append(f"  - {v}")
        if result.output_summary:
            lines.append(f"- Output: {json.dumps(result.output_summary)}")
        lines.append("")

    # Artifacts
    lines.extend([
        f"## Output Artifacts",
        f"",
    ])
    for key in ("ingest_artifacts", "intent_artifacts"):
        artifacts = context.get(key, {})
        for name, path in artifacts.items():
            lines.append(f"- {name}: `{path}`")
    lines.append("")

    # QA Summary
    qa_status = context.get("qa_status")
    if qa_status:
        lines.extend([
            f"## QA Summary",
            f"",
            f"- Overall Status: **{qa_status.value.upper()}**",
            f"",
        ])

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    logger.info(f"Execution report written: {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HirePulse Pipeline Runner")
    parser.add_argument("--output-dir", default="reports/hirepulse")
    parser.add_argument("--hiring-intent-output-dir", default="reports/hiring_intent")
    parser.add_argument("--companies-yaml", default="config/companies.yaml")
    parser.add_argument("--state-file", default="reports/hirepulse/state.json")
    parser.add_argument("--env-file")
    parser.add_argument("--report-base-db-id")
    parser.add_argument("--client-base-db-id")
    parser.add_argument("--run-overlap-audit", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--start-from", help="Start from a specific stage")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--qa-fail-on-severity",
        choices=["high", "medium", "low", "none"],
        default="high",
    )
    parser.add_argument("--qa-alert-on", choices=["fail", "warn", "never"], default="fail")
    parser.add_argument("--qa-notion-sync", choices=["always", "on_fail", "never"], default="never")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Build context from CLI args
    context = {
        "output_dir": args.output_dir,
        "hiring_intent_output_dir": args.hiring_intent_output_dir,
        "companies_yaml": args.companies_yaml,
        "env_file": args.env_file,
        "report_base_db_id": args.report_base_db_id,
        "client_base_db_id": args.client_base_db_id,
        "run_overlap_audit": args.run_overlap_audit,
        "qa_fail_on_severity": args.qa_fail_on_severity,
        "qa_alert_on": args.qa_alert_on,
        "qa_notion_sync": args.qa_notion_sync,
    }

    # Build and run pipeline
    pipeline = build_pipeline(checkpoint_dir=args.output_dir)
    results = pipeline.run(
        context=context,
        resume=args.resume,
        start_from=args.start_from,
        dry_run=args.dry_run,
    )

    # Write execution report
    report_path = write_execution_report(
        results, context,
        output_dir=args.hiring_intent_output_dir,
    )

    # Summary
    print(f"\nPipeline complete:")
    for name, result in results.items():
        print(f"  [{name}] {result.status} ({result.duration_seconds:.1f}s)")
    print(f"\nExecution report: {report_path}")

    # Exit code based on QA
    qa_status = context.get("qa_status")
    if qa_status and qa_status.value == "fail" and args.qa_fail_on_severity != "none":
        sys.exit(1)


if __name__ == "__main__":
    main()
