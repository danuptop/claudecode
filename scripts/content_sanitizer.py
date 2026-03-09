#!/usr/bin/env python3
"""
Content Sanitizer for Report Base pages.

Strips error artifacts, raw exceptions, and broken integration output
from Notion page content before publishing. Call this as a pre-write
filter in any pipeline script.

Usage as a module:
    from content_sanitizer import sanitize_blocks, sanitize_text

    # Sanitize Notion blocks before writing
    clean_blocks = sanitize_blocks(blocks)

    # Sanitize plain text
    clean_text = sanitize_text(raw_text)

Usage as CLI:
    # Clean a specific page in-place
    python3 content_sanitizer.py --page-id <notion-page-id>

    # Dry-run to see what would be cleaned
    python3 content_sanitizer.py --page-id <id> --dry-run

Deployment:
    Place at: /home/ubuntu/clawd/scripts/content_sanitizer.py
    Import from: funding-intel-brief.py, founder-intel-pipeline.py,
                 hiring_intel_module.py
"""

import argparse
import logging
import os
import re
import sys
import time
from typing import Optional

logger = logging.getLogger("content_sanitizer")

# ---------------------------------------------------------------------------
# Error patterns to detect — import from qa_validator as single source of truth.
# Fallback to local definition if qa_validator is not available (standalone use).
# ---------------------------------------------------------------------------

try:
    from qa_validator import ERROR_PATTERNS, ERROR_RE
except ImportError:
    # Standalone fallback — keep in sync with qa_validator.ERROR_PATTERNS.
    # Expected count: 10 patterns. If qa_validator adds patterns and this
    # fallback is used, the count mismatch will be caught at import time
    # once the import succeeds again.
    _FALLBACK_ERROR_PATTERN_COUNT = 10  # bump when adding patterns

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
    assert len(ERROR_PATTERNS) == _FALLBACK_ERROR_PATTERN_COUNT, (
        f"ERROR_PATTERNS fallback has {len(ERROR_PATTERNS)} entries, "
        f"expected {_FALLBACK_ERROR_PATTERN_COUNT}. Sync with qa_validator.py."
    )
    ERROR_RE = re.compile("|".join(ERROR_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# Error patterns to strip from content
# ---------------------------------------------------------------------------

# Patterns that match entire lines to remove.
# IMPORTANT: These use re.MULTILINE (^ and $ match line boundaries) but NOT
# re.DOTALL, so .* stays within single lines and won't eat adjacent content.
LINE_REMOVAL_PATTERNS = [
    # DuckDuckGo connection errors embedded as hiring signals
    r"^.*Search error: HTTPSConnectionPool\(host='html\.duckduckgo\.com'.*$",
    # Grok API timeout errors in competitor benchmarks
    r"^.*\[Grok error: HTTPSConnectionPool\(host='api\.x\.ai'.*?\].*$",
    # Generic Python connection errors
    r"^.*HTTPSConnectionPool\(host=.*?Max retries exceeded.*$",
    r"^.*requests\.exceptions\.\w+Error.*$",
    r"^.*ConnectionError\(MaxRetryError.*$",
]

# Traceback pattern — separate because it DOES need to span multiple lines.
# Matches from "Traceback" through all indented continuation lines and the
# final exception line (e.g., "ValueError: ...").
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):\n(?:[ \t]+.*\n)*\w[\w.]*(?:Error|Exception).*",
    re.MULTILINE,
)

# Patterns for inline replacement (replace match with fallback text)
INLINE_REPLACEMENTS = [
    # mcp_unavailable entries in warm intro map
    (
        r"- Status: mcp_unavailable\n- Notes: Icebreaker MCP URL not configured[^\n]*",
        "- Status: Pending — Icebreaker integration not yet configured.",
    ),
    # Truncated Grok responses
    (
        r"\[Grok error:[^\]]*\]",
        "[Analysis unavailable — API timeout. Manual review recommended.]",
    ),
    # DuckDuckGo errors appearing as signal text
    (
        r"Search error: HTTPSConnectionPool[^\n]*",
        "[Search unavailable — service timeout.]",
    ),
]

# Compiled patterns — use MULTILINE only (NOT DOTALL) so .* stays per-line
_line_removal_re = [re.compile(p, re.MULTILINE) for p in LINE_REMOVAL_PATTERNS]
_inline_replacements = [(re.compile(p), r) for p, r in INLINE_REPLACEMENTS]


# ---------------------------------------------------------------------------
# Investor list sanitizer
# ---------------------------------------------------------------------------

ROLE_INDICATORS = [
    "co-founder", "cofounder", "ceo", "cto", "cfo", "coo",
    "founder", "partner", "managing director", "president",
    "vice president", "head of", "director of",
]

# Patterns that indicate a role when used as the full entry (not a prefix
# of a fund name like "VP Ventures")
_ROLE_ONLY_PREFIXES = ["vp of ", "vp, ", "vp -"]


