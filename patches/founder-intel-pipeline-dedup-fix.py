"""
PATCH: founder-intel-pipeline.py — Stop creating standalone OUTREACH TARGET pages
====================================================================================
Date: Mar 08, 2026
Author: Audit pipeline
Bug: founder-intel-pipeline.py creates standalone TYPE=OUTREACH TARGET pages
     in Report Base for every fundraising event, even though it ALSO appends
     outreach+hiring sections into the canonical FUNDRAISING INTEL page via
     [[OUTREACH_INTEL_AUTO_START]]/[[OUTREACH_INTEL_AUTO_END]] markers.

Root cause: The pipeline has two write paths:
  1. Append-to-canonical via marker replacement (correct, idempotent)
  2. Create-new-page with TYPE=OUTREACH TARGET (redundant, non-idempotent)

Fix: Disable path #2 (standalone page creation) entirely. The canonical page
     already receives the content via marker-based replacement.

DEPLOYMENT:
  1. SSH to tony
  2. Back up: cp /home/ubuntu/clawd/scripts/founder-intel-pipeline.py /home/ubuntu/clawd/scripts/founder-intel-pipeline.py.bak.20260308
  3. Apply the changes described below
  4. Verify with: python3 /home/ubuntu/clawd/scripts/founder-intel-pipeline.py --dry-run --company "CROSSOVER MARKETS"

CHANGES NEEDED (search for these patterns in founder-intel-pipeline.py):
"""

# =============================================================================
# CHANGE 1: Find the function that creates standalone outreach pages
# =============================================================================
# SEARCH FOR a block similar to:
#
#   def create_outreach_page(...):
#       ...
#       notion.pages.create(
#           parent={"database_id": REPORT_BASE_DB},
#           properties={
#               "ENTRY": {"title": [{"text": {"content": f"..."}}]},
#               "TYPE": {"select": {"name": "OUTREACH TARGET"}},
#               ...
#           },
#           children=outreach_blocks
#       )
#
# OR:
#
#   def write_outreach_report(...):
#       ...
#       # Creates a new page
#
# ACTION: Comment out or guard the entire standalone page creation with:
#
#   if not canonical_page_id:
#       # Only create standalone page if we CANNOT find a canonical to append to
#       logger.warning(f"No canonical page found for {company}, creating standalone outreach page")
#       create_outreach_page(...)
#   else:
#       logger.info(f"Skipping standalone page — content appended to canonical {canonical_page_id}")

# =============================================================================
# CHANGE 2: Ensure marker-based append is the PRIMARY write path
# =============================================================================
# SEARCH FOR a block similar to:
#
#   def append_outreach_to_canonical(page_id, outreach_blocks):
#       # Find [[OUTREACH_INTEL_AUTO_START]] and [[OUTREACH_INTEL_AUTO_END]]
#       # Replace everything between markers
#
# VERIFY this function:
#   a) Searches for existing markers before appending
#   b) REPLACES content between markers (not just appends after end marker)
#   c) Uses full block replacement, not incremental append
#
# If it does incremental append, change to:
#   1. Delete all blocks between START and END markers
#   2. Insert new blocks between markers
#   3. This ensures idempotency — reruns replace, not stack

# =============================================================================
# CHANGE 3: Add REPORT KEY to any standalone pages that DO get created
# =============================================================================
# If standalone pages must be created as fallback, ensure they get a REPORT KEY:
#
#   report_key = f"outreach-intel:{company_slug}:{amount}"
#
# This enables the dedup system to catch them on subsequent runs.
# Currently standalone outreach pages have REPORT KEY="" (empty), which
# bypasses all dedup logic.

# =============================================================================
# CHANGE 4: Add dedup check before creating any page
# =============================================================================
# Before creating any page, query Report Base for existing pages:
#
#   existing = notion.databases.query(
#       database_id=REPORT_BASE_DB,
#       filter={
#           "and": [
#               {"property": "TYPE", "select": {"equals": "OUTREACH TARGET"}},
#               {"property": "ENTRY", "rich_text": {"contains": company_name}}
#           ]
#       }
#   )
#   if existing["results"]:
#       logger.info(f"Outreach page already exists for {company_name}, updating instead of creating")
#       # Update existing page instead of creating new one
