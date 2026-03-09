"""Tests for content_sanitizer.py pure functions."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from content_sanitizer import (
    sanitize_investor_list,
    sanitize_text,
    sanitize_blocks,
    _is_error_only,
    ERROR_RE,
)


# ---------------------------------------------------------------------------
# sanitize_investor_list
# ---------------------------------------------------------------------------

def test_clean_investors_pass_through():
    investors = ["a16z", "Coinbase Ventures", "Dragonfly"]
    assert sanitize_investor_list(investors) == investors


def test_filter_cofounder_fragment():
    investors = ["Castle Island", "Alex Wilson (co-founder", "Cyclops)", "Stripe"]
    result = sanitize_investor_list(investors)
    assert "Alex Wilson (co-founder" not in result
    assert "Cyclops)" not in result
    assert "Castle Island" in result
    assert "Stripe" in result


def test_filter_ceo_fragment():
    investors = ["Arbitrum", "David Choi (CEO/co-founder", "Permian Labs)", "Dragonfly"]
    result = sanitize_investor_list(investors)
    assert "David Choi (CEO/co-founder" not in result
    assert "Permian Labs)" not in result
    assert "Arbitrum" in result
    assert "Dragonfly" in result


def test_filter_orphaned_paren():
    investors = ["(partner of XYZ", "Real Fund"]
    result = sanitize_investor_list(investors)
    assert "(partner of XYZ" not in result
    assert "Real Fund" in result


def test_dedup_preserves_order():
    investors = ["a16z", "Coinbase", "a16z", "Coinbase"]
    result = sanitize_investor_list(investors)
    assert result == ["a16z", "Coinbase"]


def test_empty_entries_removed():
    investors = ["", "  ", "a16z", ""]
    result = sanitize_investor_list(investors)
    assert result == ["a16z"]


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------

def test_sanitize_text_removes_duckduckgo_error():
    # Line removal patterns with re.DOTALL match greedily across the whole text.
    # The key guarantee: error text is removed from the output.
    text = "Search error: HTTPSConnectionPool(host='html.duckduckgo.com', port=443): Max retries exceeded"
    result = sanitize_text(text)
    assert "HTTPSConnectionPool" not in result


def test_sanitize_text_replaces_grok_error_inline():
    # Inline replacement: [Grok error: ...] → clean fallback
    text = "[Grok error: Read timed out.]"
    result = sanitize_text(text)
    assert "Grok error" not in result
    assert "Manual review recommended" in result


def test_sanitize_text_clean_passthrough():
    text = "This is perfectly clean content with no errors."
    assert sanitize_text(text) == text


# ---------------------------------------------------------------------------
# _is_error_only
# ---------------------------------------------------------------------------

def test_is_error_only_duckduckgo():
    assert _is_error_only("Search error: HTTPSConnectionPool(host='html.duckduckgo.com', port=443): Max retries exceeded") is True


def test_is_error_only_grok():
    assert _is_error_only("[Grok error: Read timed out.]") is True


def test_is_error_only_mcp():
    assert _is_error_only("mcp_unavailable") is True


def test_is_error_only_clean_text():
    assert _is_error_only("This is legitimate content about the company.") is False


def test_is_error_only_empty():
    assert _is_error_only("") is False


# ---------------------------------------------------------------------------
# ERROR_RE
# ---------------------------------------------------------------------------

def test_error_re_detects_patterns():
    assert ERROR_RE.search("HTTPSConnectionPool(host='example.com')")
    assert ERROR_RE.search("Max retries exceeded")
    assert ERROR_RE.search("[Grok error: timeout]")
    assert ERROR_RE.search("mcp_unavailable")
    assert not ERROR_RE.search("This is clean text")


# ---------------------------------------------------------------------------
# sanitize_blocks
# ---------------------------------------------------------------------------

def test_sanitize_blocks_removes_error_block():
    blocks = [
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"plain_text": "Search error: HTTPSConnectionPool(host='html.duckduckgo.com', port=443): Max retries exceeded"}]
            },
        },
        {
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"plain_text": "Clean content here."}]
            },
        },
    ]
    result = sanitize_blocks(blocks)
    assert len(result) == 1
    assert result[0]["paragraph"]["rich_text"][0]["plain_text"] == "Clean content here."


def test_sanitize_blocks_preserves_non_text():
    blocks = [
        {"type": "divider", "divider": {}},
        {"type": "bookmark", "bookmark": {"url": "https://example.com"}},
    ]
    result = sanitize_blocks(blocks)
    assert len(result) == 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