def sanitize_investor_list(investors: list[str]) -> list[str]:
    """
    Remove person-name artifacts that leaked into investor lists.

    Filters out entries like:
    - "Alex Wilson (co-founder"
    - "Cyclops)"
    - "David Choi (CEO/co-founder"
    - "Permian Labs)"
    - "Conor Moore"  (person name without company context)

    Args:
        investors: Raw list of investor strings.

    Returns:
        Cleaned list with only company/fund names.
    """
    cleaned = []
    for inv in investors:
        inv = inv.strip()
        if not inv:
            continue

        inv_lower = inv.lower()

        # Skip entries starting with "(" — orphaned role fragments
        if inv.startswith("("):
            continue

        # Skip entries ending with ")" without opening "(" — orphaned closers
        if inv.endswith(")") and "(" not in inv:
            continue

        # Skip entries with parenthetical roles: "Name (role..."
        if "(" in inv and any(role in inv_lower for role in ROLE_INDICATORS):
            continue

        # Skip entries that are JUST a role indicator
        if any(inv_lower == role or inv_lower.startswith(role + " ") for role in ROLE_INDICATORS):
            continue

        # Skip entries that start with VP-as-role patterns (but not fund names
        # like "VP Ventures", "VP Capital")
        if any(inv_lower.startswith(p) for p in _ROLE_ONLY_PREFIXES):
            continue

        cleaned.append(inv)

    # Dedupe preserving order
    seen = set()
    result = []
    for inv in cleaned:
        key = inv.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(inv)

    return result


# ---------------------------------------------------------------------------
# Text sanitizer
# ---------------------------------------------------------------------------

def sanitize_text(text: str) -> str:
    """
    Clean error artifacts from text content.

    Args:
        text: Raw text that may contain error artifacts.

    Returns:
        Cleaned text with errors replaced by clean fallbacks.
    """
    # Remove full tracebacks first (multi-line pattern)
    text = _TRACEBACK_RE.sub("", text)

    # Apply single-line removals
    for pattern in _line_removal_re:
        text = pattern.sub("", text)

    # Apply inline replacements
    for pattern, replacement in _inline_replacements:
        text = pattern.sub(replacement, text)

    # Clean up excessive blank lines left by removals
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Block sanitizer (for Notion API blocks)
# ---------------------------------------------------------------------------

def sanitize_blocks(blocks: list[dict]) -> list[dict]:
    """
    Clean error artifacts from a list of Notion blocks.

    Removes blocks that are entirely error text, and cleans
    inline errors from blocks that have mixed content.

    Args:
        blocks: List of Notion block objects.

    Returns:
        Cleaned list of blocks.
    """
    cleaned = []
    for block in blocks:
        sanitized = _sanitize_block(block)
        if sanitized is not None:
            cleaned.append(sanitized)
    return cleaned


def _sanitize_block(block: dict) -> Optional[dict]:
    """Sanitize a single block. Returns None to remove the block entirely."""
    block_type = block.get("type", "")
    type_data = block.get(block_type, {})

    # Extract text from rich_text array
    rich_texts = type_data.get("rich_text", [])
    if not rich_texts:
        return block  # No text content — pass through

    full_text = "".join(rt.get("plain_text", "") for rt in rich_texts)

    # Check if entire block is an error — remove it
    if _is_error_only(full_text):
        logger.debug(f"Removing error block: {full_text[:80]}...")
        return None

    # Otherwise, clean inline errors
    cleaned_text = sanitize_text(full_text)
    if cleaned_text == full_text:
        return block  # No changes needed

    # Rebuild rich_text — try to preserve formatting on unmodified spans
    if cleaned_text.strip():
        new_block = {**block}
        new_rich_texts = _rebuild_rich_text(rich_texts, cleaned_text)
        new_block[block_type] = {
            **type_data,
            "rich_text": new_rich_texts,
        }
        return new_block
    else:
        return None  # Block became empty after cleaning


def _rebuild_rich_text(original_spans: list[dict], cleaned_text: str) -> list[dict]:
    """
    Attempt to preserve formatting from original rich_text spans.

    If the cleaned text is a substring of one original span, preserve that
    span's annotations. Otherwise fall back to a single plain text span.
    """
    # Fast path: if only one span, just update its text
    if len(original_spans) == 1:
        span = {**original_spans[0]}
        text_data = {**span.get("text", {}), "content": cleaned_text}
        span["text"] = text_data
        if "plain_text" in span:
            span["plain_text"] = cleaned_text
        return [span]

    # Try to rebuild by cleaning each span individually
    rebuilt = []
    for span in original_spans:
        span_text = span.get("plain_text", span.get("text", {}).get("content", ""))
        clean_span = sanitize_text(span_text)
        if clean_span.strip():
            new_span = {**span}
            new_span["text"] = {**span.get("text", {}), "content": clean_span}
            if "plain_text" in new_span:
                new_span["plain_text"] = clean_span
            rebuilt.append(new_span)

    if rebuilt:
        return rebuilt

    # Fallback: single plain text span
    return [{"type": "text", "text": {"content": cleaned_text}}]


