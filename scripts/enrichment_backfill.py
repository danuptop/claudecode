#!/usr/bin/env python3
"""
Enrichment Backfill Detector.

Finds FUNDRAISING INTEL pages that were created but never enriched
(missing outreach/hiring markers), and queues them for re-enrichment.

Usage:
    # Find unenriched pages older than 24 hours
    python3 enrichment_backfill.py --scan --min-age 24

    # Find and auto-queue for enrichment
    python3 enrichment_backfill.py --scan --min-age 24 --enqueue

    # Dry-run (report without action)
    python3 enrichment_backfill.py --scan --min-age 24 --dry-run

As a module:
    from enrichment_backfill import find_unenriched_pages

    gaps = find_unenriched_pages(min_age_hours=24)
    for gap in gaps:
        print(f"{gap['title']} — missing: {gap['missing']}")

Deployment:
    Place at: /home/ubuntu/clawd/scripts/enrichment_backfill.py
    Depends on: event_queue.py, qa_validator.py
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("enrichment_backfill")

REPORT_BASE_DB = os.getenv(
    "REPORT_BASE_DB", "902d47be-68c0-4da8-832a-a52272fc7b39"
)


def _get_notion_client():
    try:
        from notion_client import Client
    except ImportError:
        raise RuntimeError("notion-client package required")
    token = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
    if not token:
        raise RuntimeError("NOTION_TOKEN required")
    return Client(auth=token)


def _extract_text(blocks: list) -> str:
    """Extract all text from Notion blocks."""
    texts = []
    for block in blocks:
        bt = block.get("type", "")
        for rt in block.get(bt, {}).get("rich_text", []):
            texts.append(rt.get("plain_text", ""))
    return "\n".join(texts)


def find_unenriched_pages(
    min_age_hours: int = 24,
    max_pages: int = 100,
) -> list[dict]:
    """
    Find FUNDRAISING INTEL pages missing enrichment markers.

    Returns list of dicts with: page_id, title, created_at, missing (list of
    which enrichments are missing: "outreach", "hiring", or both).
    """
    notion = _get_notion_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)

    # Query all FUNDRAISING INTEL pages created before the cutoff
    results = notion.databases.query(
        database_id=REPORT_BASE_DB,
        filter={
            "and": [
                {"property": "TYPE", "select": {"equals": "FUNDRAISING INTEL"}},
                {
                    "timestamp": "created_time",
                    "created_time": {"before": cutoff.isoformat()},
                },
            ]
        },
        page_size=max_pages,
    )

    gaps = []
    for page in results.get("results", []):
        page_id = page["id"]
        title_parts = page.get("properties", {}).get("ENTRY", {}).get("title", [])
        title = "".join(p.get("plain_text", "") for p in title_parts)

        # Skip archived pages
        if "[ARCHIVED" in title.upper():
            continue
        if page.get("archived", False):
            continue

        # Fetch page content
        try:
            blocks_resp = notion.blocks.children.list(block_id=page_id)
            content = _extract_text(blocks_resp.get("results", []))
        except Exception as e:
            logger.warning(f"Could not fetch blocks for {page_id}: {e}")
            continue

        missing = []
        if "[[OUTREACH_INTEL_AUTO_START]]" not in content:
            missing.append("outreach")
        elif "Outreach enrichment pending" in content:
            missing.append("outreach")  # Has placeholder but no real content

        if "[[HIRING_INTEL_AUTO_START]]" not in content:
            missing.append("hiring")
        elif "Hiring intelligence pending" in content:
            missing.append("hiring")  # Has placeholder but no real content

        if missing:
            gaps.append({
                "page_id": page_id,
                "title": title,
                "created_at": page.get("created_time", ""),
                "missing": missing,
            })

    logger.info(f"Found {len(gaps)} unenriched pages (of {len(results.get('results', []))} checked)")
    return gaps


def enqueue_for_enrichment(gaps: list[dict]):
    """Push unenriched pages into the enrichment event queue."""
    try:
        from event_queue import EventQueue
    except ImportError:
        logger.error("event_queue module not found — cannot enqueue")
        return

    queue = EventQueue("enrichment-pending")
    for gap in gaps:
        queue.push({
            "page_id": gap["page_id"],
            "title": gap["title"],
            "missing": gap["missing"],
            "source": "backfill-detector",
        })
    logger.info(f"Enqueued {len(gaps)} pages for enrichment")


def main():
    parser = argparse.ArgumentParser(description="Enrichment Backfill Detector")
    parser.add_argument("--scan", action="store_true", help="Scan for unenriched pages")
    parser.add_argument("--min-age", type=int, default=24, metavar="HOURS",
                        help="Minimum page age in hours (default: 24)")
    parser.add_argument("--enqueue", action="store_true",
                        help="Auto-queue gaps for enrichment")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report without taking action")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.scan:
        gaps = find_unenriched_pages(min_age_hours=args.min_age)
        if not gaps:
            print("No unenriched pages found.")
            return

        print(f"\n{'='*60}")
        print(f"Found {len(gaps)} pages missing enrichment:")
        print(f"{'='*60}")
        for gap in gaps:
            missing_str = ", ".join(gap["missing"])
            print(f"  [{missing_str:>16}]  {gap['title'][:50]}")
            print(f"                     id={gap['page_id'][:12]}... created={gap['created_at'][:10]}")

        if args.enqueue and not args.dry_run:
            enqueue_for_enrichment(gaps)
        elif args.dry_run:
            print(f"\n(dry-run — would enqueue {len(gaps)} pages)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
