"""
PATCH: hiring_intel_module.py — Ensure marker-based replacement is idempotent
==============================================================================
Date: Mar 08, 2026
Author: Audit pipeline

Issue: hiring_intel_module.py generates blocks that are consumed by
founder-intel-pipeline.py. The blocks are placed between
[[HIRING_INTEL_AUTO_START]] and [[HIRING_INTEL_AUTO_END]] markers.

Verified behavior (CROSSOVER MARKETS canonical page):
- Exactly 1 instance of [[HIRING_INTEL_AUTO_START]]
- Exactly 1 instance of [[HIRING_INTEL_AUTO_END]]
- Content between markers is clean and not stacked

Current risk: If the marker replacement in founder-intel-pipeline.py
uses append-after-end instead of replace-between, reruns would stack.

DEPLOYMENT:
  1. SSH to tony
  2. Back up: cp /home/ubuntu/clawd/scripts/hiring_intel_module.py /home/ubuntu/clawd/scripts/hiring_intel_module.py.bak.20260308
  3. Verify the changes described below are in place
"""

# =============================================================================
# VERIFY 1: Output blocks include markers
# =============================================================================
# The module should wrap its output in markers:
#
#   def generate_hiring_intel_blocks(company, round_info, ...):
#       blocks = []
#       blocks.append(text_block("[[HIRING_INTEL_AUTO_START]]"))
#       blocks.append(heading_block("💼 HIRING INTELLIGENCE ..."))
#       # ... hiring content ...
#       blocks.append(text_block("[[HIRING_INTEL_AUTO_END]]"))
#       return blocks
#
# If markers are NOT included in the output, they must be added.

# =============================================================================
# VERIFY 2: DuckDuckGo error handling — suppress transport errors
# =============================================================================
# EVERY standalone outreach page has this artifact in HIRING SIGNALS:
#   "Search error: HTTPSConnectionPool(host='html.duckduckgo.com', port=443):
#    Max retries exceeded with url: /html/?q=..."
#
# SEARCH FOR the DuckDuckGo hiring signal scraper, likely:
#   def search_hiring_signals(company: str) -> list:
#       try:
#           resp = requests.get(f"https://html.duckduckgo.com/html/?q=...")
#       except Exception as e:
#           return [{"signal": str(e), "confidence": 7}]  # BUG: error as signal
#
# FIX: Do not emit transport errors as hiring signals:
#
#   def search_hiring_signals(company: str) -> list:
#       try:
#           resp = requests.get(
#               f"https://html.duckduckgo.com/html/?q=...",
#               timeout=15
#           )
#           resp.raise_for_status()
#           # ... parse signals ...
#       except (requests.ConnectionError, requests.Timeout) as e:
#           logger.warning(f"DuckDuckGo search failed for {company}: {e}")
#           return []  # Empty list, not error-as-signal
#       except Exception as e:
#           logger.error(f"Unexpected error searching hiring signals for {company}: {e}")
#           return []

# =============================================================================
# VERIFY 3: Competitor benchmark — handle Grok timeout gracefully
# =============================================================================
# CYCLOPS outreach page has:
#   "[Grok error: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out.]"
#
# Ensure Grok errors are caught and produce a clean fallback:
#
#   def generate_competitor_benchmark(company, vertical, round_size):
#       try:
#           benchmark = call_grok_api(...)
#       except (requests.Timeout, requests.ConnectionError) as e:
#           logger.warning(f"Grok API timeout for {company} benchmark: {e}")
#           benchmark = f"Competitor benchmark unavailable — API timeout. Manual review recommended."
#       return benchmark
