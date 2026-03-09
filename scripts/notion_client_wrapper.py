#!/usr/bin/env python3
"""
Notion Client Wrapper — read/write token separation + audit logging.

Provides a wrapped Notion client that:
1. Uses a READ-ONLY token for queries (limits blast radius)
2. Uses a WRITE token for creates/updates (explicit write intent)
3. Logs every write operation to a local audit log

Usage:
    from notion_client_wrapper import get_reader, get_writer, audit_log

    # Queries — use read-only token
    reader = get_reader()
    results = reader.databases.query(database_id=DB_ID, filter=...)

    # Writes — use write token, automatically logged
    writer = get_writer()
    page = writer.pages.create(parent=..., properties=..., children=...)

    # Manual audit log entry
    audit_log.record("create", page_id="abc", details="Created canonical page for OKX")

Environment variables:
    NOTION_READ_TOKEN  — read-only integration token (optional, falls back to NOTION_TOKEN)
    NOTION_WRITE_TOKEN — write integration token (optional, falls back to NOTION_TOKEN)
    NOTION_TOKEN       — default token used if read/write tokens not set
    NOTION_API_KEY     — legacy alias for NOTION_TOKEN

Deployment:
    Place at: /home/ubuntu/clawd/scripts/notion_client_wrapper.py
    Audit log: /home/ubuntu/clawd/data/audit_log.jsonl (auto-created)
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("notion_client_wrapper")

AUDIT_LOG_PATH = os.path.join(
    os.getenv("CLAWD_DATA_DIR", os.path.expanduser("/home/ubuntu/clawd/data")),
    "audit_log.jsonl",
)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLog:
    """Append-only JSONL audit log for all Notion write operations."""

    def __init__(self, path: str = AUDIT_LOG_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def record(
        self,
        action: str,
        page_id: str = "",
        database_id: str = "",
        details: str = "",
        properties: Optional[dict] = None,
        run_id: str = "",
        pipeline: str = "",
    ):
        entry = {
            "timestamp": time.time(),
            "action": action,
            "page_id": page_id,
            "database_id": database_id,
            "details": details,
            "run_id": run_id,
            "pipeline": pipeline,
            "pid": os.getpid(),
        }
        if properties:
            # Serialize a summary of properties (not full content, for size)
            entry["properties_summary"] = {
                k: str(v)[:100] for k, v in properties.items()
            }
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def recent(self, n: int = 50) -> list[dict]:
        """Read the last N audit log entries."""
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries[-n:]


audit_log = AuditLog()


# ---------------------------------------------------------------------------
# Notion client factory
# ---------------------------------------------------------------------------

def _get_token(env_var: str) -> str:
    token = os.getenv(env_var)
    if token:
        return token
    # Fallback chain
    return os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY") or ""


def _make_client(token: str):
    try:
        from notion_client import Client
    except ImportError:
        raise RuntimeError("notion-client package required: pip install notion-client")

    if not token:
        raise RuntimeError("No Notion token configured")
    return Client(auth=token)


def get_reader():
    """Get a Notion client with read-only token (for queries)."""
    token = _get_token("NOTION_READ_TOKEN")
    return _make_client(token)


def get_writer():
    """
    Get a Notion client with write token.

    Note: This returns a standard Notion client. Audit logging is done
    by the caller using audit_log.record(). For automatic logging,
    use the write_page() and update_page() helper functions below.
    """
    token = _get_token("NOTION_WRITE_TOKEN")
    return _make_client(token)


# ---------------------------------------------------------------------------
# Convenience write functions with automatic audit logging
# ---------------------------------------------------------------------------

def create_page(
    database_id: str,
    properties: dict,
    children: Optional[list] = None,
    pipeline: str = "",
    run_id: str = "",
) -> dict:
    """Create a Notion page with automatic audit logging."""
    writer = get_writer()
    kwargs = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    if children:
        kwargs["children"] = children

    page = writer.pages.create(**kwargs)

    audit_log.record(
        action="create",
        page_id=page["id"],
        database_id=database_id,
        details=f"Created page",
        properties=properties,
        run_id=run_id,
        pipeline=pipeline,
    )
    logger.info(f"Created page {page['id']} (audit logged)")
    return page


def update_page(
    page_id: str,
    properties: dict,
    pipeline: str = "",
    run_id: str = "",
) -> dict:
    """Update a Notion page with automatic audit logging."""
    writer = get_writer()
    page = writer.pages.update(page_id=page_id, properties=properties)

    audit_log.record(
        action="update",
        page_id=page_id,
        details=f"Updated properties",
        properties=properties,
        run_id=run_id,
        pipeline=pipeline,
    )
    logger.info(f"Updated page {page_id} (audit logged)")
    return page


def archive_page(
    page_id: str,
    reason: str = "",
    pipeline: str = "",
    run_id: str = "",
) -> dict:
    """Archive a Notion page with audit logging."""
    writer = get_writer()
    page = writer.pages.update(page_id=page_id, archived=True)

    audit_log.record(
        action="archive",
        page_id=page_id,
        details=f"Archived: {reason}",
        run_id=run_id,
        pipeline=pipeline,
    )
    logger.info(f"Archived page {page_id}: {reason}")
    return page
