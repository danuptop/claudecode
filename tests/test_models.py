#!/usr/bin/env python3
"""
Tests for HirePulse Pydantic models.

Covers:
- Boundary validation (the key architectural pattern)
- Domain utilities (extract, block, brand score)
- Error classification
- Investor sanitization at model boundary
"""

import os
import sys
import pytest

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hirepulse.models import (
    CompanyEntity,
    DomainDedupeResult,
    EntitySource,
    FatalError,
    FundraisingEvent,
    HiringIntentReport,
    HiringSignal,
    IntentLevel,
    PipelineErrorKind,
    RetryableError,
    SkippableError,
    classify_error,
    domain_brand_score,
    extract_domain,
    is_blocked_domain,
    normalize_company_name,
)


# ---------------------------------------------------------------------------
# Domain utilities
# ---------------------------------------------------------------------------

class TestExtractDomain:
    def test_https_url(self):
        assert extract_domain("https://securitize.io/about") == "securitize.io"

    def test_http_url(self):
        assert extract_domain("http://gogopool.com") == "gogopool.com"

    def test_www_prefix(self):
        assert extract_domain("www.crossovermarkets.com") == "crossovermarkets.com"

    def test_bare_domain(self):
        assert extract_domain("re.al") == "re.al"

    def test_with_query_params(self):
        assert extract_domain("https://example.com?foo=bar") == "example.com"

    def test_empty_string(self):
        assert extract_domain("") is None

    def test_none(self):
        assert extract_domain(None) is None

    def test_no_dot(self):
        assert extract_domain("localhost") is None


class TestIsBlockedDomain:
    def test_twitter(self):
        assert is_blocked_domain("twitter.com") is True

    def test_x_dot_com(self):
        assert is_blocked_domain("x.com") is True

    def test_linkedin(self):
        assert is_blocked_domain("linkedin.com") is True

    def test_gmail(self):
        assert is_blocked_domain("gmail.com") is True

    def test_linktr_ee(self):
        assert is_blocked_domain("linktr.ee") is True

    def test_subdomain_of_blocked(self):
        assert is_blocked_domain("mobile.twitter.com") is True

    def test_legitimate_domain(self):
        assert is_blocked_domain("securitize.io") is False

    def test_empty(self):
        assert is_blocked_domain("") is True

    def test_none(self):
        assert is_blocked_domain(None) is True


class TestNormalizeCompanyName:
    def test_basic(self):
        assert normalize_company_name("Securitize") == "securitize"

    def test_with_inc(self):
        assert normalize_company_name("Securitize Inc.") == "securitize"

    def test_with_labs(self):
        assert normalize_company_name("Uniswap Labs") == "uniswap"

    def test_special_chars(self):
        assert normalize_company_name("RE.AL") == "real"

    def test_case_insensitive(self):
        assert normalize_company_name("GOGOPOOL") == "gogopool"

    def test_with_protocol(self):
        assert normalize_company_name("Compound Protocol") == "compound"

    def test_empty(self):
        assert normalize_company_name("") == ""


class TestDomainBrandScore:
    def test_exact_match(self):
        assert domain_brand_score("securitize.io", "SECURITIZE") == 1.0

    def test_no_match(self):
        assert domain_brand_score("jane.ma", "SECURITIZE") == 0.0

    def test_partial_match(self):
        score = domain_brand_score("securitize-app.com", "SECURITIZE")
        assert score == 0.7

    def test_empty_domain(self):
        assert domain_brand_score("", "SECURITIZE") == 0.0

    def test_empty_name(self):
        assert domain_brand_score("securitize.io", "") == 0.0


# ---------------------------------------------------------------------------
# CompanyEntity model
# ---------------------------------------------------------------------------

class TestCompanyEntity:
    def test_auto_normalized_name(self):
        entity = CompanyEntity(name="Securitize Inc.", source=EntitySource.REPORT_BASE)
        assert entity.normalized_name == "securitize"

    def test_auto_canonical_domain_selection(self):
        entity = CompanyEntity(
            name="Securitize",
            source=EntitySource.REPORT_BASE,
            all_domains=["jane.ma", "securitize.io", "twitter.com"],
        )
        assert entity.canonical_domain == "securitize.io"
        assert entity.domain_brand_score == 1.0

    def test_blocks_social_domains(self):
        entity = CompanyEntity(
            name="Example Corp",
            source=EntitySource.REPORT_BASE,
            all_domains=["twitter.com", "linkedin.com"],
        )
        assert entity.canonical_domain is None

    def test_preserves_explicit_canonical_domain(self):
        entity = CompanyEntity(
            name="Test",
            source=EntitySource.MANUAL,
            canonical_domain="test.io",
            all_domains=["test.io", "test.com"],
        )
        assert entity.canonical_domain == "test.io"

    def test_content_fingerprint(self):
        entity = CompanyEntity(
            name="Test Corp",
            source=EntitySource.REPORT_BASE,
            investors=["a16z", "Coinbase"],
        )
        fp = entity.content_fingerprint()
        assert len(fp) == 16
        assert isinstance(fp, str)

    def test_content_fingerprint_stable(self):
        """Same inputs produce same hash."""
        e1 = CompanyEntity(name="Test", source=EntitySource.REPORT_BASE, investors=["a16z"])
        e2 = CompanyEntity(name="Test", source=EntitySource.REPORT_BASE, investors=["a16z"])
        assert e1.content_fingerprint() == e2.content_fingerprint()

    def test_min_name_length(self):
        with pytest.raises(Exception):
            CompanyEntity(name="", source=EntitySource.REPORT_BASE)


