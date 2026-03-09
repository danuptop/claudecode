"""Tests for qa_dashboard.py."""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from qa_dashboard import QADashboard


def test_record_and_summary():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "qa.db")
        dash = QADashboard(db_path)
        dash.record("page-1", "PASS", title="Test Page 1", run_id="run-001")
        dash.record("page-2", "FAIL", title="Test Page 2", issues="Error text detected", run_id="run-001")
        s = dash.summary()
        assert isinstance(s, dict)


def test_failures():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "qa.db")
        dash = QADashboard(db_path)
        dash.record("page-1", "FAIL", title="Bad Page", issues="Missing REPORT KEY")
        fails = dash.failures(hours=1)
        assert len(fails) >= 1


def test_record_pass():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "qa.db")
        dash = QADashboard(db_path)
        dash.record("page-1", "PASS")
        # No exception means success


def test_summary_empty():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "qa.db")
        dash = QADashboard(db_path)
        s = dash.summary()
        assert isinstance(s, dict)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
