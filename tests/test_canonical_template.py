"""Tests for canonical_template.py pure functions."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from canonical_template import (
    slugify,
    generate_report_key,
    generate_signal_pack_key,
    parse_amount,
    amounts_match,
    validate_page_structure,
    build_canonical_blocks,
    build_page_properties,
    canonicalize_company,
)


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert slugify("CROSSOVER MARKETS") == "crossover-markets"


def test_slugify_special_chars():
    assert slugify("USD.AI") == "usdai"


def test_slugify_already_slug():
    assert slugify("okx") == "okx"


def test_slugify_extra_spaces():
    assert slugify("  TAPIOCA  DAO  ") == "tapioca-dao"


# ---------------------------------------------------------------------------
# generate_report_key
# ---------------------------------------------------------------------------

def test_report_key_v3():
    assert generate_report_key("OKX", 200000000) == "fundraising-intel:v3:okx:200000000"


def test_report_key_with_spaces():
    assert generate_report_key("CROSSOVER MARKETS", 31000000) == "fundraising-intel:v3:crossover-markets:31000000"


def test_signal_pack_key():
    assert generate_signal_pack_key("2026-03-08") == "signal-pack:v1:2026-03-08"


# ---------------------------------------------------------------------------
# parse_amount
# ---------------------------------------------------------------------------

def test_parse_amount_millions():
    assert parse_amount("$31M") == 31000000


def test_parse_amount_decimal_millions():
    assert parse_amount("$4.25M") == 4250000


def test_parse_amount_billions():
    assert parse_amount("$1.5B") == 1500000000


def test_parse_amount_thousands():
    assert parse_amount("$500K") == 500000


def test_parse_amount_raw_number():
    assert parse_amount("200000000") == 200000000


def test_parse_amount_empty():
    assert parse_amount("") == 0


def test_parse_amount_none_equivalent():
    assert parse_amount("garbage") == 0


# ---------------------------------------------------------------------------
# amounts_match
# ---------------------------------------------------------------------------

def test_amounts_match_exact():
    assert amounts_match(31000000, 31000000) is True


def test_amounts_match_within_tolerance():
    # $4.2M vs $4.25M — within 5%
    assert amounts_match(4200000, 4250000) is True


def test_amounts_match_outside_tolerance():
    # $4M vs $8M — way outside 5%
    assert amounts_match(4000000, 8000000) is False


def test_amounts_match_zero_vs_nonzero():
    assert amounts_match(0, 31000000) is False


def test_amounts_match_zero_vs_zero():
    assert amounts_match(0, 0) is True


# ---------------------------------------------------------------------------
# validate_page_structure
# ---------------------------------------------------------------------------

def test_validate_complete_page():
    content = """
## DEAL SUMMARY
Company info here

## INVESTORS
- a16z
- Coinbase Ventures

## SOURCES
https://example.com

[[OUTREACH_INTEL_AUTO_START]]
outreach content
[[OUTREACH_INTEL_AUTO_END]]

[[HIRING_INTEL_AUTO_START]]
hiring content
[[HIRING_INTEL_AUTO_END]]
"""
    issues = validate_page_structure(content)
    assert issues == []


def test_validate_missing_required_section():
    content = """
## DEAL SUMMARY
Company info here

## SOURCES
https://example.com
"""
    issues = validate_page_structure(content)
    assert any("INVESTORS" in i for i in issues)


def test_validate_duplicate_markers():
    content = """
## DEAL SUMMARY
## INVESTORS
## SOURCES
[[OUTREACH_INTEL_AUTO_START]]
first
[[OUTREACH_INTEL_AUTO_END]]
[[OUTREACH_INTEL_AUTO_START]]
second — stacked!
[[OUTREACH_INTEL_AUTO_END]]
[[HIRING_INTEL_AUTO_START]]
hiring
[[HIRING_INTEL_AUTO_END]]
"""
    issues = validate_page_structure(content)
    assert any("Duplicate" in i for i in issues)


# ---------------------------------------------------------------------------
# build_canonical_blocks / build_page_properties
# ---------------------------------------------------------------------------

def test_build_canonical_blocks_has_markers():
    blocks = build_canonical_blocks(
        company="TEST CO",
        round_amount=10000000,
        round_type="SEED",
        investors=["VC Fund A"],
    )
    text = " ".join(
        rt.get("plain_text", rt.get("text", {}).get("content", ""))
        for b in blocks
        for rt in b.get(b.get("type", ""), {}).get("rich_text", [])
    )
    assert "[[OUTREACH_INTEL_AUTO_START]]" in text
    assert "[[HIRING_INTEL_AUTO_END]]" in text


def test_build_page_properties_has_report_key():
    props = build_page_properties(
        company="TEST CO",
        round_amount=10000000,
        round_type="SEED",
        run_id="run-123",
    )
    report_key = props["REPORT KEY"]["rich_text"][0]["text"]["content"]
    assert report_key == "fundraising-intel:v3:test-co:10000000"
    assert props["TYPE"]["select"]["name"] == "FUNDRAISING INTEL"
    assert props["ROUND AMOUNT"]["number"] == 10000000


# ---------------------------------------------------------------------------
# canonicalize_company
# ---------------------------------------------------------------------------

def test_canonicalize_known_alias():
    # "okx exchange" should map to "okx" via aliases
    result = canonicalize_company("OKX Exchange")
    assert result == "okx"


def test_canonicalize_unknown_falls_back_to_slugify():
    result = canonicalize_company("BRAND NEW STARTUP")
    assert result == "brand-new-startup"


def test_canonicalize_case_insensitive():
    result = canonicalize_company("okx")
    assert result == "okx"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
