#!/usr/bin/env python3
"""
Page Registry — deterministic ID tracking for idempotent page creation.

Generates a deterministic fingerprint from (company, round_type, amount_bucket)
and tracks which Notion page IDs correspond to which fingerprints. This is a
client-side dedup cache that doesn't depend on Notion query timing.

Usage:
    from page_registry import PageRegistry

    registry = PageRegistry()

    # Before creating a page, check if we already created one for this event
    fingerprint = registry.fingerprint("OKX", "STRATEGIC", 200000000)
    existing = registry.get(fingerprint)
    if existing:
        logger.info(f"Page already exists: {existing['page_id']}")
        return existing["page_id"]

    # After creating, register it
    page = notion.pages.create(...)
    registry.register(fingerprint, page_id=page["id"], company="OKX")

Deployment:
    Place at: /home/ubuntu/clawd/scripts/page_registry.py
    Data file: /home/ubuntu/clawd/data/page_registry.json (auto-created)
"""

import hashlib
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("page_registry")

DEFAULT_REGISTRY_PATH = os.path.join(
    os.getenv("CLAWD_DATA_DIR", os.path.expanduser("/home/ubuntu/clawd/data")),
    "page_registry.json",
)


class PageRegistry:
    """Local registry mapping event fingerprints to Notion page IDs."""

    def __init__(self, registry_path: str = DEFAULT_REGISTRY_PATH):
        self.registry_path = registry_path
        self._entries: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.registry_path):
            try:
                with open(self.registry_path, "r") as f:
                    self._entries = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Registry load failed ({e}), starting fresh")
                self._entries = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.registry_path), exist_ok=True)
        with open(self.registry_path, "w") as f:
            json.dump(self._entries, f, indent=2)

    @staticmethod
    def fingerprint(company: str, round_type: str, amount: int) -> str:
        """
        Generate a deterministic fingerprint for a funding event.

        The amount is bucketed to ±5% to handle slight variations.
        """
        slug = company.strip().lower().replace(" ", "-")
        rtype = round_type.strip().upper()

        # Bucket amount to nearest 5% to catch $4.2M vs $4.25M
        if amount > 0:
            step = max(1, int(amount * 0.05))
            bucketed = (amount // step) * step
        else:
            bucketed = 0

        raw = f"{slug}:{rtype}:{bucketed}"
        return hashlib.sha256(raw.encode()).hexdigest()[:20]

    def get(self, fingerprint: str) -> Optional[dict]:
        """Get registry entry for a fingerprint, or None."""
        return self._entries.get(fingerprint)

    def get_by_company(self, company: str) -> list[dict]:
        """Find all registry entries for a company (case-insensitive)."""
        slug = company.strip().lower()
        return [
            v for v in self._entries.values()
            if v.get("company", "").lower() == slug
        ]

    def register(
        self,
        fingerprint: str,
        page_id: str,
        company: str = "",
        round_type: str = "",
        amount: int = 0,
        report_key: str = "",
    ):
        """Register a new page creation."""
        self._entries[fingerprint] = {
            "page_id": page_id,
            "company": company,
            "round_type": round_type,
            "amount": amount,
            "report_key": report_key,
            "registered_at": time.time(),
        }
        self._save()
        logger.info(f"Registered page {page_id} for {company} (fp={fingerprint[:10]}...)")

    def unregister(self, fingerprint: str) -> bool:
        """Remove a fingerprint from the registry (e.g., when archiving a page)."""
        if fingerprint in self._entries:
            del self._entries[fingerprint]
            self._save()
            return True
        return False

    @property
    def size(self) -> int:
        return len(self._entries)

    def list_all(self) -> dict:
        """Return all entries (for debugging)."""
        return dict(self._entries)
