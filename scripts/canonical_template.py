#!/usr/bin/env python3
"""
Canonical Page Template for FUNDRAISING INTEL pages in Report Base.

Defines the contract that ALL pipeline scripts must follow when creating
or updating FUNDRAISING INTEL pages. This is the single source of truth
for page structure.

Usage:
    from canonical_template import (
        build_canonical_blocks,
        build_page_properties,
        validate_page_structure,
        TEMPLATE_SECTIONS,
    )

    # Build blocks and properties for a new canonical page
    blocks = build_canonical_blocks(
        company="CROSSOVER MARKETS",
        round_amount=31000000,
        round_type="SERIES B",
        investors=["a16z", "Coinbase Ventures"],
        sources=[{"url": "https://...", "title": "Bloomberg"}],
    )
    props = build_page_properties(
        company="CROSSOVER MARKETS",
        round_amount=31000000,
        round_type="SERIES B",
        run_id="run-2026-03-09-001",
    )
    page = notion.pages.create(
        parent={"database_id": DB_ID}, properties=props, children=blocks,
    )

    # Validate that page content follows the template
    issues = validate_page_structure(page_content_text)

Deployment:
    Place at: /home/ubuntu/clawd/scripts/canonical_template.py
    Import from: funding-intel-brief.py
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("canonical_template")

# ---------------------------------------------------------------------------
# Template section definitions (order matters)
# ---------------------------------------------------------------------------

TEMPLATE_SECTIONS = [
    {
        "id": "deal_summary",
        "heading": "DEAL SUMMARY",
        "required": True,
        "writer": "funding-intel-brief",
        "description": "Company name, round details, investor list.",
    },
    {
        "id": "investors",
        "heading": "INVESTORS",
        "required": True,
        "writer": "funding-intel-brief",
        "description": "Bulleted list of investor/fund names. No person names.",
    },
    {
        "id": "sources",
        "heading": "SOURCES",
        "required": True,
        "writer": "funding-intel-brief",
        "description": "Bookmark embeds or URLs to source articles.",
    },
    {
        "id": "outreach",
        "heading": "OUTREACH INTEL (AUTO)",
        "required": False,
        "writer": "founder-intel-pipeline",
        "description": (
            "Auto-generated outreach section. Wrapped in "
            "[[OUTREACH_INTEL_AUTO_START]] / [[OUTREACH_INTEL_AUTO_END]] markers. "
            "Contains: Company Snapshot, POC, Contact Details, Outreach Priority, "
            "Outreach Angle, Research Notes."
        ),
        "marker_start": "[[OUTREACH_INTEL_AUTO_START]]",
        "marker_end": "[[OUTREACH_INTEL_AUTO_END]]",
    },
    {
        "id": "hiring",
        "heading": "HIRING INTELLIGENCE",
        "required": False,
        "writer": "hiring_intel_module (via founder-intel-pipeline)",
        "description": (
            "Auto-generated hiring intelligence. Wrapped in "
            "[[HIRING_INTEL_AUTO_START]] / [[HIRING_INTEL_AUTO_END]] markers. "
            "Contains: Job Postings, Hiring Signals, Team Analysis, "
            "Predicted Hiring Needs, Competitor Benchmark."
        ),
        "marker_start": "[[HIRING_INTEL_AUTO_START]]",
        "marker_end": "[[HIRING_INTEL_AUTO_END]]",
    },
]


# ---------------------------------------------------------------------------
# Report key generation
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


# ---------------------------------------------------------------------------
# Company name normalization (entity resolution)
# ---------------------------------------------------------------------------

# Common suffixes that should be stripped for slug matching
_STRIP_SUFFIXES = [
    "labs", "protocol", "finance", "network", "dao",
    "inc", "ltd", "co", "foundation", "ventures",
]

# Known company aliases → canonical slug
# Add entries here when duplicates are discovered
COMPANY_ALIASES: dict[str, str] = {
    # USD.AI variants (F-12, Mar 09 audit)
    "usd-ai-permian-labs": "usd-ai",
    "usdai-permian-labs": "usd-ai",
    "usdai": "usd-ai",
    "permian-labs": "usd-ai",
    "usd-ai-permian": "usd-ai",
    # LayerZero
    "layerzero-labs": "layerzero",
    # Izumi Finance variants
    "izumi-finance": "izumi",
    "izumi-fi": "izumi",
    # Crema Finance
    "crema-finance": "crema",
}


def normalize_company_slug(raw_name: str) -> str:
    """
    Normalize a company name to a canonical slug for dedup.

    Steps:
      1. Slugify the raw name.
      2. Check the alias table for a known mapping.
      3. Strip common suffixes (labs, protocol, finance, etc.)
         and check aliases again.
      4. Return the canonical slug.
    """
    slug = slugify(raw_name)

    # Direct alias match
    if slug in COMPANY_ALIASES:
        return COMPANY_ALIASES[slug]

    # Try stripping suffixes
    stripped = slug
    for suffix in _STRIP_SUFFIXES:
        if stripped.endswith(f"-{suffix}"):
            stripped = stripped[: -(len(suffix) + 1)]
            break  # Only strip one suffix to avoid over-stripping
    stripped = stripped.strip("-")

    # Check alias after stripping
    if stripped in COMPANY_ALIASES:
        return COMPANY_ALIASES[stripped]

    # If stripping produced a different slug, prefer the stripped version
    # only if the remaining part is meaningful (>= 3 chars) and isn't just
    # a common word fragment.
    if stripped != slug and len(stripped) >= 3:
        return stripped

    return slug


def slugs_likely_match(slug_a: str, slug_b: str) -> bool:
    """
    Check if two company slugs likely refer to the same entity.

    Uses normalization + prefix matching + simple edit distance.
    Catches cases like 'usd-ai' vs 'usd-ai-permian-labs' that
    alias tables might miss.
    """
    a = normalize_company_slug(slug_a)
    b = normalize_company_slug(slug_b)

    # Exact match after normalization
    if a == b:
        return True

    # One is a prefix of the other (e.g., "layerzero" vs "layerzero-labs")
    if a.startswith(b) or b.startswith(a):
        return True

    # Simple character-level similarity (Jaccard on character bigrams)
    if len(a) >= 3 and len(b) >= 3:
        bigrams_a = {a[i:i+2] for i in range(len(a) - 1)}
        bigrams_b = {b[i:i+2] for i in range(len(b) - 1)}
        if bigrams_a and bigrams_b:
            similarity = len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)
            if similarity >= 0.7:
                return True

    return False


def generate_report_key(company: str, amount: int) -> str:
    """Generate a v3 REPORT KEY for a fundraising intel page.

    Raises ValueError if amount is 0 or negative, since $0 keys immediately
    fail QA validation and break dedup matching.
    """
    if amount <= 0:
        raise ValueError(
            f"Cannot generate REPORT KEY with amount={amount} for '{company}'. "
            "A $0 amount means the source wasn't parsed correctly — fix upstream."
        )
    return f"fundraising-intel:v3:{normalize_company_slug(company)}:{amount}"


def generate_signal_pack_key(date_str: str) -> str:
    """Generate a REPORT KEY for a signal pack page."""
    return f"signal-pack:v1:{date_str}"


# ---------------------------------------------------------------------------
# Amount parsing and matching
# ---------------------------------------------------------------------------

def parse_amount(amount_str: str) -> int:
    """
    Parse a dollar amount string to integer.

    "$31M" -> 31000000
    "$4.25M" -> 4250000
    "$200M" -> 200000000
    "$1.5B" -> 1500000000
    """
    if not amount_str:
        return 0

    text = amount_str.strip().replace("$", "").replace(",", "")

    multipliers = {
        "B": 1_000_000_000,
        "M": 1_000_000,
        "K": 1_000,
    }

    for suffix, mult in multipliers.items():
        if text.upper().endswith(suffix):
            try:
                return int(float(text[:-1]) * mult)
            except ValueError:
                return 0

    try:
        return int(float(text))
    except ValueError:
        return 0


def amounts_match(a: int, b: int, tolerance: float = 0.05) -> bool:
    """
    Check if two amounts match within a tolerance (default ±5%).

    Handles the BLUPRYNT case where $4.2M vs $4.25M are the same deal.

    Two $0 amounts are treated as NON-matching because $0 means the amount
    couldn't be parsed — they could be completely different deals.
    """
    if a == 0 or b == 0:
        # $0 = unparsed amount — never match (even to another $0)
        return False
    return abs(a - b) / max(a, b) <= tolerance


# ---------------------------------------------------------------------------
# Page creation template
# ---------------------------------------------------------------------------

def build_canonical_blocks(
    company: str,
    round_amount: int,
    round_type: str,
    investors: list[str],
    sources: Optional[list[dict]] = None,
) -> list[dict]:
    """
    Build the initial Notion blocks for a canonical FUNDRAISING INTEL page.

    This creates the skeleton with placeholder sections for outreach and hiring.
    The founder-intel-pipeline fills these sections later via marker replacement.

    Args:
        company: Company name (e.g., "CROSSOVER MARKETS").
        round_amount: Dollar amount as integer (e.g., 31000000).
        round_type: Round type (e.g., "SERIES B", "SEED").
        investors: List of investor/fund names.
        sources: Optional list of {"url": str, "title": str} dicts.

    Returns:
        List of Notion block objects ready for pages.create().
    """
    amount_str = _format_amount(round_amount)
    blocks = []

    # --- Callout header ---
    blocks.append({
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "\U0001f4b0"},
            "color": "yellow_background",
            "rich_text": [{
                "type": "text",
                "text": {
                    "content": f"\U0001f4b0 {company} — {amount_str} {round_type} | Auto-detected by Funding Intel Pipeline"
                },
            }],
        },
    })

    # --- Divider ---
    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # --- DEAL SUMMARY ---
    blocks.append(_heading("DEAL SUMMARY", level=2))
    blocks.append(_bullet(f"Company: {company}"))
    blocks.append(_bullet(f"Round: {amount_str} {round_type}"))
    blocks.append(_bullet(f"Investors: {', '.join(investors) if investors else 'Unknown'}"))

    # --- INVESTORS ---
    blocks.append(_heading("INVESTORS", level=2))
    for inv in investors:
        blocks.append(_bullet(inv))
    if not investors:
        blocks.append(_bullet("No investors identified yet."))

    # --- SOURCES ---
    blocks.append(_heading("SOURCES", level=2))
    if sources:
        for src in sources:
            blocks.append({
                "object": "block",
                "type": "bookmark",
                "bookmark": {"url": src["url"]},
            })
    else:
        blocks.append(_paragraph("Sources pending."))

    # --- Divider before auto-sections ---
    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # --- OUTREACH placeholder with markers ---
    blocks.append(_paragraph("[[OUTREACH_INTEL_AUTO_START]]"))
    blocks.append(_paragraph(
        "Outreach enrichment pending — will be populated by founder-intel-pipeline."
    ))
    blocks.append(_paragraph("[[OUTREACH_INTEL_AUTO_END]]"))

    # --- Divider ---
    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # --- HIRING placeholder with markers ---
    blocks.append(_paragraph("[[HIRING_INTEL_AUTO_START]]"))
    blocks.append(_paragraph(
        "Hiring intelligence pending — will be populated by hiring_intel_module."
    ))
    blocks.append(_paragraph("[[HIRING_INTEL_AUTO_END]]"))

    return blocks


def build_page_properties(
    company: str,
    round_amount: int,
    round_type: str,
    run_id: str,
    poc_user_ids: Optional[list[str]] = None,
) -> dict:
    """
    Build the Notion page properties for a canonical FUNDRAISING INTEL page.

    Returns a dict suitable for notion.pages.create(properties=...).
    """
    now = datetime.now(timezone.utc)
    amount_str = _format_amount(round_amount)
    title = f"{company} — {amount_str} {round_type} | FUNDING INTEL | {now.strftime('%b').upper()} {now.strftime('%d').lstrip('0')}, {now.year}"

    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    props = {
        "ENTRY": {"title": [{"text": {"content": title}}]},
        "TYPE": {"select": {"name": "FUNDRAISING INTEL"}},
        "SOURCE SKILL": {"select": {"name": "funding-intel-brief"}},
        "REPORT KEY": {"rich_text": [{"text": {"content": generate_report_key(company, round_amount)}}]},
        "RUN ID": {"rich_text": [{"text": {"content": run_id}}]},
        "QA STATUS": {"select": {"name": "PASS"}},
        "QA ISSUES": {"rich_text": [{"text": {"content": ""}}]},
        "COMPANY": {"rich_text": [{"text": {"content": company}}]},
        "ROUND AMOUNT": {"number": round_amount},
        "DATE": {"date": {"start": now_iso}},
    }

    if poc_user_ids:
        props["POC"] = {"people": [{"id": uid} for uid in poc_user_ids]}

    return props


# ---------------------------------------------------------------------------
# Structure validation
# ---------------------------------------------------------------------------

def validate_page_structure(page_content: str) -> list[str]:
    """
    Validate that a page follows the canonical template structure.

    Returns a list of issues (empty list = valid).
    """
    issues = []

    for section in TEMPLATE_SECTIONS:
        heading = section["heading"]
        required = section["required"]

        # Check for heading presence
        if heading.upper() not in page_content.upper():
            if required:
                issues.append(f"Missing required section: {heading}")

        # Check for marker presence on auto-sections
        marker_start = section.get("marker_start")
        marker_end = section.get("marker_end")
        if marker_start:
            if marker_start not in page_content:
                issues.append(f"Missing start marker for {heading}: {marker_start}")
            if marker_end and marker_end not in page_content:
                issues.append(f"Missing end marker for {heading}: {marker_end}")

            # Check for duplicates
            if marker_start in page_content:
                count = page_content.count(marker_start)
                if count > 1:
                    issues.append(
                        f"Duplicate markers for {heading}: "
                        f"{count} instances of {marker_start}"
                    )

    return issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_amount(amount: int) -> str:
    """Format an integer amount as a display string.

    Preserves up to 3 significant digits to avoid precision loss
    (e.g., $4.25M stays $4.25M, not $4.2M).
    """
    if amount >= 1_000_000_000:
        val = amount / 1_000_000_000
        # Strip trailing zeros but keep meaningful decimals
        formatted = f"{val:.2f}".rstrip("0").rstrip(".")
        return f"${formatted}B"
    elif amount >= 1_000_000:
        val = amount / 1_000_000
        if val == int(val):
            return f"${int(val)}M"
        formatted = f"{val:.2f}".rstrip("0").rstrip(".")
        return f"${formatted}M"
    elif amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    elif amount > 0:
        return f"${amount:,}"
    return "$0"


def _heading(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
        },
    }


def _bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
        },
    }