# ---------------------------------------------------------------------------
# HiringSignal — error rejection at boundary
# ---------------------------------------------------------------------------

class TestHiringSignal:
    def test_valid_signal(self):
        signal = HiringSignal(
            company_name="Test",
            signal_type="job_posting",
            signal_text="Senior Rust Engineer (Remote)",
            confidence=0.9,
        )
        assert signal.signal_text == "Senior Rust Engineer (Remote)"

    def test_rejects_httpsconnectionpool_error(self):
        """F-04 prevention: DuckDuckGo transport error rejected at boundary."""
        with pytest.raises(Exception) as exc_info:
            HiringSignal(
                company_name="Test",
                signal_type="job_posting",
                signal_text="Search error: HTTPSConnectionPool(host='html.duckduckgo.com') Max retries exceeded",
                confidence=0.7,
            )
        assert "transport error" in str(exc_info.value).lower()

    def test_rejects_grok_error(self):
        """F-05 prevention: Grok timeout rejected at boundary."""
        with pytest.raises(Exception) as exc_info:
            HiringSignal(
                company_name="Test",
                signal_type="competitor_benchmark",
                signal_text="[Grok error: HTTPSConnectionPool timeout]",
                confidence=0.5,
            )
        assert "transport error" in str(exc_info.value).lower()

    def test_rejects_traceback(self):
        with pytest.raises(Exception):
            HiringSignal(
                company_name="Test",
                signal_type="web_mention",
                signal_text="Traceback (most recent call last):\n  File ...",
            )

    def test_rejects_connection_error(self):
        with pytest.raises(Exception):
            HiringSignal(
                company_name="Test",
                signal_type="web_mention",
                signal_text="ConnectionError(MaxRetryError('...'))",
            )

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            HiringSignal(
                company_name="Test",
                signal_type="job_posting",
                signal_text="Valid signal",
                confidence=1.5,
            )

    def test_confidence_negative(self):
        with pytest.raises(Exception):
            HiringSignal(
                company_name="Test",
                signal_type="job_posting",
                signal_text="Valid signal",
                confidence=-0.1,
            )


# ---------------------------------------------------------------------------
# FundraisingEvent — amount validation at boundary
# ---------------------------------------------------------------------------

class TestFundraisingEvent:
    def test_rejects_absurd_amount(self):
        """F-09 prevention: $25B valuation mistaken for round amount."""
        with pytest.raises(Exception) as exc_info:
            FundraisingEvent(
                company="OKX",
                round_amount=25_000_000_000_000,  # $25T — clearly wrong
                round_type="Unknown",
            )
        assert "exceeds" in str(exc_info.value).lower() or "500" in str(exc_info.value).lower()

    def test_valid_amount(self):
        event = FundraisingEvent(
            company="Crossover Markets",
            round_amount=31_000_000,
            round_type="Series B",
        )
        assert event.round_amount == 31_000_000

    def test_auto_report_key(self):
        event = FundraisingEvent(
            company="Crossover Markets",
            round_amount=31_000_000,
            round_type="Series B",
        )
        assert event.report_key.startswith("fundraising-intel:v3:")
        assert "crossover-markets" in event.report_key
        assert "31000000" in event.report_key

    def test_zero_amount_allowed(self):
        """$0 is allowed but should be handled by dedup logic."""
        event = FundraisingEvent(
            company="Unknown Startup",
            round_amount=0,
        )
        assert event.round_amount == 0


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def test_timeout_is_retryable(self):
        assert classify_error(TimeoutError("timed out")) == PipelineErrorKind.RETRYABLE

    def test_connection_error_is_retryable(self):
        assert classify_error(ConnectionError("refused")) == PipelineErrorKind.RETRYABLE

    def test_auth_error_is_fatal(self):
        assert classify_error(Exception("Invalid API key")) == PipelineErrorKind.FATAL

    def test_forbidden_is_fatal(self):
        assert classify_error(Exception("403 Forbidden")) == PipelineErrorKind.FATAL

    def test_not_found_is_skippable(self):
        assert classify_error(Exception("404 Not Found")) == PipelineErrorKind.SKIPPABLE

    def test_parse_error_is_skippable(self):
        assert classify_error(Exception("parse error in response")) == PipelineErrorKind.SKIPPABLE

    def test_unknown_is_retryable(self):
        """Unknown errors default to retryable (safer than skippable)."""
        assert classify_error(Exception("something weird")) == PipelineErrorKind.RETRYABLE

    def test_error_classes(self):
        r = RetryableError("timeout")
        assert r.kind == PipelineErrorKind.RETRYABLE

        s = SkippableError("not found")
        assert s.kind == PipelineErrorKind.SKIPPABLE

        f = FatalError("auth failed")
        assert f.kind == PipelineErrorKind.FATAL


# ---------------------------------------------------------------------------
# HiringIntentReport
# ---------------------------------------------------------------------------

class TestHiringIntentReport:
    def test_auto_fields(self):
        report = HiringIntentReport(
            company_name="Securitize Inc.",
            signals=[
                HiringSignal(company_name="Securitize", signal_type="job_posting", signal_text="Engineer"),
                HiringSignal(company_name="Securitize", signal_type="job_posting", signal_text="Designer"),
            ],
        )
        assert report.normalized_name == "securitize"
        assert report.signal_count == 2
        assert report.generated_at != ""
