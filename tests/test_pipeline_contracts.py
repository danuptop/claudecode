#!/usr/bin/env python3
"""
Tests for pipeline orchestrator and stage contracts.

Covers:
- Stage contract validation (pre-write gates)
- Pipeline execution with dependency ordering
- Checkpoint save/load for resume capability
- Error classification driving stage behavior
"""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from hirepulse.models import FatalError, RetryableError, SkippableError
from hirepulse.pipeline_orchestrator import (
    FieldContract,
    Pipeline,
    PipelineCheckpoint,
    Stage,
    StageContract,
    StageResult,
)


# ---------------------------------------------------------------------------
# Stage contract tests
# ---------------------------------------------------------------------------

class TestStageContract:
    def test_passes_valid_output(self):
        contract = StageContract(
            stage_name="test",
            min_output_count=1,
            required_fields=[
                FieldContract(name="count", required=True, min_value=1),
                FieldContract(name="name", required=True, min_length=2),
            ],
        )
        violations = contract.validate({"count": 5, "name": "hello"})
        assert violations == []

    def test_fails_missing_required_field(self):
        contract = StageContract(
            stage_name="test",
            required_fields=[
                FieldContract(name="count", required=True),
            ],
        )
        violations = contract.validate({"other": "value"})
        assert len(violations) == 1
        assert "Missing required field" in violations[0]

    def test_fails_min_value(self):
        contract = StageContract(
            stage_name="test",
            required_fields=[
                FieldContract(name="total", required=True, min_value=10),
            ],
        )
        violations = contract.validate({"total": 5})
        assert len(violations) == 1
        assert "< min" in violations[0]

    def test_fails_max_value(self):
        contract = StageContract(
            stage_name="test",
            required_fields=[
                FieldContract(name="total", required=True, max_value=100),
            ],
        )
        violations = contract.validate({"total": 150})
        assert len(violations) == 1
        assert "> max" in violations[0]

    def test_fails_min_length(self):
        contract = StageContract(
            stage_name="test",
            required_fields=[
                FieldContract(name="name", required=True, min_length=5),
            ],
        )
        violations = contract.validate({"name": "hi"})
        assert len(violations) == 1
        assert "length" in violations[0]

    def test_fails_forbidden_pattern(self):
        contract = StageContract(
            stage_name="test",
            required_fields=[
                FieldContract(
                    name="content",
                    required=True,
                    forbidden_patterns=[r"HTTPSConnectionPool"],
                ),
            ],
        )
        violations = contract.validate({
            "content": "Error: HTTPSConnectionPool(host='api.x.ai') timeout"
        })
        assert len(violations) == 1
        assert "forbidden pattern" in violations[0]

    def test_min_output_count_for_list(self):
        contract = StageContract(stage_name="test", min_output_count=3)
        violations = contract.validate([1, 2])
        assert len(violations) == 1
        assert "count 2 < minimum 3" in violations[0]

    def test_optional_field_skipped_when_none(self):
        contract = StageContract(
            stage_name="test",
            required_fields=[
                FieldContract(name="optional_val", required=False, min_value=10),
            ],
        )
        violations = contract.validate({"optional_val": None})
        assert violations == []

    def test_custom_validator(self):
        def check_no_dupes(output):
            if isinstance(output, dict):
                names = output.get("names", [])
                if len(names) != len(set(names)):
                    return ["Duplicate names found"]
            return []

        contract = StageContract(
            stage_name="test",
            custom_validators=[check_no_dupes],
        )
        violations = contract.validate({"names": ["a", "b", "a"]})
        assert len(violations) == 1
        assert "Duplicate names" in violations[0]


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------

class TestPipelineCheckpoint:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "checkpoint.json")
            cp = PipelineCheckpoint(
                run_id="test-123",
                pipeline_name="test_pipeline",
                started_at="2026-03-09T00:00:00Z",
            )
            cp.completed_stages = ["stage_a", "stage_b"]
            cp.save(path)

            loaded = PipelineCheckpoint.load(path)
            assert loaded is not None
            assert loaded.run_id == "test-123"
            assert loaded.completed_stages == ["stage_a", "stage_b"]

    def test_load_nonexistent(self):
        loaded = PipelineCheckpoint.load("/nonexistent/path.json")
        assert loaded is None

    def test_load_corrupt_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            path = f.name
        try:
            loaded = PipelineCheckpoint.load(path)
            assert loaded is None
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Pipeline execution tests
# ---------------------------------------------------------------------------

