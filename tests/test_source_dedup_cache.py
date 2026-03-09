"""Tests for source_dedup_cache.py."""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from source_dedup_cache import SourceDedupCache


def test_url_not_duplicate_initially():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cache.json")
        cache = SourceDedupCache(path)
        assert cache.is_duplicate(source_url="https://example.com/article1") is False


def test_url_duplicate_after_record():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cache.json")
        cache = SourceDedupCache(path)
        cache.record(source_url="https://example.com/article1")
        assert cache.is_duplicate(source_url="https://example.com/article1") is True


def test_company_amount_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cache.json")
        cache = SourceDedupCache(path)
        cache.record(company="OKX", amount=200000000, page_id="page-1")
        assert cache.is_duplicate(company="OKX", amount=200000000) is True


def test_get_page_id():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cache.json")
        cache = SourceDedupCache(path)
        cache.record(source_url="https://example.com/a", page_id="page-xyz")
        result = cache.get_page_id(source_url="https://example.com/a")
        assert result == "page-xyz"


def test_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cache.json")
        cache1 = SourceDedupCache(path)
        cache1.record(source_url="https://example.com/a")

        cache2 = SourceDedupCache(path)
        assert cache2.is_duplicate(source_url="https://example.com/a") is True


def test_size():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cache.json")
        cache = SourceDedupCache(path)
        cache.record(source_url="https://example.com/1")
        cache.record(source_url="https://example.com/2")
        assert cache.size >= 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