def _is_error_only(text: str) -> bool:
    """Check if a text string is entirely error content."""
    text = text.strip()
    if not text:
        return False

    error_only_patterns = [
        r"^Search error: HTTPSConnectionPool.*$",
        r"^\[Grok error:.*\]$",
        r"^mcp_unavailable$",
        r"^Traceback \(most recent call last\):",
        r"^requests\.exceptions\.",
        r"^HTTPSConnectionPool\(host=.*Max retries exceeded",
    ]
    return any(re.match(p, text, re.DOTALL) for p in error_only_patterns)


# ---------------------------------------------------------------------------
# Icebreaker MCP section cleaner
# ---------------------------------------------------------------------------

def clean_mcp_unavailable_section(page_content: str) -> str:
    """
    Replace broken Icebreaker MCP warm intro map with a clean placeholder.

    Detects sections with multiple 'mcp_unavailable' entries and replaces
    the entire warm intro map with a single clean note.
    """
    mcp_count = page_content.count("mcp_unavailable")
    if mcp_count == 0:
        return page_content

    # Find and replace the INVESTOR & ANGEL WARM INTRO MAP section
    pattern = (
        r"(##\s*[^\n]*INVESTOR[^\n]*WARM INTRO MAP[^\n]*\n)"  # Section heading
        r"((?:(?!##\s).*\n)*)"  # Content lines (stop at next ## heading)
    )

    def replacement(match):
        heading = match.group(1)
        content = match.group(2)
        if "mcp_unavailable" in content:
            return (
                heading
                + "> Warm intro mapping pending — Icebreaker MCP integration "
                + "not yet configured. Manual warm intro research recommended.\n\n"
            )
        return match.group(0)

    return re.sub(pattern, replacement, page_content, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# CLI: Clean a specific Notion page
# ---------------------------------------------------------------------------

def clean_page(page_id: str, dry_run: bool = False):
    """Fetch a Notion page, sanitize its content, and update it."""
    try:
        from notion_client import Client
    except ImportError:
        logger.error("notion-client package required: pip install notion-client")
        sys.exit(1)

    token = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
    if not token:
        logger.error("NOTION_TOKEN or NOTION_API_KEY environment variable required")
        sys.exit(1)
    notion = Client(auth=token)

    page = notion.pages.retrieve(page_id=page_id)
    props = page.get("properties", {})
    title_parts = props.get("ENTRY", {}).get("title", [])
    title = "".join(p.get("plain_text", "") for p in title_parts)

    logger.info(f"Processing: {title}")

    # Fetch blocks
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

    # Count errors before
    full_text = ""
    for block in blocks:
        bt = block.get("type", "")
        rich_texts = block.get(bt, {}).get("rich_text", [])
        full_text += "".join(rt.get("plain_text", "") for rt in rich_texts) + "\n"

    error_count = len(ERROR_RE.findall(full_text))
    logger.info(f"  Found {error_count} error artifacts")

    if error_count == 0:
        logger.info("  No errors to clean — skipping")
        return

    if dry_run:
        logger.info("  (dry-run — not modifying page)")
        return

    # Sanitize and update individual blocks
    cleaned_count = 0
    removed_count = 0
    for block in blocks:
        bt = block.get("type", "")
        rich_texts = block.get(bt, {}).get("rich_text", [])
        if not rich_texts:
            continue

        original = "".join(rt.get("plain_text", "") for rt in rich_texts)
        if not ERROR_RE.search(original):
            continue

        cleaned = sanitize_text(original)
        if not cleaned.strip():
            # Block became empty — delete it
            try:
                notion.blocks.delete(block_id=block["id"])
                removed_count += 1
                time.sleep(0.4)  # rate limit: ~3 req/s
            except Exception as e:
                logger.warning(f"  Could not delete block {block['id']}: {e}")
        elif cleaned != original:
            # Update block with cleaned content
            try:
                notion.blocks.update(
                    block_id=block["id"],
                    **{
                        bt: {
                            "rich_text": [
                                {"type": "text", "text": {"content": cleaned}}
                            ]
                        }
                    },
                )
                cleaned_count += 1
                time.sleep(0.4)  # rate limit: ~3 req/s
            except Exception as e:
                logger.warning(f"  Could not update block {block['id']}: {e}")

    logger.info(f"  Cleaned {cleaned_count} blocks, removed {removed_count} blocks")


def main():
    parser = argparse.ArgumentParser(description="Content Sanitizer for Report Base")
    parser.add_argument("--page-id", help="Clean a specific page by ID")
    parser.add_argument("--dry-run", action="store_true", help="Report without modifying")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.page_id:
        clean_page(args.page_id, dry_run=args.dry_run)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
