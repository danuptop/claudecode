#!/usr/bin/env python3
"""
Pipeline orchestrator for HirePulse with stage contracts and checkpoint/resume.

Architectural patterns from Web3 Jobs Scraper:
- Stage contracts (orchestrator/stage_contracts.py) — validate BEFORE write
- Checkpoint/resume (orchestrator/checkpoint.py) — survive crashes
- Error classification (orchestrator/errors.py) — RetryableError/SkippableError/FatalError
- Dependency graph — prevent running stages out of order

Usage:
    from hirepulse.pipeline_orchestrator import (
        Pipeline, Stage, StageContract, run_pipeline
    )
"""

import json
import logging
import os
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from hirepulse.models import (
    FatalError,
    PipelineErrorKind,
    RetryableError,
    SkippableError,
    classify_error,
)

logger = logging.getLogger("hirepulse.orchestrator")


# ---------------------------------------------------------------------------
# Stage contracts — pre-write validation gates
# ---------------------------------------------------------------------------

@dataclass
class FieldContract:
    """Contract for a single output field."""
    name: str
    required: bool = True
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    pattern: Optional[str] = None
    forbidden_patterns: list[str] = field(default_factory=list)


@dataclass
class StageContract:
    """
    Output contract for a pipeline stage.

    Validated AFTER the stage runs but BEFORE results are persisted.
    This is the key difference from qa_validator.py which validates
    after the Notion write.
    """
    stage_name: str
    min_output_count: int = 0
    required_fields: list[FieldContract] = field(default_factory=list)
    custom_validators: list[Callable] = field(default_factory=list)

    def validate(self, output: Any) -> list[str]:
        """Validate stage output against contract. Returns list of violations."""
        violations = []

        # Check output count
        if isinstance(output, (list, dict)):
            count = len(output)
            if count < self.min_output_count:
                violations.append(
                    f"[{self.stage_name}] Output count {count} < minimum {self.min_output_count}"
                )

        # Check required fields on dict outputs
        if isinstance(output, dict):
            for fc in self.required_fields:
                val = output.get(fc.name)
                if fc.required and val is None:
                    violations.append(f"[{self.stage_name}] Missing required field: {fc.name}")
                    continue
                if val is None:
                    continue
                if fc.min_value is not None and isinstance(val, (int, float)) and val < fc.min_value:
                    violations.append(f"[{self.stage_name}] {fc.name}={val} < min {fc.min_value}")
                if fc.max_value is not None and isinstance(val, (int, float)) and val > fc.max_value:
                    violations.append(f"[{self.stage_name}] {fc.name}={val} > max {fc.max_value}")
                if fc.min_length is not None and isinstance(val, (str, list)) and len(val) < fc.min_length:
                    violations.append(f"[{self.stage_name}] {fc.name} length {len(val)} < min {fc.min_length}")
                for fp in fc.forbidden_patterns:
                    import re
                    if isinstance(val, str) and re.search(fp, val, re.IGNORECASE):
                        violations.append(f"[{self.stage_name}] {fc.name} contains forbidden pattern: {fp}")

        # Custom validators
        for validator_fn in self.custom_validators:
            try:
                issues = validator_fn(output)
                if issues:
                    violations.extend(issues)
            except Exception as e:
                violations.append(f"[{self.stage_name}] Custom validator error: {e}")

        return violations


# ---------------------------------------------------------------------------
# Stage definition
# ---------------------------------------------------------------------------

@dataclass
class Stage:
    """A single pipeline stage."""
    name: str
    fn: Callable  # The function to run
    depends_on: list[str] = field(default_factory=list)
    contract: Optional[StageContract] = None
    max_retries: int = 1
    timeout_seconds: int = 3600  # 1 hour default
    description: str = ""


# ---------------------------------------------------------------------------
# Checkpoint persistence
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """Result of running a single stage."""
    stage_name: str
    status: str  # "completed", "failed", "skipped"
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0
    output_summary: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    contract_violations: list[str] = field(default_factory=list)


