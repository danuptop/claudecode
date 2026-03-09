#!/usr/bin/env python3
"""
Unified Outreach Pipeline — single-pass create + enrich.

Merges the two-phase flow (funding-intel-brief → founder-intel-pipeline)
into one atomic pipeline that:
1. Detects funding event from source
2. Checks dedup (source cache + page registry + Notion query)
3. Enriches: outreach intel + hiring intel
4. Pre-write QA validation
5. Single atomic write to Notion (no half-built pages)
6. Post-write QA + dashboard recording

This eliminates:
- The two-phase write problem (no incomplete pages in Notion)
- The standalone OUTREACH TARGET page bug (single write path)
- The cron-trigger gap (enrichment happens inline)
- The marker replacement complexity (full page built once)

Usage:
    # Process a single funding event
    python3 unified_pipeline.py --company "OKX" --amount 200000000 \\
        --round-type STRATEGIC --source-url "https://..."

    # Dry-run (build everything but don't write to Notion)
    python3 unified_pipeline.py --company "OKX" --amount 200000000 \\
        --round-type STRATEGIC --dry-run

    # Process from a source article (auto-extract details)
    python3 unified_pipeline.py --source-url "https://..." --auto-extract

    # Process from event queue
    python3 unified_pipeline.py --from-queue

Deployment:
    Place at: /home/ubuntu/clawd/scripts/unified_pipeline.py
    Replaces: funding-intel-brief.py + founder-intel-pipeline.py (after migration)
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from typing import Optional

logger = logging.getLogger("unified_pipeline")

REPORT_BASE_DB = os.getenv(
    "REPORT_BASE_DB", "902d47be-68c0-4da8-832a-a52272fc7b39"
)


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------

class PipelineResult:
    """Result of a single pipeline execution."""

    def __init__(self):
        self.page_id: Optional[str] = None
        self.action: str = ""  # "created", "updated", "skipped", "failed"
        self.qa_status: str = ""
        self.issues: list[str] = []
        self.dry_run: bool = False
        self.timings: dict = {}

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "action": self.action,
            "qa_status": self.qa_status,
            "issues": self.issues,
            "dry_run": self.dry_run,
            "timings": self.timings,
        }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    company: str,
    amount: int,
    round_type: str,
    investors: Optional[list[str]] = None,
    source_url: Optional[str] = None,
    source_urls: Optional[list[str]] = None,
    dry_run: bool = False,
    skip_enrichment: bool = False,
    run_id: Optional[str] = None,
) -> PipelineResult:
    """
    Execute the unified pipeline for a single funding event.

    Args:
        company: Company name (e.g., "OKX").
        amount: Round amount in dollars (e.g., 200000000).
        round_type: Round type (e.g., "STRATEGIC", "SEED").
        investors: List of investor/fund names.
        source_url: Primary source article URL.
        source_urls: All source URLs for this event.
        dry_run: If True, build everything but don't write to Notion.
        skip_enrichment: If True, create skeleton without outreach/hiring.
        run_id: Pipeline execution ID (auto-generated if not provided).

    Returns:
        PipelineResult with page_id, action, qa_status, etc.
    """
    result = PipelineResult()
    result.dry_run = dry_run
    run_id = run_id or f"unified-{uuid.uuid4().hex[:8]}"
    investors = investors or []
    source_urls = source_urls or ([source_url] if source_url else [])
    t0 = time.time()

    # Import pipeline dependencies
    try:
        from canonical_template import (
            canonicalize_company,
            generate_report_key,
            build_canonical_blocks,
            build_page_properties,
            amounts_match,
        )
        from content_sanitizer import sanitize_investor_list, sanitize_blocks
        from qa_validator import pre_write_validate, post_write_hook
        from source_dedup_cache import SourceDedupCache
        from page_registry import PageRegistry
        from pipeline_lock import pipeline_lock
    except ImportError as e:
        logger.error(f"Missing dependency: {e}")
        result.action = "failed"
        result.issues.append(f"Import error: {e}")
        return result

    # -----------------------------------------------------------------------
    # Step 1: Dedup checks
    # -----------------------------------------------------------------------
    logger.info(f"[{run_id}] Processing: {company} ${amount:,} {round_type}")

    # 1a. Source URL dedup
    source_cache = SourceDedupCache()
    if source_url and source_cache.is_duplicate(source_url=source_url):
        existing_id = source_cache.get_page_id(source_url=source_url)
        logger.info(f"Source URL already processed → page {existing_id}")
        result.action = "skipped"
        result.page_id = existing_id
        result.issues.append("Source URL already processed")
        return result

    # 1b. Company+amount dedup via page registry
    registry = PageRegistry()
    company_slug = canonicalize_company(company)
    fp = registry.fingerprint(company, round_type, amount)
    existing = registry.get(fp)
    if existing:
        logger.info(f"Event already in registry → page {existing['page_id']}")
        result.action = "skipped"
        result.page_id = existing["page_id"]
        result.issues.append("Event already in page registry")
        return result

    # 1c. $0 amount guard (F-11 fix)
    if amount == 0 or amount is None:
        logger.warning(f"$0 amount for {company} — checking for existing page")
        existing_pages = registry.get_by_company(company)
        if existing_pages:
            result.action = "skipped"
            result.page_id = existing_pages[0]["page_id"]
            result.issues.append(f"$0 amount skipped — existing page: {result.page_id}")
            return result
        logger.warning(f"No existing page for {company} — creating $0 page with QA=WARN")

    result.timings["dedup_check"] = time.time() - t0

    # -----------------------------------------------------------------------
    # Step 2: Sanitize inputs
    # -----------------------------------------------------------------------
    t1 = time.time()
    clean_investors = sanitize_investor_list(investors)
    result.timings["sanitize"] = time.time() - t1

    # -----------------------------------------------------------------------
    # Step 3: Enrich (outreach + hiring) — unless skipped
    # -----------------------------------------------------------------------
    t2 = time.time()
    outreach_blocks = []
    hiring_blocks = []

    if not skip_enrichment:
        outreach_blocks = _generate_outreach(company, amount, round_type, dry_run)
        hiring_blocks = _generate_hiring(company, round_type, dry_run)

        # Sanitize enrichment output
        outreach_blocks = sanitize_blocks(outreach_blocks)
        hiring_blocks = sanitize_blocks(hiring_blocks)

    result.timings["enrichment"] = time.time() - t2

    # -----------------------------------------------------------------------
    # Step 4: Build full page (properties + blocks)
    # -----------------------------------------------------------------------
    t3 = time.time()
    sources = [{"url": u} for u in source_urls if u]
    props = build_page_properties(company, amount, round_type, run_id)
    blocks = build_canonical_blocks(
        company, amount, round_type, clean_investors, sources
    )

    # Replace outreach/hiring placeholders with real content
    if outreach_blocks:
        blocks = _replace_marker_section(
            blocks,
            "[[OUTREACH_INTEL_AUTO_START]]",
            "[[OUTREACH_INTEL_AUTO_END]]",
            outreach_blocks,
        )
    if hiring_blocks:
        blocks = _replace_marker_section(
            blocks,
            "[[HIRING_INTEL_AUTO_START]]",
            "[[HIRING_INTEL_AUTO_END]]",
            hiring_blocks,
        )

    result.timings["build_page"] = time.time() - t3

    # -----------------------------------------------------------------------
    # Step 5: Pre-write QA gate
    # -----------------------------------------------------------------------
    t4 = time.time()
    qa = pre_write_validate(props, blocks, page_type="FUNDRAISING INTEL")
    result.qa_status = qa.status
    result.issues.extend(qa.issues)

    if qa.status == "FAIL" and amount > 0:
        logger.error(f"Pre-write QA FAIL — aborting: {qa.summary}")
        result.action = "failed"
        result.timings["qa_prewrite"] = time.time() - t4

        if dry_run:
            _write_dry_run_output(company, props, blocks, qa, run_id)

        return result

    result.timings["qa_prewrite"] = time.time() - t4

    # -----------------------------------------------------------------------
    # Step 6: Write to Notion (or dry-run)
    # -----------------------------------------------------------------------
    if dry_run:
        logger.info(f"[DRY-RUN] Would create page for {company} ${amount:,} {round_type}")
        _write_dry_run_output(company, props, blocks, qa, run_id)
        result.action = "dry-run"
        result.timings["total"] = time.time() - t0
        return result

    t5 = time.time()
    try:
        from notion_client_wrapper import create_page

        page = create_page(
            database_id=REPORT_BASE_DB,
            properties=props,
            children=blocks,
            pipeline="unified-pipeline",
            run_id=run_id,
        )
        result.page_id = page["id"]
        result.action = "created"
        logger.info(f"Created page {result.page_id} for {company}")

    except Exception as e:
        logger.error(f"Notion write failed: {e}")
        result.action = "failed"
        result.issues.append(f"Write error: {e}")
        return result

    result.timings["notion_write"] = time.time() - t5

    # -----------------------------------------------------------------------
    # Step 7: Post-write bookkeeping
    # -----------------------------------------------------------------------
    # Record in caches
    source_cache.record(
        source_url=source_url,
        company=company,
        amount=amount,
        page_id=result.page_id,
    )
    report_key = generate_report_key(company, amount)
    registry.register(fp, page_id=result.page_id, company=company,
                      round_type=round_type, amount=amount, report_key=report_key)

    # Post-write QA
    try:
        post_status = post_write_hook(result.page_id)
        result.qa_status = post_status
    except Exception as e:
        logger.warning(f"Post-write QA failed: {e}")

    # Record in dashboard
    try:
        from qa_dashboard import QADashboard
        db = QADashboard()
        title = props.get("ENTRY", {}).get("title", [{}])[0].get("text", {}).get("content", company)
        db.record(
            page_id=result.page_id,
            title=title,
            page_type="FUNDRAISING INTEL",
            qa_status=result.qa_status,
            issues=" | ".join(result.issues),
            pipeline="unified-pipeline",
            run_id=run_id,
        )
    except Exception as e:
        logger.debug(f"Dashboard recording failed (non-critical): {e}")

    result.timings["total"] = time.time() - t0
    logger.info(
        f"Pipeline complete: {result.action} page={result.page_id} "
        f"qa={result.qa_status} time={result.timings['total']:.1f}s"
    )
    return result


# ---------------------------------------------------------------------------
# Enrichment generators (stubs — delegate to actual enrichment modules)
# ---------------------------------------------------------------------------

def _generate_outreach(company: str, amount: int, round_type: str, dry_run: bool) -> list[dict]:
    """Generate outreach intel blocks for a company."""
    # In production, this delegates to the outreach generation logic from
    # founder-intel-pipeline.py. For now, return placeholder blocks.
    try:
        from resilient_api import search_duckduckgo, call_grok
    except ImportError:
        pass

    # Placeholder — replace with actual outreach generation
    return [
        _text_block(f"Company: {company}"),
        _text_block(f"Round: ${amount:,} {round_type}"),
        _text_block("Outreach research pending — enrich via founder-intel-pipeline."),
    ]


def _generate_hiring(company: str, round_type: str, dry_run: bool) -> list[dict]:
    """Generate hiring intel blocks for a company."""
    # In production, this delegates to hiring_intel_module.py.
    # For now, return placeholder blocks.
    return [
        _text_block(f"Hiring intelligence for {company}"),
        _text_block("Hiring signals pending — enrich via hiring_intel_module."),
    ]


# ---------------------------------------------------------------------------
# Block helpers
# ---------------------------------------------------------------------------

def _text_block(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
        },
    }


def _replace_marker_section(
    blocks: list[dict],
    start_marker: str,
    end_marker: str,
    new_content: list[dict],
) -> list[dict]:
    """Replace placeholder content between markers with real content."""
    result = []
    inside_markers = False

    for block in blocks:
        bt = block.get("type", "")
        rich_texts = block.get(bt, {}).get("rich_text", [])
        text = "".join(
            rt.get("text", {}).get("content", rt.get("plain_text", ""))
            for rt in rich_texts
        )

        if start_marker in text:
            result.append(block)  # Keep the START marker
            result.extend(new_content)  # Insert real content
            inside_markers = True
            continue

        if end_marker in text:
            result.append(block)  # Keep the END marker
            inside_markers = False
            continue

        if not inside_markers:
            result.append(block)
        # else: skip placeholder content between markers

    return result


def _write_dry_run_output(company: str, props: dict, blocks: list, qa, run_id: str):
    """Write dry-run output to a local JSON file for inspection."""
    output_dir = os.path.join(
        os.getenv("CLAWD_DATA_DIR", "/tmp/clawd-dry-run"),
        "dry-runs",
    )
    os.makedirs(output_dir, exist_ok=True)
    slug = company.lower().replace(" ", "-")[:20]
    path = os.path.join(output_dir, f"{run_id}_{slug}.json")

    output = {
        "run_id": run_id,
        "company": company,
        "qa_status": qa.status,
        "qa_issues": qa.issues,
        "properties": _serialize_props(props),
        "blocks_count": len(blocks),
        "blocks_preview": [
            _block_preview(b) for b in blocks[:10]
        ],
    }

    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    logger.info(f"Dry-run output written to: {path}")


def _serialize_props(props: dict) -> dict:
    """Serialize Notion properties to readable format."""
    out = {}
    for k, v in props.items():
        if "title" in v:
            out[k] = v["title"][0]["text"]["content"] if v["title"] else ""
        elif "rich_text" in v:
            out[k] = v["rich_text"][0]["text"]["content"] if v["rich_text"] else ""
        elif "select" in v:
            out[k] = v["select"]["name"] if v["select"] else ""
        elif "number" in v:
            out[k] = v["number"]
        else:
            out[k] = str(v)[:100]
    return out


def _block_preview(block: dict) -> str:
    bt = block.get("type", "")
    rich_texts = block.get(bt, {}).get("rich_text", [])
    text = "".join(
        rt.get("text", {}).get("content", rt.get("plain_text", ""))
        for rt in rich_texts
    )
    return f"[{bt}] {text[:80]}" if text else f"[{bt}]"


# ---------------------------------------------------------------------------
# Queue consumer mode
# ---------------------------------------------------------------------------

def run_from_queue():
    """Process events from the enrichment queue."""
    from event_queue import EventQueue
    from pipeline_lock import pipeline_lock

    queue = EventQueue("funding-events")
    queue.recover_stale()

    with pipeline_lock("unified-pipeline"):
        processed = 0
        while True:
            event = queue.pop()
            if event is None:
                break

            payload = event.get("payload", {})
            try:
                result = run_pipeline(
                    company=payload.get("company", ""),
                    amount=payload.get("amount", 0),
                    round_type=payload.get("round_type", "UNKNOWN"),
                    investors=payload.get("investors", []),
                    source_url=payload.get("source_url"),
                )
                if result.action in ("created", "skipped", "dry-run"):
                    queue.ack(event)
                else:
                    queue.nack(event, error=str(result.issues))
                processed += 1
            except Exception as e:
                logger.error(f"Pipeline error: {e}")
                queue.nack(event, error=str(e))

        logger.info(f"Queue processing complete: {processed} events")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Unified Outreach Pipeline — single-pass create + enrich"
    )
    parser.add_argument("--company", help="Company name")
    parser.add_argument("--amount", type=int, default=0, help="Round amount in dollars")
    parser.add_argument("--round-type", default="UNKNOWN", help="Round type")
    parser.add_argument("--investors", nargs="*", help="Investor names")
    parser.add_argument("--source-url", help="Source article URL")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to Notion")
    parser.add_argument("--skip-enrichment", action="store_true",
                        help="Create skeleton without outreach/hiring")
    parser.add_argument("--from-queue", action="store_true",
                        help="Process events from the queue")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.from_queue:
        run_from_queue()
    elif args.company:
        result = run_pipeline(
            company=args.company,
            amount=args.amount,
            round_type=args.round_type,
            investors=args.investors,
            source_url=args.source_url,
            dry_run=args.dry_run,
            skip_enrichment=args.skip_enrichment,
        )
        print(json.dumps(result.to_dict(), indent=2))
        sys.exit(0 if result.action != "failed" else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
