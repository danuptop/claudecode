#!/usr/bin/env python3
"""
Domain resolver and validation for Report Base enrichment pipeline.

Solves the F-10 class of bugs: the pipeline scrapes the WRONG website
because a company name maps to a different organization's domain.

Example failure (KAST, Mar 09 2026):
  - Target: KAST (stablecoin neobank, kast.xyz, Singapore)
  - Pipeline scraped: kast.com (Kootenay Association for Science & Tech, BC)
  - Result: Wrong POC, wrong description, wrong everything

This module provides:
  1. A known-domains table for crypto/fintech companies
  2. Multi-TLD domain probing (.xyz, .io, .co, .com, etc.)
  3. Post-scrape validation that scraped content matches target company
  4. Grok-based domain resolution as last resort

Usage:
    from domain_resolver import resolve_domain, validate_scraped_domain

    # Resolve company to domain
    domain = resolve_domain("KAST", context="stablecoin payments $80M Series A")
    # Returns: "kast.xyz"

    # Validate scraped content matches
    is_valid = validate_scraped_domain(
        target_company="KAST",
        scraped_domain="kast.com",
        scraped_description="Since 1998, KAST has fostered a culture...",
        deal_context="$80M Series A, stablecoin payments, QED Investors",
    )
    # Returns: False (description is about a Canadian science org, not crypto)

Deployment:
    Place at: scripts/domain_resolver.py
    Import from: founder-intel-pipeline.py
"""

import logging
import re
from typing import Optional

logger = logging.getLogger("domain_resolver")


# ---------------------------------------------------------------------------
# Known domain mappings for crypto/fintech companies.
#
# Add entries here when the pipeline scrapes the wrong domain.
# Format: lowercase company slug → correct domain
#
# This is the FIRST lookup — before any web scraping.
# ---------------------------------------------------------------------------

KNOWN_DOMAINS: dict[str, str] = {
    # --- Verified from Mar 09 2026 audit ---
    "kast": "kast.xyz",
    "crossover-markets": "crossovermarkets.com",
    "usd.ai": "usd.ai",
    "usd-ai": "usd.ai",
    "layerzero": "layerzero.network",
    "izumi": "izumi.finance",
    "crema": "crema.finance",
    "crema-finance": "crema.finance",
    # --- Verified from Mar 09 2026 full page audit ---
    "backpack": "backpack.exchange",
    "novig": "novig.bet",
    "tapioca": "tapioca.xyz",
    "tapioca-dao": "tapioca.xyz",
    "euclid": "euclidprotocol.com",
    "euclid-protocol": "euclidprotocol.com",
    "interstate": "interstate.so",
    "helios": "helios.finance",
    "helios-finance": "helios.finance",
    "probable": "probable.bet",
    "akave": "akave.ai",
    "arq": "arq.network",

    # --- Common crypto companies with non-obvious domains ---
    "alchemy": "alchemy.com",
    "arbitrum": "arbitrum.io",
    "avalanche": "avax.network",
    "celestia": "celestia.org",
    "circle": "circle.com",
    "coinbase": "coinbase.com",
    "dydx": "dydx.exchange",
    "eigenlayer": "eigenlayer.xyz",
    "ethena": "ethena.fi",
    "fireblocks": "fireblocks.com",
    "helium": "helium.com",
    "jupiter": "jup.ag",
    "kamino": "kamino.finance",
    "lido": "lido.fi",
    "magic-eden": "magiceden.io",
    "maker": "makerdao.com",
    "monad": "monad.xyz",
    "movement": "movementlabs.xyz",
    "near": "near.org",
    "opensea": "opensea.io",
    "phantom": "phantom.app",
    "polymarket": "polymarket.com",
    "solana": "solana.com",
    "starknet": "starknet.io",
    "sui": "sui.io",
    "uniswap": "uniswap.org",
    "wormhole": "wormhole.com",
}


