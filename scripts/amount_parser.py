#!/usr/bin/env python3
"""
Amount Parser with LLM fallback.

Two-pass amount extraction:
1. Fast regex parser for standard formats ($31M, $4.25B, etc.)
2. LLM fallback (Claude Haiku) for ambiguous cases — distinguishes
   round amount vs valuation, handles "undisclosed", currency edge cases.

Usage:
    from amount_parser import extract_amount

    result = extract_amount("OKX raised at a $25B valuation in a $200M round")
    # result = {"amount": 200000000, "valuation": 25000000000,
    #           "round_type": "STRATEGIC", "confidence": "high", "source": "llm"}

    result = extract_amount("$31M Series B")
    # result = {"amount": 31000000, "round_type": "SERIES B",
    #           "confidence": "high", "source": "regex"}

Deployment:
    Place at: /home/ubuntu/clawd/scripts/amount_parser.py
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("amount_parser")

# ---------------------------------------------------------------------------
# Regex-based parser (fast path)
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(
    r"\$\s*([\d,.]+)\s*(billion|B|million|M|thousand|K)\b",
    re.IGNORECASE,
)

_ROUND_TYPES = [
    "PRE-SEED", "SEED", "SERIES A", "SERIES B", "SERIES C", "SERIES D",
    "SERIES E", "SERIES F", "STRATEGIC", "BRIDGE", "GROWTH", "EXTENSION",
    "PUBLIC SALE", "TOKEN SALE", "ICO", "IEO", "IDO", "PRIVATE",
]

_ROUND_RE = re.compile(
    r"\b(" + "|".join(re.escape(rt) for rt in _ROUND_TYPES) + r")\b",
    re.IGNORECASE,
)

_MULTIPLIERS = {
    "b": 1_000_000_000, "billion": 1_000_000_000,
    "m": 1_000_000, "million": 1_000_000,
    "k": 1_000, "thousand": 1_000,
}


def _parse_amount_regex(text: str) -> Optional[int]:
    """Extract first dollar amount from text using regex."""
    match = _AMOUNT_RE.search(text)
    if not match:
        # Try bare number with $
        bare = re.search(r"\$([\d,]+(?:\.\d+)?)\b", text)
        if bare:
            try:
                return int(float(bare.group(1).replace(",", "")))
            except ValueError:
                return None
        return None

    num_str = match.group(1).replace(",", "")
    suffix = match.group(2).lower()
    mult = _MULTIPLIERS.get(suffix, 1)

    try:
        return int(float(num_str) * mult)
    except ValueError:
        return None


def _parse_round_type(text: str) -> str:
    """Extract round type from text."""
    match = _ROUND_RE.search(text)
    return match.group(1).upper() if match else "UNKNOWN"


# ---------------------------------------------------------------------------
# LLM-based parser (fallback for ambiguous cases)
# ---------------------------------------------------------------------------

_LLM_PROMPT = """Extract the funding round details from this text.
Distinguish between the ROUND AMOUNT (money raised) and VALUATION (company value).
If the amount is undisclosed, set amount to 0.

Text: {text}

Respond with ONLY valid JSON:
{{"amount": <integer dollars raised, 0 if undisclosed>,
  "valuation": <integer valuation or null>,
  "round_type": "<SEED|SERIES A|SERIES B|...|STRATEGIC|UNKNOWN>",
  "is_undisclosed": <true if amount not stated>}}"""


def _parse_amount_llm(text: str) -> Optional[dict]:
    """Use Claude Haiku to extract amount from ambiguous text."""
    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed, skipping LLM fallback")
        return None

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("No ANTHROPIC_API_KEY, skipping LLM fallback")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": _LLM_PROMPT.format(text=text[:500])}],
        )
        content = response.content[0].text.strip()

        # Extract JSON from response
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            return {
                "amount": int(parsed.get("amount", 0)),
                "valuation": parsed.get("valuation"),
                "round_type": parsed.get("round_type", "UNKNOWN").upper(),
                "is_undisclosed": parsed.get("is_undisclosed", False),
            }
    except Exception as e:
        logger.warning(f"LLM amount parsing failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_amount(text: str, use_llm: bool = True) -> dict:
    """
    Extract funding round amount from text.

    Two-pass approach:
    1. Regex for standard formats
    2. LLM for ambiguous cases (valuation vs round, undisclosed, etc.)

    Args:
        text: Source text containing funding information.
        use_llm: Whether to use LLM fallback for ambiguous cases.

    Returns:
        Dict with keys: amount, round_type, confidence, source,
        and optionally valuation.
    """
    # Pass 1: Regex
    amount = _parse_amount_regex(text)
    round_type = _parse_round_type(text)

    # Check for ambiguity signals that warrant LLM
    needs_llm = False
    if amount is None or amount == 0:
        needs_llm = True
    elif amount >= 10_000_000_000:  # >$10B probably a valuation, not a round
        needs_llm = True
    elif "valuation" in text.lower() and "raise" not in text.lower():
        needs_llm = True
    elif "undisclosed" in text.lower():
        needs_llm = True

    # Pass 2: LLM for ambiguous cases
    if needs_llm and use_llm:
        llm_result = _parse_amount_llm(text)
        if llm_result:
            return {
                "amount": llm_result["amount"],
                "valuation": llm_result.get("valuation"),
                "round_type": llm_result.get("round_type", round_type),
                "confidence": "high" if not llm_result.get("is_undisclosed") else "low",
                "source": "llm",
            }

    # Fallback to regex result
    return {
        "amount": amount or 0,
        "valuation": None,
        "round_type": round_type,
        "confidence": "high" if amount and amount > 0 else "low",
        "source": "regex",
    }


def is_likely_valuation(amount: int, text: str) -> bool:
    """Heuristic check: is this amount more likely a valuation than a round?"""
    if amount >= 10_000_000_000:  # >$10B
        return True
    text_lower = text.lower()
    valuation_signals = ["valued at", "valuation of", "valued at $", "worth $"]
    round_signals = ["raised", "funding round", "series", "seed round", "led by"]
    val_score = sum(1 for s in valuation_signals if s in text_lower)
    round_score = sum(1 for s in round_signals if s in text_lower)
    return val_score > round_score
