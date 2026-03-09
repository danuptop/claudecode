#!/usr/bin/env python3
"""
Pydantic models for the HirePulse + Hiring Intent pipeline.

These models enforce validation at the extraction boundary — bad data
is rejected before it enters the pipeline, not cleaned up after.

Architectural pattern from: Web3 Jobs Scraper (src/scraper/core/models.py)
Applied to: Up Top Search HirePulse pipeline

Usage:
    from hirepulse.models import (
        CompanyEntity,
        FundraisingEvent,
        HiringSignal,
        HiringIntentReport,
        IngestRecord,
    )
"""

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EntitySource(str, Enum):
    """Where a company entity originated."""
    REPORT_BASE = "report_base"
    CLIENT_BASE = "client_base"
    COMPANIES_YAML = "companies_yaml"
    MANUAL = "manual"


class IntentLevel(str, Enum):
    """Hiring intent strength classification."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class QASeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class QAStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class PipelineErrorKind(str, Enum):
    """Error classification (from Web3 scraper orchestrator/errors.py pattern)."""
    RETRYABLE = "retryable"
    SKIPPABLE = "skippable"
    FATAL = "fatal"


# ---------------------------------------------------------------------------
# Blocked domains for canonical domain selection
# ---------------------------------------------------------------------------

BLOCKED_DOMAINS = frozenset({
    "twitter.com", "x.com", "t.co",
    "linktr.ee", "linktree.com",
    "gmail.com", "outlook.com", "yahoo.com", "hotmail.com",
    "linkedin.com", "facebook.com", "instagram.com",
    "github.com", "medium.com", "substack.com",
    "discord.gg", "discord.com",
    "t.me", "telegram.org",
    "youtube.com", "tiktok.com",
    "crunchbase.com", "pitchbook.com",
    "notion.so", "notion.site",
})


# ---------------------------------------------------------------------------
# Domain utilities
# ---------------------------------------------------------------------------

def extract_domain(url: str) -> Optional[str]:
    """Extract the registrable domain from a URL."""
    if not url:
        return None
    url = url.strip().lower()
    # Strip protocol
    for prefix in ("https://", "http://", "www."):
        if url.startswith(prefix):
            url = url[len(prefix):]
    # Take host part
    host = url.split("/")[0].split("?")[0].split("#")[0]
    if not host or "." not in host:
        return None
    return host


def is_blocked_domain(domain: str) -> bool:
    """Check if a domain is on the social/profile blocklist."""
    if not domain:
        return True
    domain = domain.lower().strip()
    # Check exact match
    if domain in BLOCKED_DOMAINS:
        return True
    # Check if subdomain of blocked domain
    for blocked in BLOCKED_DOMAINS:
        if domain.endswith("." + blocked):
            return True
    return False


def normalize_company_name(name: str) -> str:
    """
    Normalize a company name for matching.

    'Securitize Inc.' -> 'securitize'
    'GoGoPool' -> 'gogopool'
    'RE.AL' -> 'real'
    """
    if not name:
        return ""
    n = name.lower().strip()
    # Remove common suffixes
    for suffix in (" inc.", " inc", " ltd.", " ltd", " llc", " corp.", " corp",
                   " co.", " co", " labs", " protocol", " network", " finance",
                   " capital", " ventures", " foundation"):
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    # Remove non-alphanumeric (but keep spaces for now)
    n = re.sub(r"[^a-z0-9\s]", "", n)
    # Collapse whitespace
    n = re.sub(r"\s+", "", n)
    return n


def domain_brand_score(domain: str, company_name: str) -> float:
    """
    Score how well a domain matches a company brand name.

    'securitize.io' vs 'SECURITIZE' -> 1.0
    'jane.ma' vs 'SECURITIZE' -> 0.0
    """
    if not domain or not company_name:
        return 0.0
    norm_name = normalize_company_name(company_name)
    # Extract domain name without TLD
    parts = domain.lower().split(".")
    if len(parts) < 2:
        return 0.0
    domain_name = parts[0]
    if domain_name == norm_name:
        return 1.0
    if norm_name in domain_name or domain_name in norm_name:
        return 0.7
    return 0.0


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class CompanyEntity(BaseModel):
    """
    A company in the seed universe.

    Validated at ingestion boundary. The canonical_domain is scored and
    selected from available URLs, blocking social/profile domains.
    """
    name: str = Field(..., min_length=1, max_length=500)
    normalized_name: str = Field(default="")
    source: EntitySource
    source_id: Optional[str] = None  # Notion page ID, YAML key, etc.

    # Domain handling
    canonical_domain: Optional[str] = None
    all_domains: list[str] = Field(default_factory=list)
    domain_brand_score: float = 0.0

    # Metadata from source
    report_type: Optional[str] = None  # FUNDRAISING INTEL, LEAD GEN, etc.
    round_amount: Optional[int] = None
    round_type: Optional[str] = None
    investors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # Tracking
    first_seen_at: Optional[str] = None
    last_updated_at: Optional[str] = None
    content_hash: Optional[str] = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def compute_normalized_name(self):
        if not self.normalized_name:
            self.normalized_name = normalize_company_name(self.name)
        return self

    @model_validator(mode="after")
    def select_canonical_domain(self):
        """Pick the best non-blocked domain that matches the brand."""
        if self.canonical_domain and not is_blocked_domain(self.canonical_domain):
            return self
        if not self.all_domains:
            return self

        best_domain = None
        best_score = -1.0
        for d in self.all_domains:
            if is_blocked_domain(d):
                continue
            score = domain_brand_score(d, self.name)
            if score > best_score:
                best_score = score
                best_domain = d

        if best_domain:
            self.canonical_domain = best_domain
            self.domain_brand_score = best_score
        return self

    def content_fingerprint(self) -> str:
        """SHA256 hash of key fields for change detection."""
        data = f"{self.normalized_name}|{self.canonical_domain}|{self.round_amount}|{','.join(sorted(self.investors))}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]


class FundraisingEvent(BaseModel):
    """
    A funding round event. Validated at extraction boundary.

    This is what the Web3 scraper calls a 'StageOutputContract' —
    validation happens BEFORE write, not after.
    """
    company: str = Field(..., min_length=1)
    round_amount: int = Field(..., ge=0)
    round_type: str = Field(default="Unknown")
    investors: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    report_key: str = Field(default="")
    detected_at: str = Field(default="")

    @field_validator("round_amount")
    @classmethod
    def validate_round_amount(cls, v: int) -> int:
        if v > 500_000_000_000:  # > $500B is almost certainly a parse error
            raise ValueError(f"Round amount {v} exceeds $500B — likely a valuation, not a round")
        return v

    @field_validator("investors")
    @classmethod
    def sanitize_investors(cls, v: list[str]) -> list[str]:
        """Apply investor list sanitization at the model boundary."""
        from content_sanitizer import sanitize_investor_list
        return sanitize_investor_list(v)

    @model_validator(mode="after")
    def generate_report_key_if_empty(self):
        if not self.report_key:
            from canonical_template import generate_report_key
            self.report_key = generate_report_key(self.company, self.round_amount)
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()
        return self


class HiringSignal(BaseModel):
    """
    A single hiring signal for a company.

    Error classification prevents transport errors from leaking as data.
    """
    company_name: str
    signal_type: str = Field(..., description="e.g. 'job_posting', 'headcount_growth', 'role_pattern'")
    signal_text: str = Field(default="")
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source: str = Field(default="unknown")
    detected_at: str = Field(default="")

    @field_validator("signal_text")
    @classmethod
    def reject_error_signals(cls, v: str) -> str:
        """
        Prevents F-04/F-05: transport errors emitted as data.
        Validation at the boundary, not cleanup after the fact.
        """
        error_indicators = [
            "HTTPSConnectionPool",
            "Max retries exceeded",
            "Read timed out",
            "[Grok error:",
            "ConnectionError(",
            "Traceback (most recent call last)",
            "requests.exceptions.",
        ]
        for indicator in error_indicators:
            if indicator in v:
                raise ValueError(f"Signal text contains transport error: '{indicator}' — this is not a hiring signal")
        return v


class HiringIntentReport(BaseModel):
    """Hiring intent assessment for a company."""
    company_name: str
    normalized_name: str = ""
    canonical_domain: Optional[str] = None
    intent_level: IntentLevel = IntentLevel.NONE
    signals: list[HiringSignal] = Field(default_factory=list)
    signal_count: int = 0
    job_count: int = 0
    hiring_velocity: Optional[str] = None  # "accelerating", "steady", "decelerating"
    top_roles: list[str] = Field(default_factory=list)
    competitor_benchmark: Optional[str] = None
    generated_at: str = Field(default="")

    @model_validator(mode="after")
    def compute_fields(self):
        if not self.normalized_name:
            self.normalized_name = normalize_company_name(self.company_name)
        if not self.signal_count:
            self.signal_count = len(self.signals)
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()
        return self


class IngestRecord(BaseModel):
    """Record of a single ingest run for audit trail."""
    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    companies_ingested: int = 0
    companies_skipped: int = 0
    companies_errored: int = 0
    source_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    duration_seconds: Optional[float] = None


class DomainDedupeResult(BaseModel):
    """Result of domain-based deduplication for a company name."""
    normalized_name: str
    canonical_domain: Optional[str] = None
    domain_candidates: list[dict[str, Any]] = Field(default_factory=list)
    split_into: list[str] = Field(default_factory=list)  # If same name splits into multiple entities
    decision: str = ""  # "merged", "split", "single"
    reason: str = ""


class OverlapEntry(BaseModel):
    """A detected overlap between pipeline artifacts."""
    entity_name: str
    pipelines: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    overlap_type: str = ""  # "full_duplicate", "partial_overlap", "complementary"
    recommendation: str = ""
    owner: Optional[str] = None


# ---------------------------------------------------------------------------
# Error classification (from Web3 scraper pattern)
# ---------------------------------------------------------------------------

class PipelineError(Exception):
    """Base pipeline error with classification."""
    kind: PipelineErrorKind = PipelineErrorKind.RETRYABLE

    def __init__(self, message: str, kind: PipelineErrorKind = PipelineErrorKind.RETRYABLE):
        super().__init__(message)
        self.kind = kind


class RetryableError(PipelineError):
    """Network timeout, transient API failure — retry this stage."""
    def __init__(self, message: str):
        super().__init__(message, PipelineErrorKind.RETRYABLE)


class SkippableError(PipelineError):
    """Skip this company/entity but continue pipeline."""
    def __init__(self, message: str):
        super().__init__(message, PipelineErrorKind.SKIPPABLE)


class FatalError(PipelineError):
    """Stop pipeline immediately (auth failure, DB corruption)."""
    def __init__(self, message: str):
        super().__init__(message, PipelineErrorKind.FATAL)


def classify_error(exc: Exception) -> PipelineErrorKind:
    """
    Classify an exception into pipeline error categories.

    This is the error classification layer that prevents transport
    errors from being emitted as data (F-04, F-05).
    """
    exc_name = type(exc).__name__
    exc_str = str(exc).lower()

    # Fatal errors — stop pipeline
    fatal_patterns = ["auth", "forbidden", "invalid api key", "quota exceeded"]
    if any(p in exc_str for p in fatal_patterns):
        return PipelineErrorKind.FATAL

    # Retryable — network/timeout issues
    retryable_names = {
        "ConnectionError", "TimeoutError", "ReadTimeout", "ConnectTimeout",
        "MaxRetryError", "Timeout",
    }
    if exc_name in retryable_names:
        return PipelineErrorKind.RETRYABLE

    retryable_patterns = [
        "connection", "timeout", "timed out", "max retries",
        "httpsconnectionpool", "rate limit", "429", "503", "502",
    ]
    if any(p in exc_str for p in retryable_patterns):
        return PipelineErrorKind.RETRYABLE

    # Skippable — bad data for this entity, but pipeline can continue
    skippable_patterns = [
        "not found", "404", "no data", "empty response",
        "parse error", "validation error",
    ]
    if any(p in exc_str for p in skippable_patterns):
        return PipelineErrorKind.SKIPPABLE

    # Default: retryable (safer than skippable)
    return PipelineErrorKind.RETRYABLE
