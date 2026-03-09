#!/usr/bin/env python3
"""
Hiring Intent Intelligence — Enriches seed universe with hiring signals.

Architectural patterns applied from Web3 Jobs Scraper:
- Enrichment orchestrator (enrichers/enrichment_orchestrator.py) — parallel, rate-limited
- Error classification — transport errors NEVER emitted as data
- Domain-aware matching — reduces false positives/negatives
- Stage contracts — validate output before persistence

Usage:
    from hirepulse.hiring_intent_intelligence import run_hiring_intent

    result = run_hiring_intent(
        hirepulse_output_dir="reports/hirepulse",
        output_dir="reports/hiring_intent",
        companies_yaml="config/companies.yaml",
    )

Deployment:
    Tony: /home/ubuntu/clawd/scripts/hirepulse/hiring_intent_intelligence.py
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from hirepulse.models import (
    CompanyEntity,
    DomainDedupeResult,
    FatalError,
    HiringIntentReport,
    HiringSignal,
    IntentLevel,
    OverlapEntry,
    PipelineErrorKind,
    RetryableError,
    SkippableError,
    classify_error,
    normalize_company_name,
)

logger = logging.getLogger("hirepulse.hiring_intent")


# ---------------------------------------------------------------------------
# Enrichers — modular signal sources
# ---------------------------------------------------------------------------

def enrich_from_hirepulse(
    entity: CompanyEntity,
    hirepulse_data: dict[str, Any],
) -> list[HiringSignal]:
    """
    Match entity against HirePulse job data.

    Uses domain-aware matching as primary, normalized name as fallback.
    This addresses the handoff prompt priority #2: improve matching precision.
    """
    signals = []
    jobs = hirepulse_data.get("jobs", [])

    for job in jobs:
        # Domain-aware match (primary path — more precise)
        if entity.canonical_domain and _domain_match(job, entity.canonical_domain):
            try:
                signal = HiringSignal(
                    company_name=entity.name,
                    signal_type="job_posting",
                    signal_text=_safe_signal_text(job),
                    confidence=0.9,
                    source="hirepulse_domain_match",
                    detected_at=job.get("scraped_at", ""),
                )
                signals.append(signal)
            except ValueError as e:
                # Pydantic rejected this — likely transport error in signal_text
                logger.debug(f"Signal rejected by model for {entity.name}: {e}")
            continue

        # Normalized name match (fallback — baseline path)
        job_company = normalize_company_name(job.get("company", ""))
        if job_company and job_company == entity.normalized_name:
            try:
                signal = HiringSignal(
                    company_name=entity.name,
                    signal_type="job_posting",
                    signal_text=_safe_signal_text(job),
                    confidence=0.7,  # Lower confidence for name-only match
                    source="hirepulse_name_match",
                    detected_at=job.get("scraped_at", ""),
                )
                signals.append(signal)
            except ValueError as e:
                logger.debug(f"Signal rejected by model for {entity.name}: {e}")

    return signals


def enrich_from_duckduckgo(entity: CompanyEntity) -> list[HiringSignal]:
    """
    Search DuckDuckGo for hiring signals.

    Uses resilient_api with error classification — transport errors
    return empty list, NEVER emit as data (prevents F-04).
    """
    try:
        # Import here to handle missing dependency gracefully
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from resilient_api import search_duckduckgo
    except ImportError:
        logger.debug("resilient_api not available — skipping DuckDuckGo enrichment")
        return []

    query = f"{entity.name} hiring jobs careers 2026"
    result = search_duckduckgo(query)

    if result is None:
        # API failure — return empty, NOT error-as-signal
        return []

    signals = []
    # Parse search results for hiring indicators
    hiring_keywords = ["hiring", "careers", "job opening", "we're growing", "join our team"]
    for keyword in hiring_keywords:
        if keyword.lower() in result.lower():
            try:
                signal = HiringSignal(
                    company_name=entity.name,
                    signal_type="web_mention",
                    signal_text=f"DuckDuckGo search found '{keyword}' indicator",
                    confidence=0.4,
                    source="duckduckgo",
                    detected_at=datetime.now(timezone.utc).isoformat(),
                )
                signals.append(signal)
            except ValueError:
                pass  # Model rejected — likely error text leaked through

    return signals


def enrich_from_grok(entity: CompanyEntity) -> list[HiringSignal]:
    """
    Call Grok API for hiring analysis.

    Uses resilient_api with error classification — transport errors
    return empty list, NEVER emit as data (prevents F-05).
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from resilient_api import call_grok
    except ImportError:
        logger.debug("resilient_api not available — skipping Grok enrichment")
        return []

    prompt = (
        f"Analyze hiring intent for {entity.name}. "
        f"What roles are they likely hiring for? "
        f"Return a brief JSON array of role titles, or empty array if unknown."
    )

    result = call_grok(prompt, timeout=20)
    if result is None:
        return []

    # Parse response — Grok returns free text, try to extract roles
    signals = []
    try:
        # Try JSON parse first
        import re
        json_match = re.search(r'\[.*?\]', result, re.DOTALL)
        if json_match:
            roles = json.loads(json_match.group())
            if isinstance(roles, list):
                for role in roles[:10]:  # Cap at 10 roles
                    try:
                        signal = HiringSignal(
                            company_name=entity.name,
                            signal_type="predicted_role",
                            signal_text=str(role),
                            confidence=0.5,
                            source="grok_analysis",
                            detected_at=datetime.now(timezone.utc).isoformat(),
                        )
                        signals.append(signal)
                    except ValueError:
                        pass  # Model rejected
    except (json.JSONDecodeError, TypeError):
        pass  # Failed to parse — return empty, not error

    return signals


