"""
PATCH: funding-intel-brief.py — Harden dedup and investor parsing
==================================================================
Date: Mar 08, 2026
Author: Audit pipeline

Issues found:
1. Investor list parsing mixes co-founder names with investor names
   (e.g., "Alex Wilson (co-founder" appears as investor for CYCLOPS)
2. Merged pages retain "Archived duplicate:" references in body content
3. REPORT KEY format inconsistency: older pages use date-based keys
   (e.g., fundraising-intel:okx:2026-03-05) while newer pages use
   amount-based keys (fundraising-intel:v3:okx:200000000)

DEPLOYMENT:
  1. SSH to tony
  2. Back up: cp /home/ubuntu/clawd/scripts/funding-intel-brief.py /home/ubuntu/clawd/scripts/funding-intel-brief.py.bak.20260308
  3. Apply the changes described below
"""

# =============================================================================
# CHANGE 1: Fix investor list parsing — filter out person-role artifacts
# =============================================================================
# SEARCH FOR the investor aggregation/parsing function, likely named:
#   def aggregate_investors(...) or def parse_investors(...) or
#   def merge_investor_lists(...)
#
# ADD a sanitization step after aggregation:
#
#   def sanitize_investor_list(investors: list[str]) -> list[str]:
#       """Remove person-name artifacts that leaked into investor list."""
#       # Filter out entries that are clearly person roles, not companies
#       role_indicators = ['co-founder', 'ceo', 'cto', 'cfo', 'founder', 'partner']
#       cleaned = []
#       for inv in investors:
#           inv_lower = inv.strip().lower()
#           # Skip entries that look like "(co-founder" or "CompanyName)"
#           if inv_lower.startswith('(') or inv_lower.endswith(')'):
#               continue
#           # Skip entries containing role indicators with parentheses
#           if any(f'({role}' in inv_lower or f'{role})' in inv_lower for role in role_indicators):
#               continue
#           # Skip entries that are just a person's name (no company indicators)
#           # Heuristic: if it has a parenthetical role, it's a person not an investor
#           if '(' in inv and any(role in inv.lower() for role in role_indicators):
#               continue
#           cleaned.append(inv.strip())
#       return list(dict.fromkeys(cleaned))  # dedupe preserving order

# =============================================================================
# CHANGE 2: Stop embedding "Archived duplicate:" text in canonical body
# =============================================================================
# SEARCH FOR the merge/dedup function that writes to canonical pages.
# Look for patterns like:
#   f"Archived duplicate: {dup_title}"
#   f"Dedup cleanup ({timestamp}): ..."
#
# CHANGE: Move these to QA ISSUES property instead of page body:
#
#   # Instead of appending to page body:
#   # body_blocks.append(f"- Archived duplicate: {dup_title}")
#
#   # Write to QA ISSUES property:
#   qa_notes = page_props.get("QA ISSUES", "") or ""
#   qa_notes += f"\nArchived: {dup_title} ({timestamp})"
#   notion.pages.update(page_id=canonical_id, properties={
#       "QA ISSUES": {"rich_text": [{"text": {"content": qa_notes.strip()}}]}
#   })

# =============================================================================
# CHANGE 3: Normalize REPORT KEY format for all pages
# =============================================================================
# SEARCH FOR the report key generation logic. Ensure ALL pages use v3 format:
#
#   def generate_report_key(company: str, amount: int) -> str:
#       slug = slugify(company)  # lowercase, hyphens
#       return f"fundraising-intel:v3:{slug}:{amount}"
#
# IMPORTANT: The dedup query must also match legacy key formats:
#
#   def find_existing_canonical(company: str, amount: int):
#       v3_key = generate_report_key(company, amount)
#       # Also check legacy patterns
#       legacy_key_prefix = f"fundraising-intel:{slugify(company)}:"
#
#       results = notion.databases.query(
#           database_id=REPORT_BASE_DB,
#           filter={"or": [
#               {"property": "REPORT KEY", "rich_text": {"equals": v3_key}},
#               {"property": "REPORT KEY", "rich_text": {"starts_with": legacy_key_prefix}},
#           ]}
#       )
#       return results

# =============================================================================
# CHANGE 4: Ensure amount-tolerance matching in dedup
# =============================================================================
# When matching by amount, allow ±5% tolerance for slight variations
# (e.g., $4.2M vs $4.25M for BLUPRYNT):
#
#   def amounts_match(a: int, b: int, tolerance: float = 0.05) -> bool:
#       if a == 0 or b == 0:
#           return a == b
#       return abs(a - b) / max(a, b) <= tolerance
