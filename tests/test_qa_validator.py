"""Tests for qa_validator.py pure functions (no Notion API calls)."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from qa_validator import (
    QAResult,
    validate_page,
    pre_write_validate,
    ERROR_RE,
)


# ---------------------------------------------------------------------------
# QAResult
# ---------------------------------------------------------------------------

def test_qa_result_starts_pass():
    qa = QAResult()
    assert qa.status == "PASS"
    assert qa.issues == []


def test_qa_result_warn():
    qa = QAResult()
    qa.warn("something minor")
    assert qa.status == "WARN"
    assert len(qa.issues) == 1


def test_qa_result_fail_overrides_warn():
    qa = QAResult()
    qa.warn("minor")
    qa.fail("major")
    assert qa.status == "FAIL"
    assert len(qa.issues) == 2


def test_qa_result_summary():
    qa = QAResult()
    assert qa.summary == "All checks passed."
    qa.warn("issue1")
    assert "issue1" in qa.summary


# ---------------------------------------------------------------------------
# ERROR_RE
# ---------------------------------------------------------------------------

def test_error_re_matches_duckduckgo():
    assert ERROR_RE.search("Search error: HTTPSConnectionPool(host='html.duckduckgo.com')")


def test_error_re_matches_grok():
    assert ERROR_RE.search("[Grok error: timeout]")


def test_error_re_no_false_positive():
    assert not ERROR_RE.search("The company raised $31M in Series B funding.")


# ---------------------------------------------------------------------------
# validate_page
# ---------------------------------------------------------------------------

def _make_props(report_key="fundraising-intel:v3:test:1000", source_skill="funding-intel-brief", run_id="run-001"):
    """Helper to build minimal page properties."""
    props = {}
    if report_key is not None:
        props["REPORT KEY"] = {"type": "rich_text", "rich_text": [{"plain_text": report_key}]}
    else:
        props["REPORT KEY"] = {"type": "rich_text", "rich_text": []}
    props["SOURCE SKILL"] = {"type": "rich_text", "rich_text": [{"plain_text": source_skill}]}
    props["RUN ID"] = {"type": "rich_text", "rich_text": [{"plain_text": run_id}]}
    return props


def test_validate_page_pass():
    props = _make_props()
    content = "Clean content with no errors."
    qa = validate_page(props, content, "FUNDRAISING INTEL")
    # May be WARN due to missing markers, but should NOT be FAIL
    assert qa.status in ("PASS", "WARN")


def test_validate_page_fail_empty_report_key():
    props = _make_props(report_key=None)
    content = "Some content."
    qa = validate_page(props, content, "FUNDRAISING INTEL")
    assert qa.status == "FAIL"
    assert any("REPORT KEY" in i for i in qa.issues)


def test_validate_page_fail_error_in_content():
    props = _make_props()
    content = "Results: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out"
    qa = validate_page(props, content, "FUNDRAISING INTEL")
    assert qa.status == "FAIL"
    assert any("Error text" in i for i in qa.issues)


def test_validate_page_warn_missing_source_skill():
    props = _make_props(source_skill="")
    content = "Clean content."
    qa = validate_page(props, content, "FUNDRAISING INTEL")
    assert any("SOURCE SKILL" in i for i in qa.issues)


def test_validate_page_fundraising_intel_with_markers():
    props = _make_props()
    content = """
## INVESTORS
- a16z
- Coinbase
- Dragonfly

[[OUTREACH_INTEL_AUTO_START]]
outreach
[[OUTREACH_INTEL_AUTO_END]]

[[HIRING_INTEL_AUTO_START]]
hiring
[[HIRING_INTEL_AUTO_END]]
""" + "x" * 500  # Ensure content > 500 chars
    qa = validate_page(props, content, "FUNDRAISING INTEL")
    # Should not warn about missing markers
    marker_warns = [i for i in qa.issues if "marker" in i.lower() or "enrichment" in i.lower() or "intelligence" in i.lower()]
    assert marker_warns == []


def test_validate_page_detects_stacked_markers():
    props = _make_props()
    content = """
[[OUTREACH_INTEL_AUTO_START]]
first
[[OUTREACH_INTEL_AUTO_END]]
[[OUTREACH_INTEL_AUTO_START]]
stacked!
[[OUTREACH_INTEL_AUTO_END]]
[[HIRING_INTEL_AUTO_START]]
hiring
[[HIRING_INTEL_AUTO_END]]
""" + "x" * 500
    qa = validate_page(props, content, "FUNDRAISING INTEL")
    assert qa.status == "FAIL"
    assert any("Stacked" in i for i in qa.issues)


# ---------------------------------------------------------------------------
# pre_write_validate
# ---------------------------------------------------------------------------

def test_pre_write_validate_basic():
    props = _make_props()
    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"plain_text": "Clean block content"}],
            },
        },
    ]
    qa = pre_write_validate(props, blocks, page_type="FUNDRAISING INTEL")
    # Should not FAIL (may WARN due to missing markers)
    assert qa.status in ("PASS", "WARN")


def test_pre_write_validate_catches_error_blocks():
    props = _make_props()
    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"plain_text": "Search error: HTTPSConnectionPool(host='html.duckduckgo.com')"}],
            },
        },
    ]
    qa = pre_write_validate(props, blocks, page_type="FUNDRAISING INTEL")
    assert qa.status == "FAIL"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