def _domain_match(job: dict, domain: str) -> bool:
    """Check if a job listing's company URL matches a domain."""
    job_url = job.get("company_url", "") or job.get("url", "")
    if not job_url:
        return False
    return domain.lower() in job_url.lower()


def _safe_signal_text(job: dict) -> str:
    """Extract signal text from a job, safe for HiringSignal validation."""
    title = job.get("title", "Unknown role")
    location = job.get("location", "")
    text = f"{title}"
    if location:
        text += f" ({location})"
    return text[:500]  # Cap length


# ---------------------------------------------------------------------------
# Intent scoring
# ---------------------------------------------------------------------------

def score_intent(signals: list[HiringSignal]) -> IntentLevel:
    """
    Score hiring intent level from signals.

    HIGH: 5+ signals or any high-confidence job postings
    MEDIUM: 2-4 signals
    LOW: 1 signal
    NONE: no signals
    """
    if not signals:
        return IntentLevel.NONE

    high_confidence = [s for s in signals if s.confidence >= 0.8]
    if len(signals) >= 5 or len(high_confidence) >= 2:
        return IntentLevel.HIGH
    if len(signals) >= 2:
        return IntentLevel.MEDIUM
    return IntentLevel.LOW


# ---------------------------------------------------------------------------
# Overlap audit
# ---------------------------------------------------------------------------

def run_overlap_audit(
    seed_universe: list[CompanyEntity],
) -> list[OverlapEntry]:
    """
    Check for overlapping/redundant work across pipelines.

    Addresses handoff prompt priority #4: operational overlap/redundancy audit.
    """
    overlaps = []

    for entity in seed_universe:
        pipelines = []
        artifacts = []

        # Check which pipelines touch this entity
        if entity.source == "report_base":
            if entity.report_type == "FUNDRAISING INTEL":
                pipelines.append("funding-intel-brief")
                pipelines.append("founder-intel-pipeline")
                artifacts.append(f"Report Base page: {entity.source_id}")
            if entity.report_type == "LEAD GEN":
                pipelines.append("lead-gen-pipeline")
                artifacts.append(f"Report Base page: {entity.source_id}")

        if entity.source == "client_base":
            pipelines.append("talent_flow_pipeline")
            artifacts.append(f"Client Base page: {entity.source_id}")

        # HirePulse always touches it
        pipelines.append("hirepulse_pipeline")
        artifacts.append("reports/hiring_intent/")

        if len(pipelines) > 2:
            overlap_type = "partial_overlap"
            if "talent_flow_pipeline" in pipelines and "founder-intel-pipeline" in pipelines:
                overlap_type = "full_duplicate"

            overlaps.append(OverlapEntry(
                entity_name=entity.name,
                pipelines=pipelines,
                artifact_paths=artifacts,
                overlap_type=overlap_type,
                recommendation=_overlap_recommendation(pipelines),
                owner="hirepulse_pipeline",
            ))

    return overlaps


