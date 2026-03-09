"""
PATCH: founder-intel-pipeline.py — Add domain validation (F-10 fix)
====================================================================
Date: Mar 09, 2026
Author: Audit pipeline
Bug: Pipeline scrapes WRONG company website because it resolves domains
     via naive {company_name}.com lookup. No validation that scraped
     content matches the target company.

Examples of failures:
  - KAST: scraped kast.com (Kootenay Association for Science & Tech)
          instead of kast.xyz (stablecoin neobank)
  - CYCLOPS: scraped Fortune article, got "Success Billionaire" as CEO
  - ARQ: scraped LatAmList, got "About Harassment" as CEO

Root cause: No post-scrape validation. Pipeline publishes whatever it
scrapes without checking if the content is about the right company.

Fix: Import and use domain_resolver.py for:
  1. Resolving company name → correct domain (known table + TLD probing)
  2. Validating scraped content against target company before publishing

DEPLOYMENT:
  1. SSH to tony
  2. Copy domain_resolver.py to /home/ubuntu/clawd/scripts/
  3. Apply changes below to founder-intel-pipeline.py
  4. Test: python3 founder-intel-pipeline.py --dry-run --company "KAST"
"""

# =============================================================================
# CHANGE 1: Import domain_resolver at the top of founder-intel-pipeline.py
# =============================================================================
# ADD after existing imports:
#
#   from domain_resolver import (
#       resolve_domain,
#       validate_scraped_domain,
#       build_enrichment_context,
#   )

# =============================================================================
# CHANGE 2: Replace naive domain resolution
# =============================================================================
# SEARCH FOR the domain resolution logic. It likely looks like:
#
#   domain = f"{company_name.lower().replace(' ', '')}.com"
#   # or
#   domain = f"{slugify(company_name)}.com"
#   # or
#   domain = search_for_domain(company_name)  # DuckDuckGo search
#
# REPLACE WITH:
#
#   # Build context from deal data for domain validation
#   deal_context = build_enrichment_context(
#       company=company_name,
#       round_amount=round_amount,
#       round_type=round_type,
#       investors=investors,
#   )
#
#   # Resolve domain using known table + TLD probing
#   def _probe_domain(domain: str) -> bool:
#       """Check if a domain exists and returns a real website."""
#       try:
#           resp = requests.head(f"https://{domain}", timeout=5, allow_redirects=True)
#           return resp.status_code < 400
#       except Exception:
#           return False
#
#   domain = resolve_domain(
#       company_name=company_name,
#       context=deal_context,
#       probe_fn=_probe_domain,
#   )

# =============================================================================
# CHANGE 3: Add post-scrape validation BEFORE using scraped content
# =============================================================================
# SEARCH FOR the website scraping function. It likely calls:
#
#   resp = requests.get(f"https://{domain}", ...)
#   description = extract_description(resp.text)
#   ceo = extract_ceo(resp.text)
#
# ADD AFTER scraping, BEFORE using the data:
#
#   # Validate that scraped content matches the target company
#   is_valid = validate_scraped_domain(
#       target_company=company_name,
#       scraped_domain=domain,
#       scraped_description=description,
#       deal_context=deal_context,
#   )
#
#   if not is_valid:
#       logger.error(
#           f"DOMAIN MISMATCH: {domain} does not appear to be {company_name}. "
#           f"Scraped description: {description[:100]}... "
#           f"Skipping POC extraction — manual research required."
#       )
#       # Use fallback values instead of garbage data
#       description = f"{company_name} — company description pending manual research."
#       ceo = None
#       ceo_title = None
#       domain = None  # Clear domain so it's not shown as verified

# =============================================================================
# CHANGE 4: Update POC section to reflect validation status
# =============================================================================
# When domain validation fails, the PRIMARY POC section should say:
#
#   ## 👤 PRIMARY POC
#   - PRIMARY POC NOT IDENTIFIED
#   - Domain validation failed — {domain} appears to be a different organization
#   - Manual research required: search for "{company_name} {round_type} founder CEO"
#
# This is BETTER than publishing wrong data. A human can fix "NOT IDENTIFIED"
# in 30 seconds. Fixing wrong POC data requires discovering the error first.

# =============================================================================
# CHANGE 5: Add domain validation result to QA metadata
# =============================================================================
# Add a field to the RESEARCH NOTES section:
#
#   ## 🔍 RESEARCH NOTES
#   - **Domain validation:** PASSED ✅ / FAILED ❌ / SKIPPED ⚠️
#   - **Resolved domain:** {domain} (source: known_table / tld_probe / fallback)
#   - **Founder identified via:** {source}
#   ...
#
# This makes it visible in the page when domain resolution failed,
# so humans know to manually verify.

# =============================================================================
# TESTING
# =============================================================================
# After deployment, verify with these test cases:
#
# 1. KAST (should resolve to kast.xyz, NOT kast.com):
#    python3 founder-intel-pipeline.py --dry-run --company "KAST"
#    Expected: domain=kast.xyz, POC=Raagulan Pathy
#
# 2. CROSSOVER MARKETS (should resolve to crossovermarkets.com):
#    python3 founder-intel-pipeline.py --dry-run --company "CROSSOVER MARKETS"
#    Expected: domain=crossovermarkets.com
#
# 3. Unknown company (should fallback with validation):
#    python3 founder-intel-pipeline.py --dry-run --company "NEWCORP"
#    Expected: domain=newcorp.com with validation warning
