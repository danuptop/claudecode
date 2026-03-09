#!/usr/bin/env python3
"""
HirePulse Ingest — Builds the seed universe from Report Base + Client Base + companies.yaml.

Architectural patterns applied from Web3 Jobs Scraper:
- Source configuration (companies.yaml) — single source of truth
- Pydantic models — validation at extraction boundary
- Domain-aware dedup — canonical domain scoring, blocked domain filtering
- Content hashing — format-independent change detection
- Error classification — RetryableError/SkippableError/FatalError

Usage:
    from hirepulse.hirepulse_ingest import run_ingest

    result = run_ingest(
        companies_yaml="config/companies.yaml",
        output_dir="reports/hirepulse",
        env_file=".env.hirepulse",
    )

CLI:
    python3 -m hirepulse.hirepulse_ingest \\
        --companies-yaml config/companies.yaml \\
        --output-dir reports/hirepulse \\
        --env-file .env.hirepulse

Deployment:
    Tony: /home/ubuntu/clawd/scripts/hirepulse/hirepulse_ingest.py
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from hirepulse.models import (
    CompanyEntity,
    DomainDedupeResult,
    EntitySource,
    FatalError,
    IngestRecord,
    RetryableError,
    SkippableError,
    classify_error,
    domain_brand_score,
    extract_domain,
    is_blocked_domain,
    normalize_company_name,
)

logger = logging.getLogger("hirepulse.ingest")


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------

def load_companies_yaml(path: str) -> list[CompanyEntity]:
    """Load companies from the YAML seed universe config."""
    if not os.path.exists(path):
        raise FatalError(f"companies.yaml not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    companies = data.get("companies", [])
    entities = []
    for c in companies:
        try:
            entity = CompanyEntity(
                name=c["name"],
                source=EntitySource(c.get("source", "companies_yaml")),
                source_id=c.get("source_id"),
                canonical_domain=c.get("canonical_domain"),
                all_domains=c.get("domains", []),
                report_type=c.get("report_type"),
                round_amount=c.get("round_amount"),
                round_type=c.get("round_type"),
                tags=c.get("tags", []),
            )
            entities.append(entity)
        except Exception as e:
            logger.warning(f"Skipping invalid YAML entry '{c.get('name', '?')}': {e}")

    logger.info(f"Loaded {len(entities)} companies from {path}")
    return entities


def load_report_base(
    db_id: str,
    notion_token: Optional[str] = None,
    page_types: Optional[list[str]] = None,
) -> list[CompanyEntity]:
    """
    Load companies from Report Base Notion database.

    Queries FUNDRAISING INTEL and LEAD GEN pages by default.
    """
    if page_types is None:
        page_types = ["FUNDRAISING INTEL", "LEAD GEN"]

    token = notion_token or os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
    if not token:
        logger.warning("No Notion token — skipping Report Base load")
        return []

    try:
        from notion_client import Client
        notion = Client(auth=token)
    except ImportError:
        logger.warning("notion-client not installed — skipping Report Base load")
        return []

    entities = []
    for page_type in page_types:
        try:
            results = notion.databases.query(
                database_id=db_id,
                filter={
                    "property": "TYPE",
                    "select": {"equals": page_type},
                },
            )

            for page in results.get("results", []):
                try:
                    entity = _page_to_entity(page, EntitySource.REPORT_BASE)
                    if entity:
                        entities.append(entity)
                except Exception as e:
                    logger.warning(f"Skipping page {page.get('id', '?')}: {e}")

        except Exception as e:
            kind = classify_error(e)
            if kind.value == "fatal":
                raise FatalError(f"Report Base query failed: {e}")
            logger.warning(f"Error querying Report Base for {page_type}: {e}")

    logger.info(f"Loaded {len(entities)} companies from Report Base")
    return entities


def load_client_base(
    db_id: str,
    notion_token: Optional[str] = None,
) -> list[CompanyEntity]:
    """Load companies from Client Base Notion database."""
    token = notion_token or os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY")
    if not token:
        logger.warning("No Notion token — skipping Client Base load")
        return []

    try:
        from notion_client import Client
        notion = Client(auth=token)
    except ImportError:
        logger.warning("notion-client not installed — skipping Client Base load")
        return []

    entities = []
    try:
        results = notion.databases.query(database_id=db_id)
        for page in results.get("results", []):
            try:
                entity = _page_to_entity(page, EntitySource.CLIENT_BASE)
                if entity:
                    entities.append(entity)
            except Exception as e:
                logger.warning(f"Skipping client page {page.get('id', '?')}: {e}")
    except Exception as e:
        kind = classify_error(e)
        if kind.value == "fatal":
            raise FatalError(f"Client Base query failed: {e}")
        logger.warning(f"Error querying Client Base: {e}")

    logger.info(f"Loaded {len(entities)} companies from Client Base")
    return entities


def _page_to_entity(page: dict, source: EntitySource) -> Optional[CompanyEntity]:
    """Convert a Notion page to a CompanyEntity."""
    props = page.get("properties", {})

    # Extract company name
    name = _get_text_prop(props, "COMPANY") or _get_text_prop(props, "ENTRY")
    if not name or name.startswith("[ARCHIVED"):
        return None

    # Clean up name (remove " — $31M SERIES B | FUNDING INTEL | MAR 8, 2026")
    if " — " in name:
        name = name.split(" — ")[0].strip()

    # Extract domains from any URL-like properties
    domains = []
    for key in ("WEBSITE", "URL", "DOMAIN"):
        url = _get_text_prop(props, key)
        if url:
            d = extract_domain(url)
            if d:
                domains.append(d)

    # Extract round amount
    amount_prop = props.get("ROUND AMOUNT", {})
    round_amount = None
    if amount_prop.get("type") == "number":
        round_amount = amount_prop.get("number")

    return CompanyEntity(
        name=name,
        source=source,
        source_id=page.get("id"),
        all_domains=domains,
        report_type=_get_select_prop(props, "TYPE"),
        round_amount=round_amount,
        round_type=_get_text_prop(props, "ROUND TYPE"),
        first_seen_at=page.get("created_time"),
        last_updated_at=page.get("last_edited_time"),
    )


# ---------------------------------------------------------------------------
# Domain deduplication
# ---------------------------------------------------------------------------

def dedupe_by_domain(entities: list[CompanyEntity]) -> tuple[list[CompanyEntity], list[DomainDedupeResult]]:
    """
    Deduplicate companies using domain-aware matching.

    Strategy:
    1. Group by normalized_name
    2. Within each group, score domains against brand name
    3. If domains conflict (e.g., 'gogopool.com' vs 'avantprotocol.com'),
       split into separate entities (multi-bucket split)
    4. If domains match or one entity has no domain, merge

    Returns:
        Tuple of (deduped entities, dedup audit trail)
    """
    # Group by normalized name
    name_groups: dict[str, list[CompanyEntity]] = {}
    for entity in entities:
        key = entity.normalized_name
        if key not in name_groups:
            name_groups[key] = []
        name_groups[key].append(entity)

    deduped = []
    audit = []

    for norm_name, group in name_groups.items():
        if len(group) == 1:
            deduped.append(group[0])
            audit.append(DomainDedupeResult(
                normalized_name=norm_name,
                canonical_domain=group[0].canonical_domain,
                decision="single",
                reason="Only one entity with this name",
            ))
            continue

        # Collect all domains across the group
        domain_map: dict[str, list[CompanyEntity]] = {}
        no_domain: list[CompanyEntity] = []
        for entity in group:
            if entity.canonical_domain and not is_blocked_domain(entity.canonical_domain):
                d = entity.canonical_domain
                if d not in domain_map:
                    domain_map[d] = []
                domain_map[d].append(entity)
            else:
                no_domain.append(entity)

        if len(domain_map) <= 1:
            # All entities share same domain (or no domain) — merge
            merged = _merge_entities(group)
            deduped.append(merged)
            audit.append(DomainDedupeResult(
                normalized_name=norm_name,
                canonical_domain=merged.canonical_domain,
                domain_candidates=[
                    {"domain": d, "count": len(ents)}
                    for d, ents in domain_map.items()
                ],
                decision="merged",
                reason=f"All {len(group)} entities share domain or have no domain",
            ))
        else:
            # Conflicting domains — split into separate entities
            split_names = []
            for domain, domain_entities in domain_map.items():
                merged = _merge_entities(domain_entities + no_domain)
                merged.canonical_domain = domain
                deduped.append(merged)
                split_names.append(f"{norm_name}@{domain}")

            audit.append(DomainDedupeResult(
                normalized_name=norm_name,
                canonical_domain=None,
                domain_candidates=[
                    {"domain": d, "count": len(ents)}
                    for d, ents in domain_map.items()
                ],
                split_into=split_names,
                decision="split",
                reason=f"Conflicting domains: {list(domain_map.keys())}",
            ))

    logger.info(
        f"Domain dedup: {len(entities)} -> {len(deduped)} entities "
        f"({len(entities) - len(deduped)} merged/removed)"
    )
    return deduped, audit


def _merge_entities(entities: list[CompanyEntity]) -> CompanyEntity:
    """Merge multiple entities for the same company, preferring higher-priority sources."""
    if len(entities) == 1:
        return entities[0]

    # Priority: client_base > report_base > companies_yaml > manual
    source_priority = {
        EntitySource.CLIENT_BASE: 0,
        EntitySource.REPORT_BASE: 1,
        EntitySource.COMPANIES_YAML: 2,
        EntitySource.MANUAL: 3,
    }
    entities.sort(key=lambda e: source_priority.get(e.source, 99))
    primary = entities[0]

    # Merge all domains
    all_domains = set()
    for e in entities:
        all_domains.update(e.all_domains)
        if e.canonical_domain:
            all_domains.add(e.canonical_domain)

    # Merge tags
    all_tags = []
    seen_tags = set()
    for e in entities:
        for tag in e.tags:
            if tag.lower() not in seen_tags:
                seen_tags.add(tag.lower())
                all_tags.append(tag)

    # Merge investors
    all_investors = []
    seen_inv = set()
    for e in entities:
        for inv in e.investors:
            if inv.lower() not in seen_inv:
                seen_inv.add(inv.lower())
                all_investors.append(inv)

    return CompanyEntity(
        name=primary.name,
        source=primary.source,
        source_id=primary.source_id,
        canonical_domain=primary.canonical_domain,
        all_domains=list(all_domains),
        report_type=primary.report_type,
        round_amount=primary.round_amount or next(
            (e.round_amount for e in entities if e.round_amount), None
        ),
        round_type=primary.round_type or next(
            (e.round_type for e in entities if e.round_type), None
        ),
        investors=all_investors,
        tags=all_tags,
        first_seen_at=primary.first_seen_at,
        last_updated_at=max(
            (e.last_updated_at for e in entities if e.last_updated_at),
            default=primary.last_updated_at,
        ),
    )


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

def _get_text_prop(props: dict, name: str) -> str:
    prop = props.get(name, {})
    prop_type = prop.get("type", "")
    if prop_type == "rich_text":
        parts = prop.get("rich_text", [])
        return "".join(p.get("plain_text", "") for p in parts).strip()
    if prop_type == "title":
        parts = prop.get("title", [])
        return "".join(p.get("plain_text", "") for p in parts).strip()
    return ""


def _get_select_prop(props: dict, name: str) -> Optional[str]:
    prop = props.get(name, {})
    if prop.get("type") == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    return None


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def run_ingest(
    companies_yaml: str = "config/companies.yaml",
    output_dir: str = "reports/hirepulse",
    report_base_db_id: Optional[str] = None,
    client_base_db_id: Optional[str] = None,
    env_file: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run the full ingest pipeline: load sources -> dedupe -> output universe.

    Returns:
        Dict with seed_universe, dedup_audit, and ingest_record.
    """
    if env_file and os.path.exists(env_file):
        _load_env_file(env_file)

    run_id = str(uuid.uuid4())[:8]
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"Ingest starting (run_id={run_id})")

    # Resolve database IDs
    rb_db = report_base_db_id or os.getenv(
        "REPORT_BASE_DB", "902d47be-68c0-4da8-832a-a52272fc7b39"
    )
    cb_db = client_base_db_id or os.getenv(
        "CLIENT_BASE_DB", "14dba737-d3d2-4b1d-b1d9-ba9ef4f4141a"
    )

    # Load from all sources
    all_entities: list[CompanyEntity] = []
    source_counts = {}

    # 1. companies.yaml (always loaded)
    yaml_entities = load_companies_yaml(companies_yaml)
    all_entities.extend(yaml_entities)
    source_counts["companies_yaml"] = len(yaml_entities)

    # 2. Report Base (if Notion available)
    rb_entities = load_report_base(rb_db)
    all_entities.extend(rb_entities)
    source_counts["report_base"] = len(rb_entities)

    # 3. Client Base (if Notion available)
    cb_entities = load_client_base(cb_db)
    all_entities.extend(cb_entities)
    source_counts["client_base"] = len(cb_entities)

    logger.info(f"Total entities loaded: {len(all_entities)} from {source_counts}")

    # Deduplicate with domain awareness
    deduped, dedup_audit = dedupe_by_domain(all_entities)

    # Compute content hashes for change detection
    for entity in deduped:
        entity.content_hash = entity.content_fingerprint()

    # Build ingest record
    completed_at = datetime.now(timezone.utc).isoformat()
    record = IngestRecord(
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        companies_ingested=len(deduped),
        companies_skipped=len(all_entities) - len(deduped),
        source_counts=source_counts,
    )

    # Write output artifacts
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Seed universe
    universe_path = os.path.join(output_dir, "seed_universe_latest.json")
    universe_data = {
        "generated_at": completed_at,
        "run_id": run_id,
        "total_companies": len(deduped),
        "source_counts": source_counts,
        "companies": [e.model_dump(mode="json") for e in deduped],
    }
    with open(universe_path, "w") as f:
        json.dump(universe_data, f, indent=2)
    logger.info(f"Seed universe written: {universe_path} ({len(deduped)} companies)")

    # Domain dedup audit
    dedup_path = os.path.join(output_dir, "domain_dedupe_audit_latest.json")
    with open(dedup_path, "w") as f:
        json.dump(
            {
                "generated_at": completed_at,
                "run_id": run_id,
                "total_before": len(all_entities),
                "total_after": len(deduped),
                "decisions": [d.model_dump(mode="json") for d in dedup_audit],
            },
            f,
            indent=2,
        )
    logger.info(f"Dedup audit written: {dedup_path}")

    # Ingest record
    record_path = os.path.join(output_dir, "ingest_record_latest.json")
    with open(record_path, "w") as f:
        json.dump(record.model_dump(mode="json"), f, indent=2)

    return {
        "seed_universe": deduped,
        "dedup_audit": dedup_audit,
        "ingest_record": record,
        "artifacts": {
            "seed_universe": universe_path,
            "domain_dedupe_audit": dedup_path,
            "ingest_record": record_path,
        },
    }


def _load_env_file(path: str):
    """Load a .env file into os.environ."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="HirePulse Ingest — Build seed universe")
    parser.add_argument("--companies-yaml", default="config/companies.yaml")
    parser.add_argument("--output-dir", default="reports/hirepulse")
    parser.add_argument("--report-base-db-id")
    parser.add_argument("--client-base-db-id")
    parser.add_argument("--env-file")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    result = run_ingest(
        companies_yaml=args.companies_yaml,
        output_dir=args.output_dir,
        report_base_db_id=args.report_base_db_id,
        client_base_db_id=args.client_base_db_id,
        env_file=args.env_file,
    )

    print(f"\nIngest complete:")
    print(f"  Companies: {result['ingest_record'].companies_ingested}")
    print(f"  Sources: {result['ingest_record'].source_counts}")
    for name, path in result["artifacts"].items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