def _overlap_recommendation(pipelines: list[str]) -> str:
    """Generate consolidation recommendation based on overlapping pipelines."""
    if "talent_flow_pipeline" in pipelines and "founder-intel-pipeline" in pipelines:
        return (
            "CONSOLIDATE: founder-intel-pipeline and talent_flow_pipeline both "
            "produce outreach intel for this company. Designate one as canonical "
            "owner. Recommendation: founder-intel-pipeline owns outreach for "
            "FUNDRAISING INTEL companies; talent_flow owns for CLIENT companies."
        )
    if "funding-intel-brief" in pipelines and "lead-gen-pipeline" in pipelines:
        return (
            "REVIEW: Company appears in both FUNDRAISING INTEL and LEAD GEN. "
            "Verify REPORT KEYs are distinct to prevent dedup conflicts."
        )
    return "No action needed — pipelines serve complementary purposes."


# ---------------------------------------------------------------------------
# Main hiring intent function
# ---------------------------------------------------------------------------

def run_hiring_intent(
    hirepulse_output_dir: str = "reports/hirepulse",
    output_dir: str = "reports/hiring_intent",
    companies_yaml: str = "config/companies.yaml",
    report_base_db_id: Optional[str] = None,
    client_base_db_id: Optional[str] = None,
    run_overlap_audit_flag: bool = False,
) -> dict[str, Any]:
    """
    Run hiring intent intelligence on the seed universe.

    Steps:
    1. Load seed universe from ingest output
    2. Enrich each company with hiring signals (parallel-ready)
    3. Score intent level
    4. Optionally run overlap audit
    5. Write output artifacts

    Returns:
        Dict with reports, overlap audit, and artifact paths.
    """
    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc).isoformat()

    # Load seed universe
    universe_path = os.path.join(hirepulse_output_dir, "seed_universe_latest.json")
    if not os.path.exists(universe_path):
        raise FatalError(f"Seed universe not found: {universe_path}. Run ingest first.")

    with open(universe_path) as f:
        universe_data = json.load(f)

    entities = [CompanyEntity(**c) for c in universe_data.get("companies", [])]
    logger.info(f"Loaded {len(entities)} companies from seed universe")

    # Load HirePulse job data (if available)
    hirepulse_data = {}
    hp_data_path = os.path.join(hirepulse_output_dir, "jobs_latest.json")
    if os.path.exists(hp_data_path):
        with open(hp_data_path) as f:
            hirepulse_data = json.load(f)

    # Enrich each entity
    reports: list[HiringIntentReport] = []
    errors = []

    for entity in entities:
        try:
            all_signals: list[HiringSignal] = []

            # Layer 1: HirePulse job data match
            hp_signals = enrich_from_hirepulse(entity, hirepulse_data)
            all_signals.extend(hp_signals)

            # Layer 2: DuckDuckGo (if no job data found)
            if not hp_signals:
                ddg_signals = enrich_from_duckduckgo(entity)
                all_signals.extend(ddg_signals)

            # Layer 3: Grok analysis (for high-priority or funded companies)
            if entity.round_amount and entity.round_amount > 0:
                grok_signals = enrich_from_grok(entity)
                all_signals.extend(grok_signals)

            # Score intent
            intent_level = score_intent(all_signals)

            report = HiringIntentReport(
                company_name=entity.name,
                normalized_name=entity.normalized_name,
                canonical_domain=entity.canonical_domain,
                intent_level=intent_level,
                signals=all_signals,
                job_count=len([s for s in all_signals if s.signal_type == "job_posting"]),
                top_roles=[
                    s.signal_text for s in all_signals
                    if s.signal_type in ("job_posting", "predicted_role")
                ][:10],
            )
            reports.append(report)

        except FatalError:
            raise  # Let fatal errors propagate
        except Exception as e:
            kind = classify_error(e)
            if kind == PipelineErrorKind.FATAL:
                raise FatalError(f"Fatal error processing {entity.name}: {e}")
            logger.warning(f"Skipping {entity.name}: {e}")
            errors.append({"company": entity.name, "error": str(e), "kind": kind.value})

    # Overlap audit
    overlap_audit = []
    if run_overlap_audit_flag:
        overlap_audit = run_overlap_audit(entities)

    # Write output artifacts
    completed_at = datetime.now(timezone.utc).isoformat()
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Hiring intent report
    intent_path = os.path.join(output_dir, "hiring_intent_latest.json")
    intent_data = {
        "generated_at": completed_at,
        "run_id": run_id,
        "total_companies": len(reports),
        "intent_breakdown": {
            "high": len([r for r in reports if r.intent_level == IntentLevel.HIGH]),
            "medium": len([r for r in reports if r.intent_level == IntentLevel.MEDIUM]),
            "low": len([r for r in reports if r.intent_level == IntentLevel.LOW]),
            "none": len([r for r in reports if r.intent_level == IntentLevel.NONE]),
        },
        "errors": errors,
        "reports": [r.model_dump(mode="json") for r in reports],
    }
    with open(intent_path, "w") as f:
        json.dump(intent_data, f, indent=2)
    logger.info(f"Hiring intent written: {intent_path}")

    artifacts = {"hiring_intent": intent_path}

    # Overlap audit (if requested)
    if overlap_audit:
        overlap_path = os.path.join(output_dir, "overlap_audit_latest.md")
        _write_overlap_markdown(overlap_audit, overlap_path, run_id, completed_at)
        artifacts["overlap_audit"] = overlap_path

        overlap_json_path = os.path.join(output_dir, "overlap_audit_latest.json")
        with open(overlap_json_path, "w") as f:
            json.dump(
                {
                    "generated_at": completed_at,
                    "run_id": run_id,
                    "entries": [o.model_dump(mode="json") for o in overlap_audit],
                },
                f,
                indent=2,
            )
        artifacts["overlap_audit_json"] = overlap_json_path

    return {
        "reports": reports,
        "overlap_audit": overlap_audit,
        "errors": errors,
        "artifacts": artifacts,
    }


