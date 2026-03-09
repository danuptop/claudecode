"""Tests for amount_parser.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from amount_parser import extract_amount


def test_parse_millions():
    r = extract_amount("$31M", use_llm=False)
    assert r["amount"] == 31_000_000
    assert r["source"] == "regex"


def test_parse_decimal_millions():
    r = extract_amount("$4.25M", use_llm=False)
    assert r["amount"] == 4_250_000


def test_parse_billions():
    r = extract_amount("$1.5B", use_llm=False)
    assert r["amount"] == 1_500_000_000


def test_parse_thousands():
    r = extract_amount("$500K", use_llm=False)
    assert r["amount"] == 500_000


def test_parse_empty():
    r = extract_amount("", use_llm=False)
    assert r["amount"] == 0
    assert r["confidence"] == "low"


def test_parse_garbage():
    r = extract_amount("undisclosed", use_llm=False)
    assert r["amount"] == 0


def test_parse_million_word():
    r = extract_amount("$31 million", use_llm=False)
    assert r["amount"] == 31_000_000


def test_parse_billion_word():
    r = extract_amount("$2 billion", use_llm=False)
    assert r["amount"] == 2_000_000_000


def test_result_has_required_keys():
    r = extract_amount("$10M Series A", use_llm=False)
    assert "amount" in r
    assert "round_type" in r
    assert "confidence" in r
    assert "source" in r


def test_round_type_extracted():
    r = extract_amount("raised $10M in Series A funding", use_llm=False)
    assert r["round_type"] == "SERIES A"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
