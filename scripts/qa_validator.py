#!/usr/bin/env python3
"""
Post-write QA Validator for Report Base pages.

Runs after any pipeline write and sets QA STATUS based on objective criteria.
This is the single most important fix — it makes all other problems visible.

Usage:
    # Validate a specific page after pipeline write
    python3 qa_validator.py --page-id <notion-page-id>

    # Validate all pages written in the last N hours
    python3 qa_validator.py --recent <hours>

    # Dry-run (report issues without updating QA STATUS)
    python3 qa_validator.py --page-id <id> --dry-run

Deployment:
    Place at: /home/ubuntu/clawd/scripts/qa_validator.py
    Call from: funding-intel-brief.py and founder-intel-pipeline.py
    after every page write/update.
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPORT_BASE_DB = os.getenv(
    "REPORT_BASE_DB", "902d47be-68c0-4da8-832a-a52272fc7b39"
)

# Error patterns that should NEVER appear in published page content
ERROR_PATTERNS = [
    r"HTTPSConnectionPool\(",
    r"Max retries exceeded",
    r"Read timed out",
    r"\[Grok error:",
    r"mcp_unavailable",
    r"ConnectionError\(",
    r"Traceback \(most recent call last\)",
    r"requests\.exceptions\.",
    r"TimeoutError",
    r"Search error: HTTPSConnectionPool",
]

ERROR_RE = re.compile("|".join(ERROR_PATTERNS), re.IGNORECASE)

# Marker pairs that should be present on enriched FUNDRAISING INTEL pages
REQUIRED_MARKER_PAIRS = {
    "outreach": (
        "[[OUTREACH_INTEL_AUTO_START]]",
        "[[OUTREACH_INTEL_AUTO_END]]",
    ),
    "hiring": (
        "[[HIRING_INTEL_AUTO_START]]",
        "[[HIRING_INTEL_AUTO_END]]",
    ),
}

logger = logging.getLogger("qa_validator")


# ---------------------------------------------------------------------------
# Notion client helper (uses the same client as the calling pipeline)
# ---------------------------------------------------------------------------

def get_notion_client():
    """Get Notion client. Import here to allow standalone testing."""
    try:
        from notion_client import Client

        token = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
        if not token:
            raise RuntimeError(
                "NOTION_TOKEN or NOTION_API_KEY environment variable required"
            )
        return Client(auth=token)
    except ImportError:
        raise RuntimeError("notion-client package required: pip install notion-client")


# ---------------------------------------------------------------------------
# Page content extraction
# ---------------------------------------------------------------------------

def extract_page_text(blocks: list, _notion=None) -> str:
    """Recursively extract all text from Notion blocks."""
    texts = []
    for block in blocks:
        block_type = block.get("type", "")
        type_data = block.get(block_type, {})

        # Extract rich_text from any block type
        rich_texts = type_data.get("rich_text", [])
        for rt in rich_texts:
            texts.append(rt.get("plain_text", ""))

        # Recurse into children
        if block.get("has_children"):
            try:
                if _notion is None:
                    _notion = get_notion_client()
                children = _notion.blocks.children.list(block_id=block["id"])
                texts.append(extract_page_text(children.get("results", []), _notion))
            except Exception:
                pass

    return "\n".join(texts)


def get_page_blocks(notion, page_id: str) -> list:
    """Fetch all blocks for a page, handling pagination."""
    blocks = []
    cursor = None
    while True:
        kwargs = {"block_id": page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.blocks.children.list(**kwargs)
        blocks.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return blocks


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

class QAResult:
    """Accumulates QA check results."""

    def __init__(self):
        self.issues: list[str] = []
        self.status: str = "PASS"

    def warn(self, msg: str):
        self.issues.append(f"[WARN] {msg}")
        if self.status == "PASS":
            self.status = "WARN"

    def fail(self, msg: str):
        self.issues.append(f"[FAIL] {msg}")
        self.status = "FAIL"

    @property
    def summary(self) -> str:
        if not self.issues:
            return "All checks passed."
        return " | ".join(self.issues)


def validate_page(
    page_props: dict, page_content: str, page_type: str
) -> QAResult:
    """
    Run all QA checks against a page.

    Args:
        page_props: Notion page properties (parsed).
        page_content: Full text content of the page.
        page_type: The TYPE select value (e.g., "FUNDRAISING INTEL").

    Returns:
        QAResult with accumulated status and issues.
    """
    result = QAResult()

    # ---- Check 1: REPORT KEY must be populated ----
    report_key = _get_text_prop(page_props, "REPORT KEY")
    if not report_key:
        result.fail("Empty REPORT KEY — page is invisible to dedup system.")

    # ---- Check 2: No error artifacts in content ----
    error_matches = ERROR_RE.findall(page_content)
    if error_matches:
        unique_errors = list(set(error_matches))[:3]
        result.fail(
            f"Error text in body: {', '.join(unique_errors)}"
        )

    # ---- Check 3: For FUNDRAISING INTEL — require markers or flag ----
    if page_type == "FUNDRAISING INTEL":
        _check_fundraising_intel(page_content, page_props, result)

    # ---- Check 4: For SIGNAL PACK — require minimum sections ----
    if page_type == "SIGNAL PACK":
        _check_signal_pack(page_content, result)

    # ---- Check 5: SOURCE SKILL must be populated ----
    source_skill = _get_text_prop(page_props, "SOURCE SKILL")
    if not source_skill:
        result.warn("Empty SOURCE SKILL — pipeline traceability lost.")

    # ---- Check 6: RUN ID should be populated ----
    run_id = _get_text_prop(page_props, "RUN ID")
    if not run_id:
        result.warn("Empty RUN ID — cannot trace to specific pipeline run.")

    return result


def _check_fundraising_intel(content: str, props: dict, result: QAResult):
    """FUNDRAISING INTEL-specific checks."""

    # Check for outreach markers
    has_outreach_start = "[[OUTREACH_INTEL_AUTO_START]]" in content
    has_outreach_end = "[[OUTREACH_INTEL_AUTO_END]]" in content
    has_hiring_start = "[[HIRING_INTEL_AUTO_START]]" in content
    has_hiring_end = "[[HIRING_INTEL_AUTO_END]]" in content

    if has_outreach_start != has_outreach_end:
        result.fail(
            "Unpaired outreach markers — START without END or vice versa."
        )
    if has_hiring_start != has_hiring_end:
        result.fail(
            "Unpaired hiring markers — START without END or vice versa."
        )

    if not has_outreach_start:
        result.warn("Missing outreach enrichment section (no markers found).")
    if not has_hiring_start:
        result.warn("Missing hiring intelligence section (no markers found).")

    # Check for duplicate markers (stacking bug)
    outreach_start_count = content.count("[[OUTREACH_INTEL_AUTO_START]]")
    hiring_start_count = content.count("[[HIRING_INTEL_AUTO_START]]")
    if outreach_start_count > 1:
        result.fail(
            f"Stacked outreach sections: {outreach_start_count} START markers found."
        )
    if hiring_start_count > 1:
        result.fail(
            f"Stacked hiring sections: {hiring_start_count} START markers found."
        )

    # Check investor count
    investor_section = _extract_section(content, "INVESTORS")
    if investor_section:
        investor_lines = [
            line.strip()
            for line in investor_section.split("\n")
            if line.strip().startswith("-") or line.strip().startswith("•")
        ]
        if len(investor_lines) < 1:
            result.warn("No investors listed.")
        elif len(investor_lines) < 3:
            result.warn(f"Only {len(investor_lines)} investor(s) listed — verify completeness.")

    # Check for mcp_unavailable noise
    mcp_count = content.count("mcp_unavailable")
    if mcp_count > 0:
        result.warn(
            f"Icebreaker MCP unavailable ({mcp_count} entries) — warm intro map is placeholder only."
        )

    # Check content length (bare skeletons are usually < 500 chars)
    if len(content.strip()) < 500:
        result.warn("Page content is very thin (<500 chars) — likely a bare skeleton.")


def _check_signal_pack(content: str, result: QAResult):
    """SIGNAL PACK-specific checks."""
    required_sections = ["MARKET SNAPSHOT", "SIGNAL"]
    for section in required_sections:
        if section.upper() not in content.upper():
            result.warn(f"Missing expected section: {section}")

    if len(content.strip()) < 300:
        result.warn("Signal Pack content is very thin (<300 chars).")


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

def _get_text_prop(props: dict, name: str) -> str:
    """Extract a text/rich_text property value."""
    prop = props.get(name, {})
    prop_type = prop.get("type", "")

    if prop_type == "rich_text":
        parts = prop.get("rich_text", [])
        return "".join(p.get("plain_text", "") for p in parts).strip()
    elif prop_type == "title":
        parts = prop.get("title", [])
        return "".join(p.get("plain_text", "") for p in parts).strip()
    elif prop_type == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""

    return ""


def _extract_section(content: str, heading: str) -> Optional[str]:
    """Extract text under a heading until the next heading."""
    pattern = rf"(?:^|\n)#+\s*.*{re.escape(heading)}.*\n(.*?)(?=\n#+\s|\Z)"
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Main: update Notion page QA STATUS based on validation
# ---------------------------------------------------------------------------

def validate_and_update(page_id: str, dry_run: bool = False) -> QAResult:
    """Validate a page and update its QA STATUS in Notion."""
    notion = get_notion_client()

    # Fetch page properties
    page = notion.pages.retrieve(page_id=page_id)
    props = page.get("properties", {})
    page_type = _get_text_prop(props, "TYPE")
    title = _get_text_prop(props, "ENTRY") or _get_text_prop(props, "title")

    # Skip archived pages
    if "[ARCHIVED" in title.upper():
        logger.info(f"Skipping archived page: {title}")
        qa = QAResult()
        qa.status = "SKIP"
        return qa

    # Fetch page content
    blocks = get_page_blocks(notion, page_id)
    content = extract_page_text(blocks)

    # Run validation
    qa = validate_page(props, content, page_type)

    logger.info(f"Page: {title}")
    logger.info(f"  QA STATUS: {qa.status}")
    logger.info(f"  Issues: {qa.summary}")

    if dry_run:
        logger.info("  (dry-run — not updating Notion)")
        return qa

    # Update page properties
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    update_props = {
        "QA STATUS": {"select": {"name": qa.status}},
        "QA ISSUES": {
            "rich_text": [{"text": {"content": qa.summary[:2000]}}]
        },
    }

    # Also set LAST AUDITED AT
    notion.pages.update(
        page_id=page_id,
        properties={
            **update_props,
            "LAST AUDITED AT": {
                "date": {"start": now_iso}
            },
        },
    )

    return qa


def validate_recent(hours: int = 24, dry_run: bool = False):
    """Validate all pages created/updated in the last N hours."""
    notion = get_notion_client()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Query pages with DATE after cutoff
    results = notion.databases.query(
        database_id=REPORT_BASE_DB,
        filter={
            "property": "DATE",
            "date": {"after": cutoff.isoformat()},
        },
        sorts=[{"property": "DATE", "direction": "descending"}],
    )

    pages = results.get("results", [])
    logger.info(f"Found {len(pages)} pages created in last {hours}h")

    stats = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for page in pages:
        page_id = page["id"]
        try:
            qa = validate_and_update(page_id, dry_run=dry_run)
            stats[qa.status] = stats.get(qa.status, 0) + 1
        except Exception as e:
            logger.error(f"Error validating {page_id}: {e}")

    logger.info(f"\nValidation summary: {json.dumps(stats, indent=2)}")
    return stats


# ---------------------------------------------------------------------------
# Integration hook — call from other pipeline scripts
# ---------------------------------------------------------------------------

def post_write_hook(page_id: str, dry_run: bool = False) -> str:
    """
    Call this after writing/updating a Notion page.

    Returns the QA STATUS string ("PASS", "WARN", "FAIL").

    Usage in pipeline scripts:
        from qa_validator import post_write_hook
        status = post_write_hook(page_id)
        if status == "FAIL":
            logger.error(f"Page {page_id} failed QA validation")
    """
    qa = validate_and_update(page_id, dry_run=dry_run)
    return qa.status


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Report Base QA Validator")
    parser.add_argument("--page-id", help="Validate a specific page by ID")
    parser.add_argument(
        "--recent",
        type=int,
        metavar="HOURS",
        help="Validate all pages from the last N hours",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report issues without updating Notion",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.page_id:
        validate_and_update(args.page_id, dry_run=args.dry_run)
    elif args.recent is not None:
        validate_recent(hours=args.recent, dry_run=args.dry_run)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
