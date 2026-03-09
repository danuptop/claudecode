"""Tests for pipeline_lock.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from pipeline_lock import pipeline_lock, PipelineLockError, check_lock_status, release_lock


def test_lock_acquire_and_release():
    with pipeline_lock("test-lock-unit", timeout=0):
        # Lock is held
        status = check_lock_status("test-lock-unit")
        assert status is not None


def test_lock_released_after_context():
    with pipeline_lock("test-lock-release", timeout=0):
        pass
    # After context, lock should be released
    # Acquiring again should succeed
    with pipeline_lock("test-lock-release", timeout=0):
        pass


def test_lock_reentrant_fails():
    with pipeline_lock("test-lock-reentrant", timeout=0):
        try:
            with pipeline_lock("test-lock-reentrant", timeout=0):
                assert False, "Should have raised PipelineLockError"
        except PipelineLockError:
            pass  # Expected


def test_release_lock():
    with pipeline_lock("test-lock-manual", timeout=0):
        released = release_lock("test-lock-manual")
        assert released is True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