class TestPipelineExecution:
    def test_runs_stages_in_order(self):
        call_order = []

        def stage_a(ctx):
            call_order.append("a")
            return {"status": "ok"}

        def stage_b(ctx):
            call_order.append("b")
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[
                    Stage(name="a", fn=stage_a),
                    Stage(name="b", fn=stage_b, depends_on=["a"]),
                ],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run()

        assert call_order == ["a", "b"]
        assert results["a"].status == "completed"
        assert results["b"].status == "completed"

    def test_fatal_stops_before_dependents(self):
        """FATAL error in stage A stops pipeline — stage B never runs."""
        def stage_a(ctx):
            raise FatalError("boom")

        def stage_b(ctx):
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[
                    Stage(name="a", fn=stage_a),
                    Stage(name="b", fn=stage_b, depends_on=["a"]),
                ],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run()

        assert results["a"].status == "failed"
        assert "FATAL" in results["a"].error
        # Stage b never reached because fatal stopped the pipeline
        assert "b" not in results

    def test_skips_unmet_dependencies_non_fatal(self):
        """Non-fatal error in stage A -> stage B skipped due to unmet dependency."""
        attempt = [0]

        def stage_a(ctx):
            attempt[0] += 1
            raise Exception("404 Not Found")  # Classified as skippable

        def stage_b(ctx):
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[
                    Stage(name="a", fn=stage_a, max_retries=0),
                    Stage(name="b", fn=stage_b, depends_on=["a"]),
                ],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run()

        assert results["a"].status == "failed"
        assert "b" in results
        assert results["b"].status == "skipped"

    def test_retries_retryable_errors(self):
        attempt_count = [0]

        def flaky_stage(ctx):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                raise ConnectionError("timeout")
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[
                    Stage(name="flaky", fn=flaky_stage, max_retries=3),
                ],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run()

        assert results["flaky"].status == "completed"
        assert attempt_count[0] == 3

    def test_fatal_error_stops_pipeline(self):
        call_order = []

        def stage_a(ctx):
            call_order.append("a")
            raise FatalError("auth failed")

        def stage_b(ctx):
            call_order.append("b")
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[
                    Stage(name="a", fn=stage_a),
                    Stage(name="b", fn=stage_b),
                ],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run()

        assert call_order == ["a"]
        assert results["a"].status == "failed"
        assert "FATAL" in results["a"].error

    def test_dry_run(self):
        call_count = [0]

        def stage_a(ctx):
            call_count[0] += 1
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[Stage(name="a", fn=stage_a)],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run(dry_run=True)

        assert call_count[0] == 0  # Stage function not called
        assert results["a"].status == "completed"

    def test_resume_skips_completed(self):
        call_order = []

        def stage_a(ctx):
            call_order.append("a")
            return {}

        def stage_b(ctx):
            call_order.append("b")
            return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            # First run — complete stage a
            pipeline = Pipeline(
                name="test",
                stages=[
                    Stage(name="a", fn=stage_a),
                    Stage(name="b", fn=stage_b, depends_on=["a"]),
                ],
                checkpoint_dir=tmpdir,
            )
            pipeline.run()
            assert call_order == ["a", "b"]

            # Resume — stage a should be skipped
            call_order.clear()
            pipeline2 = Pipeline(
                name="test",
                stages=[
                    Stage(name="a", fn=stage_a),
                    Stage(name="b", fn=stage_b, depends_on=["a"]),
                ],
                checkpoint_dir=tmpdir,
            )
            pipeline2.run(resume=True)
            assert call_order == []  # Both already completed

    def test_contract_violations_dont_fail_stage(self):
        """Contract violations are warnings, not failures."""
        def stage_a(ctx):
            return {"count": 0}

        contract = StageContract(
            stage_name="a",
            required_fields=[FieldContract(name="count", min_value=10)],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = Pipeline(
                name="test",
                stages=[Stage(name="a", fn=stage_a, contract=contract)],
                checkpoint_dir=tmpdir,
            )
            results = pipeline.run()

        assert results["a"].status == "completed"
        assert len(results["a"].contract_violations) > 0
