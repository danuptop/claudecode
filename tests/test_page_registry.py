"""Tests for page_registry.py."""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from page_registry import PageRegistry


def test_register_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "registry.json")
        reg = PageRegistry(path)
        fp = PageRegistry.fingerprint("okx", "series-b", 200000000)
        reg.register(fp, "page-id-123", company="okx", round_type="series-b", amount=200000000)
        result = reg.get(fp)
        assert result is not None
        assert result["page_id"] == "page-id-123"


def test_get_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "registry.json")
        reg = PageRegistry(path)
        fp = PageRegistry.fingerprint("nonexistent", "seed", 1000000)
        assert reg.get(fp) is None


def test_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "registry.json")
        reg1 = PageRegistry(path)
        fp = PageRegistry.fingerprint("okx", "series-b", 200000000)
        reg1.register(fp, "page-id-456", company="okx")

        reg2 = PageRegistry(path)
        assert reg2.get(fp) is not None


def test_fingerprint_deterministic():
    fp1 = PageRegistry.fingerprint("okx", "series-b", 200000000)
    fp2 = PageRegistry.fingerprint("okx", "series-b", 200000000)
    assert fp1 == fp2


def test_get_by_company():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "registry.json")
        reg = PageRegistry(path)
        fp = PageRegistry.fingerprint("okx", "series-b", 200000000)
        reg.register(fp, "page-1", company="okx")
        results = reg.get_by_company("okx")
        assert len(results) >= 1


def test_unregister():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "registry.json")
        reg = PageRegistry(path)
        fp = PageRegistry.fingerprint("test", "seed", 5000000)
        reg.register(fp, "page-x", company="test")
        assert reg.get(fp) is not None
        reg.unregister(fp)
        assert reg.get(fp) is None


def test_size():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "registry.json")
        reg = PageRegistry(path)
        fp1 = PageRegistry.fingerprint("a", "seed", 1000000)
        fp2 = PageRegistry.fingerprint("b", "series-a", 5000000)
        reg.register(fp1, "p1")
        reg.register(fp2, "p2")
        assert reg.size == 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
