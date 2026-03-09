#!/usr/bin/env python3
"""
Tests for HirePulse QA — pre-write contract validation.

Key tests:
- Stage contracts catch error-as-data (F-04/F-05 prevention)
- Freshness checks
- Domain dedup quality checks
"""

import json
import os
import sys
import tempfile
import pytest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hirepulse.hirepulse_qa import (
    INGEST_CONTRACT,
    HIRING_INTENT_CONTRACT,
    validate_ingest_output,
    validate_hiring_intent_output,
    validate_domain_dedup,
)
from hirepulse.models import QASeverity


class TestIngestContract:
    def test_valid_output_passes(self):
        output = {
            "total_companies": 5,
            "generated_at": "2026-03-09T00:00:00Z",
            "run_id": "abc123",
        }
        violations = INGEST_CONTRACT.validate(output)
        assert violations == []

    def test_zero_companies_fails(self):
        output = {
            "total_companies": 0,
            "generated_at": "2026-03-09T00:00:00Z",
            "run_id": "abc123",
        }
        violations = INGEST_CONTRACT.validate(output)
        assert any("< min" in v for v in violations)

    def test_missing_run_id_fails(self):
        output = {
            "total_companies": 5,
            "generated_at": "2026-03-09T00:00:00Z",
        }
        violations = INGEST_CONTRACT.validate(output)
        assert any("run_id" in v for v in violations)


class TestHiringIntentContract:
    def test_catches_error_in_reports(self):
        """Ensures transport errors in reports field are caught by contract."""
        output = {
            "total_companies": 1,
            "generated_at": "2026-03-09T00:00:00Z",
            "reports": "HTTPSConnectionPool error in signal data",
        }
        violations = HIRING_INTENT_CONTRACT.validate(output)
        assert any("forbidden pattern" in v for v in violations)

    def test_catches_grok_error(self):
        output = {
            "total_companies": 1,
            "generated_at": "2026-03-09T00:00:00Z",
            "reports": "[Grok error: timeout] some data",
        }
        violations = HIRING_INTENT_CONTRACT.validate(output)
        assert any("forbidden pattern" in v for v in violations)


class TestValidateIngestOutput:
    def test_file_not_found(self):
        findings = validate_ingest_output("/nonexistent/path.json")
        assert len(findings) == 1
        assert findings[0].severity == QASeverity.HIGH

    def test_empty_companies(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "run_id": "test",
                "total_companies": 0,
                "companies": [],
            }, f)
            path = f.name

        try:
            findings = validate_ingest_output(path)
            high_findings = [f for f in findings if f.severity == QASeverity.HIGH]
            assert len(high_findings) > 0
        finally:
            os.unlink(path)

    def test_stale_data_warns(self):
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "generated_at": old_time,
                "run_id": "test",
                "total_companies": 5,
                "companies": [
                    {"name": f"Co{i}", "normalized_name": f"co{i}", "canonical_domain": f"co{i}.com"}
                    for i in range(5)
                ],
            }, f)
            path = f.name

        try:
            findings = validate_ingest_output(path)
            freshness = [f for f in findings if f.category == "freshness"]
            assert len(freshness) > 0
        finally:
            os.unlink(path)


class TestValidateHiringIntentOutput:
    def test_error_as_data_caught(self):
        """F-04/F-05: Transport errors in signal text must be flagged."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_companies": 1,
                "intent_breakdown": {"high": 0, "medium": 0, "low": 0, "none": 1},
                "errors": [],
                "reports": [{
                    "company_name": "Test",
                    "intent_level": "none",
                    "signals": [{
                        "company_name": "Test",
                        "signal_type": "job_posting",
                        "signal_text": "HTTPSConnectionPool(host='html.duckduckgo.com') Max retries exceeded",
                        "confidence": 0.7,
                    }],
                }],
            }, f)
            path = f.name

        try:
            findings = validate_hiring_intent_output(path)
            error_as_data = [f for f in findings if f.category == "error_as_data"]
            assert len(error_as_data) > 0
            assert error_as_data[0].severity == QASeverity.HIGH
        finally:
            os.unlink(path)

    def test_high_none_rate_warns(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_companies": 10,
                "intent_breakdown": {"high": 0, "medium": 0, "low": 0, "none": 10},
                "errors": [],
                "reports": [],
            }, f)
            path = f.name

        try:
            findings = validate_hiring_intent_output(path)
            quality = [f for f in findings if f.category == "intent_quality"]
            assert len(quality) > 0
        finally:
            os.unlink(path)