def _write_overlap_markdown(
    overlaps: list[OverlapEntry],
    path: str,
    run_id: str,
    generated_at: str,
):
    """Write overlap audit as readable markdown."""
    lines = [
        f"# Overlap Audit — HirePulse Pipeline",
        f"",
        f"**Run ID:** {run_id}",
        f"**Generated:** {generated_at}",
        f"**Total overlapping entities:** {len(overlaps)}",
        f"",
        f"## Summary",
        f"",
    ]

    by_type = {}
    for o in overlaps:
        by_type.setdefault(o.overlap_type, []).append(o)

    for overlap_type, entries in by_type.items():
        lines.append(f"### {overlap_type.replace('_', ' ').title()} ({len(entries)})")
        lines.append("")
        for entry in entries:
            lines.append(f"- **{entry.entity_name}**")
            lines.append(f"  - Pipelines: {', '.join(entry.pipelines)}")
            lines.append(f"  - Owner: {entry.owner or 'unassigned'}")
            lines.append(f"  - Recommendation: {entry.recommendation}")
            lines.append("")

    with open(path, "w") as f:
        f.write("\n".join(lines))
    logger.info(f"Overlap audit written: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Hiring Intent Intelligence")
    parser.add_argument("--hirepulse-output-dir", default="reports/hirepulse")
    parser.add_argument("--output-dir", default="reports/hiring_intent")
    parser.add_argument("--companies-yaml", default="config/companies.yaml")
    parser.add_argument("--report-base-db-id")
    parser.add_argument("--client-base-db-id")
    parser.add_argument("--run-overlap-audit", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = run_hiring_intent(
        hirepulse_output_dir=args.hirepulse_output_dir,
        output_dir=args.output_dir,
        companies_yaml=args.companies_yaml,
        report_base_db_id=args.report_base_db_id,
        client_base_db_id=args.client_base_db_id,
        run_overlap_audit_flag=args.run_overlap_audit,
    )

    print(f"\nHiring Intent complete:")
    print(f"  Companies analyzed: {len(result['reports'])}")
    intent_counts = {}
    for r in result["reports"]:
        intent_counts[r.intent_level.value] = intent_counts.get(r.intent_level.value, 0) + 1
    print(f"  Intent breakdown: {intent_counts}")
    if result["errors"]:
        print(f"  Errors: {len(result['errors'])}")
    for name, path in result["artifacts"].items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
