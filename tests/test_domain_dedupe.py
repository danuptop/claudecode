#!/usr/bin/env python3
"""
Tests for domain-based deduplication.

Covers the regression cases from the handoff prompt:
- SECURITIZE canonical domain must be securitize.io (not jane.ma)
- REAL and RE.AL split into separate domain-backed entities
- GOGOPOOL split into gogopool.com and avantprotocol.com variants
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hirepulse.models import CompanyEntity, EntitySource
from hirepulse.hirepulse_ingest import dedupe_by_domain


class TestDomainDedupeBlockedFiltering:
    """Blocked-domain filtering tests."""

    def test_twitter_filtered(self):
        entity = CompanyEntity(
            name="Test Corp",
            source=EntitySource.REPORT_BASE,
            all_domains=["twitter.com", "test.io"],
        )
        assert entity.canonical_domain == "test.io"

    def test_all_blocked_no_canonical(self):
        entity = CompanyEntity(
            name="Test Corp",
            source=EntitySource.REPORT_BASE,
            all_domains=["twitter.com", "linkedin.com", "gmail.com"],
        )
        assert entity.canonical_domain is None

    def test_linktr_ee_filtered(self):
        entity = CompanyEntity(
            name="Test Corp",
            source=EntitySource.REPORT_BASE,
            all_domains=["linktr.ee", "test.com"],
        )
        assert entity.canonical_domain == "test.com"


class TestDomainDedupeCanonicalScoring:
    """Canonical domain scoring/ranking tests."""

    def test_brand_match_wins(self):
        """securitize.io should be selected over jane.ma."""
        entity = CompanyEntity(
            name="SECURITIZE",
            source=EntitySource.REPORT_BASE,
            all_domains=["jane.ma", "securitize.io"],
        )
        assert entity.canonical_domain == "securitize.io"
        assert entity.domain_brand_score == 1.0

    def test_partial_match_over_random(self):
        entity = CompanyEntity(
            name="GOGOPOOL",
            source=EntitySource.REPORT_BASE,
            all_domains=["random.xyz", "gogopool-app.com", "gogopool.com"],
        )
        assert entity.canonical_domain == "gogopool.com"

    def test_no_match_picks_first_non_blocked(self):
        entity = CompanyEntity(
            name="ACME",
            source=EntitySource.REPORT_BASE,
            all_domains=["twitter.com", "example.org"],
        )
        assert entity.canonical_domain == "example.org"


class TestDomainDedupeSplitBehavior:
    """Domain-split behavior for same-name conflicts."""

    def test_securitize_regression(self):
        """SECURITIZE canonical: securitize.io (not jane.ma)."""
        entities = [
            CompanyEntity(
                name="SECURITIZE",
                source=EntitySource.REPORT_BASE,
                canonical_domain="securitize.io",
                all_domains=["securitize.io"],
            ),
            CompanyEntity(
                name="Securitize",
                source=EntitySource.COMPANIES_YAML,
                canonical_domain="securitize.io",
                all_domains=["securitize.io", "jane.ma"],
            ),
        ]
        deduped, audit = dedupe_by_domain(entities)

        assert len(deduped) == 1
        assert deduped[0].canonical_domain == "securitize.io"
        assert audit[0].decision == "merged"

    def test_real_re_al_split(self):
        """REAL and RE.AL with different domains -> split into separate entities."""
        entities = [
            CompanyEntity(
                name="REAL",
                source=EntitySource.REPORT_BASE,
                canonical_domain="real.com",
                all_domains=["real.com"],
            ),
            CompanyEntity(
                name="RE.AL",
                source=EntitySource.REPORT_BASE,
                canonical_domain="re.al",
                all_domains=["re.al"],
            ),
        ]
        deduped, audit = dedupe_by_domain(entities)

        # Both normalize to "real" but have different domains -> split
        assert len(deduped) == 2
        domains = {e.canonical_domain for e in deduped}
        assert "real.com" in domains
        assert "re.al" in domains
        # Find the split audit entry
        split_audits = [a for a in audit if a.decision == "split"]
        assert len(split_audits) == 1

    def test_gogopool_split(self):
        """GOGOPOOL with gogopool.com and avantprotocol.com -> split."""
        entities = [
            CompanyEntity(
                name="GoGoPool",
                source=EntitySource.REPORT_BASE,
                canonical_domain="gogopool.com",
                all_domains=["gogopool.com"],
            ),
            CompanyEntity(
                name="GOGOPOOL",
                source=EntitySource.COMPANIES_YAML,
                canonical_domain="avantprotocol.com",
                all_domains=["avantprotocol.com"],
            ),
        ]
        deduped, audit = dedupe_by_domain(entities)

        assert len(deduped) == 2
        domains = {e.canonical_domain for e in deduped}
        assert "gogopool.com" in domains
        assert "avantprotocol.com" in domains

    def test_same_domain_merges(self):
        """Two entities with same normalized name and same domain -> merge."""
        entities = [
            CompanyEntity(
                name="OKX",
                source=EntitySource.REPORT_BASE,
                canonical_domain="okx.com",
                all_domains=["okx.com"],
                round_amount=200_000_000,
            ),
            CompanyEntity(
                name="OKX",
                source=EntitySource.COMPANIES_YAML,
                canonical_domain="okx.com",
                all_domains=["okx.com"],
            ),
        ]
        deduped, audit = dedupe_by_domain(entities)

        assert len(deduped) == 1
        assert deduped[0].canonical_domain == "okx.com"
        assert deduped[0].round_amount == 200_000_000  # Preserved from merge

    def test_no_domain_entities_merge(self):
        """Entities with no domain and same name -> merge."""
        entities = [
            CompanyEntity(
                name="Mystery Corp",
                source=EntitySource.REPORT_BASE,
            ),
            CompanyEntity(
                name="Mystery Corp",
                source=EntitySource.CLIENT_BASE,
            ),
        ]
        deduped, audit = dedupe_by_domain(entities)

        assert len(deduped) == 1
        assert audit[0].decision == "merged"

    def test_single_entity_passthrough(self):
        entity = CompanyEntity(
            name="Unique Corp",
            source=EntitySource.REPORT_BASE,
            canonical_domain="unique.io",
        )
        deduped, audit = dedupe_by_domain([entity])

        assert len(deduped) == 1
        assert audit[0].decision == "single"

    def test_merge_preserves_all_domains(self):
        """Merging entities combines their domain lists."""
        entities = [
            CompanyEntity(
                name="Test",
                source=EntitySource.REPORT_BASE,
                canonical_domain="test.io",
                all_domains=["test.io"],
            ),
            CompanyEntity(
                name="Test",
                source=EntitySource.CLIENT_BASE,
                canonical_domain="test.io",
                all_domains=["test.io", "test.com"],
            ),
        ]
        deduped, _ = dedupe_by_domain(entities)

        assert len(deduped) == 1
        assert "test.io" in deduped[0].all_domains
        assert "test.com" in deduped[0].all_domains

    def test_merge_priority_order(self):
        """Client Base entities take priority over Report Base."""
        entities = [
            CompanyEntity(
                name="Priority Test",
                source=EntitySource.COMPANIES_YAML,
                canonical_domain="test.io",
                all_domains=["test.io"],
                tags=["yaml-tag"],
            ),
            CompanyEntity(
                name="Priority Test",
                source=EntitySource.CLIENT_BASE,
                canonical_domain="test.io",
                all_domains=["test.io"],
                tags=["client-tag"],
            ),
        ]
        deduped, _ = dedupe_by_domain(entities)

        assert len(deduped) == 1
        # Client Base has higher priority
        assert deduped[0].source == EntitySource.CLIENT_BASE