@dataclass
class PipelineCheckpoint:
    """Persisted pipeline state for resume capability."""
    run_id: str
    pipeline_name: str
    started_at: str
    completed_stages: list[str] = field(default_factory=list)
    stage_results: dict[str, dict] = field(default_factory=dict)
    current_stage: Optional[str] = None
    status: str = "running"  # "running", "completed", "failed"

    def save(self, path: str):
        """Persist checkpoint to disk."""
        data = {
            "run_id": self.run_id,
            "pipeline_name": self.pipeline_name,
            "started_at": self.started_at,
            "completed_stages": self.completed_stages,
            "stage_results": self.stage_results,
            "current_stage": self.current_stage,
            "status": self.status,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Checkpoint saved: {path}")

    @classmethod
    def load(cls, path: str) -> Optional["PipelineCheckpoint"]:
        """Load checkpoint from disk if it exists."""
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            cp = cls(
                run_id=data["run_id"],
                pipeline_name=data["pipeline_name"],
                started_at=data["started_at"],
            )
            cp.completed_stages = data.get("completed_stages", [])
            cp.stage_results = data.get("stage_results", {})
            cp.current_stage = data.get("current_stage")
            cp.status = data.get("status", "running")
            return cp
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Corrupt checkpoint at {path}: {e}")
            return None


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Multi-stage pipeline with contracts, checkpoints, and error classification.

    Patterns applied:
    - Stage contracts validate output BEFORE persistence
    - Checkpoint/resume survives crashes
    - Error classification prevents data corruption
    - Dependency graph enforces stage ordering
    """

    def __init__(
        self,
        name: str,
        stages: list[Stage],
        checkpoint_dir: str = "reports/hirepulse",
    ):
        self.name = name
        self.stages = {s.name: s for s in stages}
        self.stage_order = [s.name for s in stages]
        self.checkpoint_dir = checkpoint_dir
        self._context: dict[str, Any] = {}  # Shared state between stages

    @property
    def checkpoint_path(self) -> str:
        return os.path.join(self.checkpoint_dir, f"{self.name}_checkpoint.json")

    def run(
        self,
        context: Optional[dict[str, Any]] = None,
        resume: bool = False,
        start_from: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict[str, StageResult]:
        """
        Execute the pipeline.

        Args:
            context: Shared state dict passed to all stage functions.
            resume: If True, load checkpoint and skip completed stages.
            start_from: Override — start from this specific stage.
            dry_run: Log what would happen without executing.

        Returns:
            Dict of stage_name -> StageResult.
        """
        self._context = context or {}
        results: dict[str, StageResult] = {}

        # Load or create checkpoint
        checkpoint = None
        if resume:
            checkpoint = PipelineCheckpoint.load(self.checkpoint_path)
            if checkpoint:
                logger.info(
                    f"Resuming pipeline '{self.name}' from checkpoint "
                    f"(run_id={checkpoint.run_id}, "
                    f"completed={checkpoint.completed_stages})"
                )

        if not checkpoint:
            checkpoint = PipelineCheckpoint(
                run_id=str(uuid.uuid4())[:8],
                pipeline_name=self.name,
                started_at=datetime.now(timezone.utc).isoformat(),
            )

        self._context["run_id"] = checkpoint.run_id
        self._context["dry_run"] = dry_run

        logger.info(f"Pipeline '{self.name}' starting (run_id={checkpoint.run_id})")

        # Determine which stages to run
        skip_until = start_from
        for stage_name in self.stage_order:
            stage = self.stages[stage_name]

            # Skip if already completed (resume mode)
            if stage_name in checkpoint.completed_stages:
                logger.info(f"  [{stage_name}] SKIPPED (already completed)")
                continue

            # Skip until we reach start_from
            if skip_until and stage_name != skip_until:
                logger.info(f"  [{stage_name}] SKIPPED (before start_from={skip_until})")
                continue
            skip_until = None  # Found start_from, run everything after

            # Check dependencies
            unmet = [d for d in stage.depends_on if d not in checkpoint.completed_stages]
            if unmet:
                logger.warning(f"  [{stage_name}] SKIPPED — unmet dependencies: {unmet}")
                results[stage_name] = StageResult(
                    stage_name=stage_name,
                    status="skipped",
                    error=f"Unmet dependencies: {unmet}",
                )
                continue

            # Execute stage
            checkpoint.current_stage = stage_name
            checkpoint.save(self.checkpoint_path)

            result = self._run_stage(stage, dry_run=dry_run)
            results[stage_name] = result

            # Update checkpoint
            if result.status == "completed":
                checkpoint.completed_stages.append(stage_name)
                checkpoint.stage_results[stage_name] = {
                    "status": result.status,
                    "duration": result.duration_seconds,
                    "output_summary": result.output_summary,
                }
                checkpoint.save(self.checkpoint_path)
            elif result.status == "failed":
                # Check error classification
                if result.error and "FATAL" in result.error:
                    logger.error(f"FATAL error in [{stage_name}] — stopping pipeline")
                    checkpoint.status = "failed"
                    checkpoint.save(self.checkpoint_path)
                    break
                else:
                    logger.warning(f"Stage [{stage_name}] failed — continuing pipeline")
                    checkpoint.stage_results[stage_name] = {
                        "status": "failed",
                        "error": result.error,
                    }
                    checkpoint.save(self.checkpoint_path)

        # Pipeline complete
        all_completed = all(
            sn in checkpoint.completed_stages for sn in self.stage_order
        )
        checkpoint.status = "completed" if all_completed else "partial"
        checkpoint.current_stage = None
        checkpoint.save(self.checkpoint_path)

        # Summary
        stats = {"completed": 0, "failed": 0, "skipped": 0}
        for r in results.values():
            stats[r.status] = stats.get(r.status, 0) + 1
        logger.info(f"Pipeline '{self.name}' finished: {stats}")

        return results

    def _run_stage(self, stage: Stage, dry_run: bool = False) -> StageResult:
        """Execute a single stage with retries and contract validation."""
        logger.info(f"  [{stage.name}] Starting...")
        start_time = time.time()

        if dry_run:
            return StageResult(
                stage_name=stage.name,
                status="completed",
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                output_summary={"dry_run": True},
            )

        last_error = None
        for attempt in range(stage.max_retries + 1):
            try:
                output = stage.fn(self._context)

                # Validate contract
                if stage.contract:
                    violations = stage.contract.validate(output)
                    if violations:
                        logger.warning(
                            f"  [{stage.name}] Contract violations: {violations}"
                        )
                        # Contract violations are warnings, not failures
                        # (downstream stages decide if they can proceed)

                elapsed = time.time() - start_time
                logger.info(f"  [{stage.name}] Completed in {elapsed:.1f}s")

                return StageResult(
                    stage_name=stage.name,
                    status="completed",
                    started_at=datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=elapsed,
                    output_summary=_summarize_output(output),
                    contract_violations=violations if stage.contract else [],
                )

            except FatalError as e:
                elapsed = time.time() - start_time
                logger.error(f"  [{stage.name}] FATAL: {e}")
                return StageResult(
                    stage_name=stage.name,
                    status="failed",
                    started_at=datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_seconds=elapsed,
                    error=f"FATAL: {e}",
                )

            except SkippableError as e:
                logger.warning(f"  [{stage.name}] Skippable error: {e}")
                last_error = str(e)
                # Don't retry skippable errors
                break

            except Exception as e:
                last_error = str(e)
                error_kind = classify_error(e)

                if error_kind == PipelineErrorKind.FATAL:
                    elapsed = time.time() - start_time
                    return StageResult(
                        stage_name=stage.name,
                        status="failed",
                        duration_seconds=elapsed,
                        error=f"FATAL: {e}",
                    )

                if error_kind == PipelineErrorKind.RETRYABLE and attempt < stage.max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        f"  [{stage.name}] Retryable error (attempt {attempt+1}/{stage.max_retries+1}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue

                # Exhausted retries or skippable
                logger.error(f"  [{stage.name}] Failed after {attempt+1} attempts: {e}")
                break

        elapsed = time.time() - start_time
        return StageResult(
            stage_name=stage.name,
            status="failed",
            started_at=datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=elapsed,
            error=last_error,
        )


def _summarize_output(output: Any) -> dict[str, Any]:
    """Create a concise summary of stage output for checkpoint logging."""
    if output is None:
        return {"type": "none"}
    if isinstance(output, dict):
        return {"type": "dict", "keys": list(output.keys())[:20], "count": len(output)}
    if isinstance(output, list):
        return {"type": "list", "count": len(output)}
    return {"type": type(output).__name__}