# TLDs commonly used by crypto/fintech companies, ordered by priority.
# The pipeline should probe these BEFORE defaulting to .com.
CRYPTO_TLDS = [
    ".xyz", ".io", ".co", ".finance", ".network",
    ".exchange", ".fi", ".app", ".ai", ".org",
    ".com",  # last — most likely to be squatted by unrelated orgs
]

# Keywords that indicate a crypto/fintech company (used for validation)
_CRYPTO_KEYWORDS = {
    "blockchain", "crypto", "defi", "web3", "token", "stablecoin",
    "nft", "dao", "dex", "cefi", "custody", "wallet", "mining",
    "validator", "staking", "yield", "liquidity", "protocol",
    "smart contract", "solana", "ethereum", "bitcoin", "layer 2",
    "layer2", "l2", "rollup", "zk", "cross-chain", "bridge",
    "fintech", "neobank", "payments", "remittance", "lending",
    "institutional", "trading", "exchange", "otc", "prime brokerage",
}

# Keywords that indicate a NON-crypto organization
_NON_CRYPTO_KEYWORDS = {
    "university", "school", "college", "association", "municipality",
    "county", "city of", "government", "church", "hospital",
    "museum", "library", "foundation for", "society of",
    "chamber of commerce", "tourism", "visitor", "park district",
    "nonprofit", "non-profit", "charity",
}


def resolve_domain(
    company_name: str,
    context: str = "",
    probe_fn: Optional[callable] = None,
) -> Optional[str]:
    """
    Resolve a company name to its actual domain.

    Args:
        company_name: Company name (e.g., "KAST", "Crossover Markets").
        context: Deal context for disambiguation (e.g., "stablecoin $80M Series A").
        probe_fn: Optional function(domain: str) -> bool that checks if a
                  domain exists and looks like a real company site.
                  If None, only the known-domains table is used.

    Returns:
        Domain string (e.g., "kast.xyz") or None if not resolved.
    """
    slug = _slugify(company_name)

    # 1. Check known domains table
    if slug in KNOWN_DOMAINS:
        domain = KNOWN_DOMAINS[slug]
        logger.info(f"Domain for '{company_name}' resolved via known table: {domain}")
        return domain

    # 2. Check with common suffixes stripped
    for suffix in ("labs", "protocol", "finance", "network", "dao", "tech"):
        if slug.endswith(f"-{suffix}"):
            base = slug[:-(len(suffix) + 1)]
            if base in KNOWN_DOMAINS:
                domain = KNOWN_DOMAINS[base]
                logger.info(f"Domain for '{company_name}' resolved via suffix strip: {domain}")
                return domain

    # 3. If a probe function is provided, try TLD variations
    if probe_fn:
        base_name = slug.replace("-", "")  # e.g., "crossovermarkets"
        for tld in CRYPTO_TLDS:
            candidate = f"{base_name}{tld}"
            try:
                if probe_fn(candidate):
                    logger.info(f"Domain for '{company_name}' resolved via TLD probe: {candidate}")
                    return candidate
            except Exception:
                continue

        # Also try with hyphens for multi-word names
        if "-" in slug:
            for tld in CRYPTO_TLDS:
                candidate = f"{slug}{tld}"
                try:
                    if probe_fn(candidate):
                        logger.info(f"Domain for '{company_name}' resolved via TLD probe: {candidate}")
                        return candidate
                except Exception:
                    continue

    # 4. Fall back to .com (but caller MUST validate)
    fallback = f"{slug.replace('-', '')}.com"
    logger.warning(
        f"Domain for '{company_name}' not in known table. "
        f"Falling back to {fallback} — MUST validate after scraping."
    )
    return fallback


