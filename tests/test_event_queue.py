"""Tests for event_queue.py."""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from event_queue import EventQueue


def test_push_and_pop():
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue("test", tmp)
        q.push({"company": "OKX", "amount": 200000000})
        event = q.pop()
        assert event is not None
        assert event["payload"]["company"] == "OKX"


def test_ack_removes_event():
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue("test", tmp)
        q.push({"company": "TEST"})
        event = q.pop()
        q.ack(event)
        # No more events
        assert q.pop() is None


def test_nack_returns_to_pending():
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue("test", tmp)
        q.push({"company": "RETRY"})
        event = q.pop()
        q.nack(event, error="temp failure")
        # Should be available again
        event2 = q.pop()
        assert event2 is not None


def test_queue_empty():
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue("test", tmp)
        assert q.pop() is None


def test_status():
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue("test", tmp)
        q.push({"a": 1})
        q.push({"b": 2})
        s = q.status()
        assert s["pending"] == 2


def test_list_pending():
    with tempfile.TemporaryDirectory() as tmp:
        q = EventQueue("test", tmp)
        q.push({"a": 1})
        q.push({"b": 2})
        pending = q.list_pending()
        assert len(pending) == 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
