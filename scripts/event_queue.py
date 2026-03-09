#!/usr/bin/env python3
"""
Event Queue — file-based queue for pipeline orchestration.

Replaces cron-based triggering with event-driven execution.
When funding-intel-brief creates a canonical page, it enqueues the page ID.
founder-intel-pipeline reads from the queue and enriches each page.

Usage:
    from event_queue import EventQueue

    # Producer (in funding-intel-brief.py):
    queue = EventQueue("enrichment-pending")
    queue.push({"page_id": "abc-123", "company": "OKX", "action": "enrich"})

    # Consumer (in founder-intel-pipeline.py):
    queue = EventQueue("enrichment-pending")
    while True:
        event = queue.pop()
        if event is None:
            break
        enrich_page(event["page_id"])
        queue.ack(event)

CLI:
    python3 event_queue.py --queue enrichment-pending --status
    python3 event_queue.py --queue enrichment-pending --list
    python3 event_queue.py --queue enrichment-pending --push '{"page_id":"abc"}'

Deployment:
    Place at: /home/ubuntu/clawd/scripts/event_queue.py
    Data dir: /home/ubuntu/clawd/data/queues/ (auto-created)
"""

import argparse
import fcntl
import json
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger("event_queue")

QUEUE_DIR = os.path.join(
    os.getenv("CLAWD_DATA_DIR", os.path.expanduser("/home/ubuntu/clawd/data")),
    "queues",
)


class EventQueue:
    """
    File-based persistent event queue with at-least-once delivery.

    Events are stored as JSON files in a directory structure:
        queues/{name}/pending/   - events waiting to be processed
        queues/{name}/active/    - events currently being processed
        queues/{name}/done/      - completed events (pruned periodically)
        queues/{name}/failed/    - events that failed processing
    """

    def __init__(self, name: str, base_dir: str = QUEUE_DIR):
        self.name = name
        self.base_dir = os.path.join(base_dir, name)
        self._dirs = {
            "pending": os.path.join(self.base_dir, "pending"),
            "active": os.path.join(self.base_dir, "active"),
            "done": os.path.join(self.base_dir, "done"),
            "failed": os.path.join(self.base_dir, "failed"),
        }
        for d in self._dirs.values():
            os.makedirs(d, exist_ok=True)

    def push(self, payload: dict) -> str:
        """
        Add an event to the queue.

        Returns the event ID.
        """
        event_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        event = {
            "id": event_id,
            "payload": payload,
            "created_at": time.time(),
            "attempts": 0,
        }
        path = os.path.join(self._dirs["pending"], f"{event_id}.json")
        with open(path, "w") as f:
            json.dump(event, f, indent=2)
        logger.info(f"Enqueued event {event_id} to '{self.name}'")
        return event_id

    def pop(self) -> Optional[dict]:
        """
        Get the next pending event and move it to active.

        Returns the event dict, or None if the queue is empty.
        """
        pending = self._dirs["pending"]
        files = sorted(os.listdir(pending))
        if not files:
            return None

        # Take the oldest event
        filename = files[0]
        src = os.path.join(pending, filename)
        dst = os.path.join(self._dirs["active"], filename)

        try:
            # Atomic move (on same filesystem)
            os.rename(src, dst)
        except FileNotFoundError:
            # Another consumer got it first
            return self.pop()

        with open(dst, "r") as f:
            event = json.load(f)

        event["attempts"] = event.get("attempts", 0) + 1
        event["started_at"] = time.time()

        with open(dst, "w") as f:
            json.dump(event, f, indent=2)

        logger.info(f"Popped event {event['id']} from '{self.name}' (attempt {event['attempts']})")
        return event

    def ack(self, event: dict):
        """Mark an event as successfully processed."""
        event_id = event["id"]
        src = os.path.join(self._dirs["active"], f"{event_id}.json")
        dst = os.path.join(self._dirs["done"], f"{event_id}.json")
        event["completed_at"] = time.time()
        try:
            with open(src, "w") as f:
                json.dump(event, f, indent=2)
            os.rename(src, dst)
            logger.info(f"Acked event {event_id}")
        except FileNotFoundError:
            logger.warning(f"Event {event_id} not found in active queue")

    def nack(self, event: dict, error: str = ""):
        """Mark an event as failed. It can be retried later."""
        event_id = event["id"]
        src = os.path.join(self._dirs["active"], f"{event_id}.json")
        event["error"] = error
        event["failed_at"] = time.time()

        max_attempts = 3
        if event.get("attempts", 0) >= max_attempts:
            # Move to failed (dead letter)
            dst = os.path.join(self._dirs["failed"], f"{event_id}.json")
            logger.error(f"Event {event_id} failed permanently after {max_attempts} attempts: {error}")
        else:
            # Move back to pending for retry
            dst = os.path.join(self._dirs["pending"], f"{event_id}.json")
            logger.warning(f"Event {event_id} failed (attempt {event['attempts']}), re-queuing: {error}")

        try:
            with open(src, "w") as f:
                json.dump(event, f, indent=2)
            os.rename(src, dst)
        except FileNotFoundError:
            logger.warning(f"Event {event_id} not found in active queue")

    def recover_stale(self, max_age_seconds: int = 3600):
        """Move stale active events back to pending (crashed consumer recovery)."""
        active_dir = self._dirs["active"]
        cutoff = time.time() - max_age_seconds
        recovered = 0

        for filename in os.listdir(active_dir):
            path = os.path.join(active_dir, filename)
            try:
                with open(path, "r") as f:
                    event = json.load(f)
                if event.get("started_at", 0) < cutoff:
                    dst = os.path.join(self._dirs["pending"], filename)
                    os.rename(path, dst)
                    recovered += 1
            except Exception as e:
                logger.warning(f"Error recovering stale event {filename}: {e}")

        if recovered:
            logger.info(f"Recovered {recovered} stale events in '{self.name}'")
        return recovered

    def status(self) -> dict:
        """Get queue status counts."""
        return {
            name: len(os.listdir(path))
            for name, path in self._dirs.items()
        }

    def list_pending(self) -> list[dict]:
        """List all pending events."""
        events = []
        for filename in sorted(os.listdir(self._dirs["pending"])):
            path = os.path.join(self._dirs["pending"], filename)
            with open(path, "r") as f:
                events.append(json.load(f))
        return events

    def purge_done(self, max_age_days: int = 7):
        """Remove completed events older than max_age_days."""
        done_dir = self._dirs["done"]
        cutoff = time.time() - (max_age_days * 86400)
        purged = 0
        for filename in os.listdir(done_dir):
            path = os.path.join(done_dir, filename)
            try:
                with open(path, "r") as f:
                    event = json.load(f)
                if event.get("completed_at", 0) < cutoff:
                    os.remove(path)
                    purged += 1
            except Exception as e:
                logger.warning(f"Error purging event {filename}: {e}")
        if purged:
            logger.info(f"Purged {purged} old completed events from '{self.name}'")


def main():
    parser = argparse.ArgumentParser(description="Event Queue Manager")
    parser.add_argument("--queue", required=True, help="Queue name")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--push", metavar="JSON", help="Push event payload")
    parser.add_argument("--recover", action="store_true", help="Recover stale active events")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    q = EventQueue(args.queue)

    if args.status:
        print(json.dumps(q.status(), indent=2))
    elif args.list:
        for event in q.list_pending():
            print(json.dumps(event, indent=2))
    elif args.push:
        payload = json.loads(args.push)
        event_id = q.push(payload)
        print(f"Enqueued: {event_id}")
    elif args.recover:
        n = q.recover_stale()
        print(f"Recovered {n} events")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