def validate_scraped_domain(
    target_company: str,
    scraped_domain: str,
    scraped_description: str,
    deal_context: str = "",
) -> bool:
    """
    Validate that scraped content actually matches the target company.

    This catches the KAST/kast.com class of bugs where the domain belongs
    to a completely different organization.

    Args:
        target_company: Company name we're looking for (e.g., "KAST").
        scraped_domain: Domain that was scraped (e.g., "kast.com").
        scraped_description: Text content scraped from the domain.
        deal_context: Context about the deal (investors, round type, etc.).

    Returns:
        True if the scraped content likely matches the target company.
        False if it appears to be the wrong organization.
    """
    desc_lower = scraped_description.lower()
    company_lower = target_company.lower()

    # Check 1: Does the description contain obvious non-crypto indicators?
    non_crypto_hits = [kw for kw in _NON_CRYPTO_KEYWORDS if kw in desc_lower]
    if non_crypto_hits:
        logger.warning(
            f"Domain validation FAILED for {scraped_domain}: "
            f"description contains non-crypto keywords: {non_crypto_hits}"
        )
        return False

    # Check 2: Does the description mention the company name at all?
    # Allow partial matches for short names
    name_found = company_lower in desc_lower
    if not name_found and len(company_lower) >= 4:
        # Try without common suffixes
        for suffix in (" labs", " protocol", " finance", " tech"):
            base = company_lower.replace(suffix, "").strip()
            if base in desc_lower:
                name_found = True
                break

    # Check 3: For crypto fundraising, does the description contain
    # ANY crypto/fintech keywords?
    crypto_hits = [kw for kw in _CRYPTO_KEYWORDS if kw in desc_lower]

    # Check 4: Does the deal context match the description?
    context_lower = deal_context.lower()
    context_keywords = set(re.findall(r'\b[a-z]{4,}\b', context_lower))
    desc_keywords = set(re.findall(r'\b[a-z]{4,}\b', desc_lower))
    context_overlap = context_keywords & desc_keywords
    context_score = len(context_overlap) / max(len(context_keywords), 1)

    # Decision logic:
    #
    # For crypto fundraising deals (the primary use case), we need POSITIVE
    # evidence that the domain is a crypto/fintech company. Just matching
    # the company name is insufficient — short names like "KAST" (4 chars)
    # can appear in completely unrelated organizations.

    # FAIL: Non-crypto org with no crypto indicators
    if non_crypto_hits and not crypto_hits:
        logger.warning(
            f"Domain validation FAILED for {scraped_domain}: "
            f"non-crypto org ({non_crypto_hits}), no crypto keywords. "
            f"Description: {scraped_description[:100]}..."
        )
        return False

    # FAIL: No crypto keywords at all — suspicious for a crypto fundraising deal
    if not crypto_hits:
        logger.warning(
            f"Domain validation FAILED for {scraped_domain}: "
            f"no crypto/fintech keywords in description. "
            f"For a company raising ${deal_context}, this is suspicious. "
            f"Description: {scraped_description[:100]}..."
        )
        return False

    # PASS: Has crypto keywords — likely the right company
    if name_found or context_score > 0.1:
        logger.info(
            f"Domain validation PASSED for {scraped_domain}: "
            f"crypto keywords: {crypto_hits[:3]}, "
            f"name found: {name_found}, "
            f"context overlap: {context_score:.0%}"
        )
        return True

    # Has crypto keywords but no name match and low context overlap — ambiguous
    logger.warning(
        f"Domain validation AMBIGUOUS for {scraped_domain}: "
        f"has crypto keywords ({crypto_hits[:3]}) but name '{target_company}' "
        f"not found and context overlap {context_score:.0%}. "
        f"Flagging for manual review."
    )
    return False


def build_enrichment_context(
    company: str,
    round_amount: int,
    round_type: str,
    investors: list[str],
) -> str:
    """Build a context string from deal data for domain validation."""
    parts = [company, f"${round_amount:,}", round_type]
    if investors:
        parts.append(", ".join(investors[:5]))
    return " ".join(parts)


def _slugify(text: str) -> str:
    """Simple slugify for domain lookup."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9.\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")
