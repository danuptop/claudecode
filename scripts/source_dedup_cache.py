#!/usr/bin/env python3
"""
Source Article Dedup Cache.

Prevents processing the same funding event multiple times when it appears
in different news sources on different days (the OKX pattern: Mar 5, 6, 7).

Uses a local JSON file as a persistent cache of processed source URLs and
company+event fingerprints.

Usage:
    from source_dedup_cache import SourceDedupCache

    cache = SourceDedupCache()

    # Check before processing
    if cache.is_duplicate(source_url="https://...", company="OKX", amount=200000000):
        logger.info("Already processed this event — skipping")
        return

    # After successful processing, record it
    cache.record(
        source_url="https://...",
        company="OKX",
        amount=200000000,
        page_id="31cf30f9-...",
    )

Deployment:
    Place at: /home/ubuntu/clawd/scripts/source_dedup_cache.py
    Data file: /home/ubuntu/clawd/data/source_dedup_cache.json (auto-created)
"""

import hashlib
import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("source_dedup_cache")

DEFAULT_CACHE_PATH = os.path.join(
    os.getenv("CLAWD_DATA_DIR", os.path.expanduser("/home/ubuntu/clawd/data")),
    "source_dedup_cache.json",
)

# Cache entries older than this are pruned on load
MAX_AGE_DAYS = 90


class SourceDedupCache:
    """Persistent dedup cache for source articles and funding events."""

    def __init__(self, cache_path: str = DEFAULT_CACHE_PATH):
        self.cache_path = cache_path
        self._entries: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r") as f:
                    data = json.load(f)
                cutoff = time.time() - (MAX_AGE_DAYS * 86400)
                self._entries = {
                    k: v for k, v in data.items()
                    if v.get("timestamp", 0) > cutoff
                }
                pruned = len(data) - len(self._entries)
                if pruned > 0:
                    logger.info(f"Pruned {pruned} stale cache entries (>{MAX_AGE_DAYS}d)")
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Cache load failed ({e}), starting fresh")
                self._entries = {}
        else:
            self._entries = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(self._entries, f, indent=2)

    @staticmethod
    def _url_key(url: str) -> str:
        normalized = url.strip().lower().rstrip("/")
        return f"url:{hashlib.sha256(normalized.encode()).hexdigest()[:16]}"

    @staticmethod
    def _event_key(company: str, amount: int) -> str:
        slug = company.strip().lower().replace(" ", "-")
        # Bucket amounts to nearest 5% to catch $4.2M vs $4.25M
        if amount > 0:
            step = max(1, int(amount * 0.05))
            bucket = (amount // step) * step
        else:
            bucket = 0
        return f"event:{slug}:{bucket}"

    def is_duplicate(
        self,
        source_url: Optional[str] = None,
        company: Optional[str] = None,
        amount: Optional[int] = None,
    ) -> bool:
        """
        Check if this source URL or funding event has been processed before.

        Returns True if either:
        - The exact source URL has been seen, OR
        - The same company+amount combination has been seen (within tolerance)
        """
        if source_url:
            key = self._url_key(source_url)
            if key in self._entries:
                logger.info(
                    f"Source URL already processed: {source_url[:80]}... "
                    f"(page_id={self._entries[key].get('page_id', '?')})"
                )
                return True

        if company and amount is not None:
            key = self._event_key(company, amount)
            if key in self._entries:
                logger.info(
                    f"Event already processed: {company} ${amount} "
                    f"(page_id={self._entries[key].get('page_id', '?')})"
                )
                return True

        return False

    def record(
        self,
        source_url: Optional[str] = None,
        company: Optional[str] = None,
        amount: Optional[int] = None,
        page_id: Optional[str] = None,
    ):
        """Record a processed source URL and/or funding event."""
        now = time.time()
        entry = {
            "timestamp": now,
            "page_id": page_id,
            "company": company,
            "amount": amount,
            "source_url": source_url,
        }

        if source_url:
            self._entries[self._url_key(source_url)] = entry

        if company and amount is not None:
            self._entries[self._event_key(company, amount)] = entry

        self._save()

    def get_page_id(
        self,
        source_url: Optional[str] = None,
        company: Optional[str] = None,
        amount: Optional[int] = None,
    ) -> Optional[str]:
        """Get the page ID for a previously processed event, if any."""
        if source_url:
            key = self._url_key(source_url)
            if key in self._entries:
                return self._entries[key].get("page_id")

        if company and amount is not None:
            key = self._event_key(company, amount)
            if key in self._entries:
                return self._entries[key].get("page_id")

        return None

    @property
    def size(self) -> int:
        return len(self._entries)
