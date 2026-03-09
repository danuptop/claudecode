#!/usr/bin/env python3
"""
Pipeline Lock — single-instance execution guard.

Prevents concurrent pipeline runs that cause race conditions in dedup.
Uses flock for atomic, OS-level locking.

Usage:
    from pipeline_lock import pipeline_lock

    with pipeline_lock("funding-intel-brief"):
        # Only one instance of this block runs at a time
        run_pipeline()

    # Or as a decorator:
    @single_instance("funding-intel-brief")
    def run_pipeline():
        ...

CLI:
    # Check if a pipeline is currently running
    python3 pipeline_lock.py --status funding-intel-brief

    # Force-release a stale lock (use with caution)
    python3 pipeline_lock.py --release funding-intel-brief

Deployment:
    Place at: /home/ubuntu/clawd/scripts/pipeline_lock.py
"""

import argparse
import fcntl
import functools
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger("pipeline_lock")

LOCK_DIR = os.getenv("PIPELINE_LOCK_DIR", "/tmp/clawd-locks")


def _lock_path(pipeline_name: str) -> str:
    os.makedirs(LOCK_DIR, exist_ok=True)
    safe_name = pipeline_name.replace("/", "_").replace(" ", "_")
    return os.path.join(LOCK_DIR, f"{safe_name}.lock")


@contextmanager
def pipeline_lock(
    pipeline_name: str,
    timeout: float = 0,
    poll_interval: float = 1.0,
):
    """
    Context manager that acquires an exclusive lock for a pipeline.

    Args:
        pipeline_name: Unique name for the pipeline (e.g., "funding-intel-brief").
        timeout: Max seconds to wait for lock. 0 = fail immediately if locked.
        poll_interval: Seconds between lock attempts when waiting.

    Raises:
        PipelineLockError: If the lock cannot be acquired.

    Usage:
        with pipeline_lock("funding-intel-brief"):
            run_pipeline()
    """
    lock_file = _lock_path(pipeline_name)
    fd = open(lock_file, "w")

    start = time.monotonic()
    acquired = False

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except (IOError, OSError):
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                break
            time.sleep(min(poll_interval, timeout - elapsed))

    if not acquired:
        fd.close()
        raise PipelineLockError(
            f"Pipeline '{pipeline_name}' is already running. "
            f"Lock file: {lock_file}"
        )

    # Write PID for debugging
    fd.seek(0)
    fd.truncate()
    fd.write(f"{os.getpid()}\n{pipeline_name}\n{time.time()}\n")
    fd.flush()

    logger.info(f"Acquired lock for '{pipeline_name}' (pid={os.getpid()})")

    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        try:
            os.remove(lock_file)
        except OSError:
            pass
        logger.info(f"Released lock for '{pipeline_name}'")


class PipelineLockError(Exception):
    pass


def single_instance(pipeline_name: str):
    """Decorator that ensures only one instance of a function runs at a time."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with pipeline_lock(pipeline_name):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


def check_lock_status(pipeline_name: str) -> Optional[dict]:
    """Check if a pipeline lock is currently held. Returns lock info or None."""
    lock_file = _lock_path(pipeline_name)
    if not os.path.exists(lock_file):
        return None

    try:
        fd = open(lock_file, "r+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock acquired — means no one is holding it
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
            return None
        except (IOError, OSError):
            # Lock is held
            fd.seek(0)
            lines = fd.read().strip().split("\n")
            fd.close()
            if len(lines) >= 3:
                return {
                    "pid": int(lines[0]),
                    "pipeline": lines[1],
                    "since": float(lines[2]),
                    "elapsed": time.time() - float(lines[2]),
                }
            return {"pid": -1, "pipeline": pipeline_name, "since": 0, "elapsed": 0}
    except Exception:
        return None


def release_lock(pipeline_name: str) -> bool:
    """Force-release a lock file. Use only for stale locks."""
    lock_file = _lock_path(pipeline_name)
    try:
        os.remove(lock_file)
        logger.warning(f"Force-released lock for '{pipeline_name}'")
        return True
    except FileNotFoundError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Pipeline Lock Manager")
    parser.add_argument("pipeline", help="Pipeline name")
    parser.add_argument("--status", action="store_true", help="Check lock status")
    parser.add_argument("--release", action="store_true", help="Force-release lock")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.status:
        info = check_lock_status(args.pipeline)
        if info:
            print(f"LOCKED — pid={info['pid']}, running for {info['elapsed']:.0f}s")
        else:
            print("UNLOCKED")
    elif args.release:
        if release_lock(args.pipeline):
            print("Released.")
        else:
            print("No lock file found.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
