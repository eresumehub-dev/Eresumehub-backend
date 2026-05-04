"""
EresumeHub — AI Output Validator
Version: 1.1.0
────────────────────────────────────────────────────────────────────────────────
Changelog from v1.0.0:

  FIX 1 — SUBSTRING TRAP in _is_in_input (Critical — Gemini + ChatGPT audit)
    Previous logic: `if item_lower in inp or inp in item_lower`
    Bug: "it" matches inside "Architecture". "Java" matches "Javascript".
    Single-letter inputs like "C" would validate ANY word.
    Fix: Enforce minimum length (>2 chars) before substring matching.
         Use word-boundary regex for all matches ≥ 3 chars.
         Exact match only for 1-2 char tokens (acronyms like "C", "R", "UI").

  FIX 2 — METRIC FALSE POSITIVE on existing numbers
    Previous pattern matched \d+ (any digit sequence), including years in dates.
    A bullet like "Reduced costs by 15%" correctly has a metric, but the model's
    internal check sometimes re-adds [ADD METRIC] due to prompt ambiguity.
    Fix: Pattern now requires % or currency or magnitude suffix to qualify as
         a "real" metric (not just a year or ID number). Validator now also
         skips bullets that are date strings or very short fragments.

  FIX 3 — VERB CHECK false positives on pronouns
    "I", "We", "Me", "My" were in BANNED_VERBS but aren't verbs — they cause
    false positives on legitimate text like company names or acronyms.
    Fix: Pronouns moved to a separate BANNED_PRONOUNS check with stricter
         start-of-sentence anchoring, not word-boundary matching.
────────────────────────────────────────────────────────────────────────────────
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Violation:
    """A single detected issue in the AI output."""
    code:        str          # Machine-readable. e.g. "BANNED_VERB", "HALLUCINATED_SKILL"
    field:       str          # Where in the output. e.g. "experience[0].bullets[2]"
    description: str          # Human-readable. e.g. "Banned verb 'assisted' found."
    severity:    str = "error"  # "error" = reject | "warning" = flag but pass


# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

# Pure action verbs only. Pronouns handled separately below.
BANNED_VERBS = [
    "helped", "help", "helping",
    "assisted", "assist", "assisting",
    "participated", "participate", "participating",
    "contributed", "contribute", "contributing",
    "supported", "support", "supporting",
    "worked on",
    "was involved in",
    "was responsible for",
]

# Pronouns that should never start a bullet point
# Kept separate to use start-of-sentence anchoring (stricter, fewer false positives)
BANNED_PRONOUNS = ["I ", "We ", "Me ", "My "]

# Pre-compiled verb patterns — word boundary, case-insensitive
_BANNED_VERB_PATTERNS = [
    re.compile(r"\b" + re.escape(v).strip() + r"\b", re.IGNORECASE | re.MULTILINE)
    for v in BANNED_VERBS
]

# Pronoun patterns — must appear at the START of a string (after optional whitespace)
# This prevents matching "I" inside words like "Implemented" or company names like "AI Corp"
_BANNED_PRONOUN_PATTERNS = [
    re.compile(r"^\s*" + re.escape(p.strip()) + r"\b", re.IGNORECASE)
    for p in BANNED_PRONOUNS
]

# Metric pattern — requires a meaningful unit suffix, not just any digit.
# Matches: 25%, $1M, €500k, £200, ¥1000, 3x, 15%, 200K users
# Does NOT match bare years like 2019, 2023, or bare IDs like "12345"
_METRIC_PATTERN = re.compile(
    r"(\d+\s*%)"                          # percentages: 25%, 3.5%
    r"|(\$\s*[\d,.]+)"                    # USD: $1M, $500
    r"|(€\s*[\d,.]+)"                     # EUR: €200k
    r"|(£\s*[\d,.]+)"                     # GBP: £500
    r"|(¥\s*[\d,.]+)"                     # JPY: ¥1000
    r"|(\d+\s*[xX])"                      # multipliers: 3x, 10X
    r"|(\d+\s*[kKmMbB]\b)"               # magnitudes: 200K, 1M, 3B
    r"|(increased|reduced|improved|grew|saved|generated)\s+\w*\s*\d+",  # verb+number phrases
    re.IGNORECASE
)

_METRIC_PLACEHOLDER = "[ADD METRIC]"

# Dates we should skip during metric/verb checks (not bullet points)
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}(-\d{2})?$|^present$", re.IGNORECASE)

# Compound word patterns (spelling check)
_COMBINED_WORD_PATTERN = re.compile(
    r"\b(high(?:impact|level|quality|performance|throughput)|"
    r"cross(?:functional|platform|team|department)|"
    r"full(?:stack|time|scale)|"
    r"end(?:to)end|"
    r"real(?:time)|"
    r"data(?:driven))\b",
    re.IGNORECASE
)

# Valid date format: YYYY-MM or "present"
_DATE_FORMAT_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ──────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ──────────────────────────────────────────────────────────────────────────────

def run_diff_check(
    output_data: dict,
    input_data:  dict,
    fields_to_check: Optional[list] = None,
) -> list:
    """
    THE MOAT — Deterministic hallucination detection.

    Compares output collections against flattened input data.
    Any output item not traceable to input = hallucination = rejection.

    v1.1.0: Hardened _is_in_input to prevent substring trap false negatives.
    """
    if fields_to_check is None:
        fields_to_check = ["skills", "languages", "certifications", "tools"]

    violations = []
    input_flat  = _flatten_to_strings(input_data)
    input_lower = {s.lower().strip() for s in input_flat if s.strip()}

    for field_name in fields_to_check:
        output_items = output_data.get(field_name, [])
        if not isinstance(output_items, list):
            continue

        for item in output_items:
            item_str = _item_to_string(item).lower().strip()
            if not item_str:
                continue

            if not _is_in_input(item_str, input_lower):
                violations.append(Violation(
                    code="HALLUCINATED_DATA",
                    field=field_name,
                    description=(
                        f"'{_item_to_string(item)}' appears in output but cannot be "
                        f"traced to input data. Possible hallucination — removing."
                    ),
                    severity="error",
                ))

    return violations


def run_verb_check(output_data: dict) -> list:
    """
    Scan all bullet-point text for banned verbs and banned pronouns.
    Deterministic — regex-based, not LLM-judged.

    v1.1.0: Pronouns now checked with start-of-sentence anchor only,
            preventing false positives on words like "Implemented" or "AI".
    """
    violations = []
    texts = _extract_bullet_points(output_data)

    for path, text in texts:
        # Check banned action verbs (word boundary)
        for pattern, verb in zip(_BANNED_VERB_PATTERNS, BANNED_VERBS):
            if pattern.search(text):
                violations.append(Violation(
                    code="BANNED_VERB",
                    field=path,
                    description=f"Banned verb '{verb}' found in: \"{text[:100]}\"",
                    severity="error",
                ))
                break  # One verb violation per bullet is enough

        # Check banned pronouns (start-of-sentence anchor)
        for pattern, pronoun in zip(_BANNED_PRONOUN_PATTERNS, BANNED_PRONOUNS):
            if pattern.search(text):
                violations.append(Violation(
                    code="FIRST_PERSON_PRONOUN",
                    field=path,
                    description=f"Bullet starts with first-person pronoun '{pronoun.strip()}': \"{text[:100]}\"",
                    severity="error",
                ))
                break

    return violations


def run_metric_check(output_data: dict) -> list:
    """
    Verify every bullet point without a real metric has [ADD METRIC] placeholder.

    v1.1.0: Pattern now requires a unit suffix (%, $, x, K/M/B) to count as a
            real metric. Bare years (2019, 2023) and IDs no longer qualify.
            Bullets shorter than 25 chars are skipped (too short to need a metric).
    """
    violations = []
    bullets = _extract_bullet_points(output_data)

    for path, bullet in bullets:
        # Skip very short strings — not real achievement bullets
        if len(bullet.strip()) < 25:
            continue

        has_metric      = bool(_METRIC_PATTERN.search(bullet))
        has_placeholder = _METRIC_PLACEHOLDER in bullet

        if not has_metric and not has_placeholder:
            violations.append(Violation(
                code="MISSING_METRIC_PLACEHOLDER",
                field=path,
                description=(
                    f"Bullet has no metric and no [ADD METRIC] placeholder: "
                    f"\"{bullet[:100]}\""
                ),
                severity="warning",  # Warning: flags but doesn't block output
            ))

    return violations


def run_date_check(output_data: dict) -> list:
    """Verify all date fields match YYYY-MM format or are the string 'present'."""
    violations = []
    date_fields = {"start_date", "end_date", "date", "graduation_date"}

    def _check(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in date_fields:
                    if v is not None and str(v).lower() != "present":
                        if not _DATE_FORMAT_PATTERN.match(str(v)):
                            violations.append(Violation(
                                code="INVALID_DATE_FORMAT",
                                field=f"{path}.{k}",
                                description=f"Date '{v}' does not match YYYY-MM format.",
                                severity="error",
                            ))
                else:
                    _check(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _check(item, f"{path}[{i}]")

    _check(output_data, "root")
    return violations


def run_spelling_check(output_data: dict) -> list:
    """Detect combined words missing hyphens (e.g. 'highimpact' → 'high-impact')."""
    violations = []
    for path, text in _extract_all_text(output_data):
        m = _COMBINED_WORD_PATTERN.search(text)
        if m:
            violations.append(Violation(
                code="COMBINED_WORD_SPELLING",
                field=path,
                description=f"Possible combined word without hyphen: '{m.group()}' in \"{text[:80]}\"",
                severity="warning",
            ))
    return violations


# ──────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _is_in_input(item_lower: str, input_lower: set) -> bool:
    """
    Check if an output item is traceable to the input data.

    v1.1.0 HARDENED — prevents the substring trap:
      - 1-2 char tokens: exact match only (prevents "C" validating "React")
      - 3+ char tokens: word-boundary regex match (prevents "Java" validating "Javascript",
        and "it" validating "Architecture")
    """
    # Exact match — always valid
    if item_lower in input_lower:
        return True

    for inp in input_lower:
        inp = inp.strip()
        if not inp:
            continue

        # For very short tokens (1-2 chars like "C", "R", "UI"), exact match only
        # Substring matching on these would cause catastrophic false positives
        if len(inp) <= 2 or len(item_lower) <= 2:
            if item_lower == inp:
                return True
            continue

        # For longer tokens, use word-boundary regex to prevent bleed-through
        # "Java" matches "Java 11" ✓
        # "Java" does NOT match "Javascript" ✓
        # "it" does NOT match "Architecture" ✓
        try:
            if re.search(rf"\b{re.escape(inp)}\b", item_lower, re.IGNORECASE):
                return True
            if re.search(rf"\b{re.escape(item_lower)}\b", inp, re.IGNORECASE):
                return True
        except re.error:
            # Fallback for items with special regex characters
            if item_lower == inp:
                return True

    return False


def _flatten_to_strings(obj: Any, depth: int = 0) -> list:
    """Recursively extract all string values from a nested dict/list."""
    if depth > 10:
        return []
    results = []
    if isinstance(obj, str) and obj.strip():
        results.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            results.extend(_flatten_to_strings(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_flatten_to_strings(item, depth + 1))
    return results


def _item_to_string(item: Any) -> str:
    """Convert a skill/language/cert item (str or dict) to a string for comparison."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        # Try common name fields in priority order
        for key in ("name", "label", "language", "skill", "value"):
            if item.get(key):
                return str(item[key])
        return str(item)
    return str(item)


def _extract_all_text(obj: Any, path: str = "root") -> list:
    """Extract all string values with their JSON path."""
    results = []
    if isinstance(obj, str) and len(obj) > 3:
        # Skip date strings — not useful for verb/spelling checks
        if not _DATE_PATTERN.match(obj.strip()):
            results.append((path, obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(_extract_all_text(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results.extend(_extract_all_text(item, f"{path}[{i}]"))
    return results


def _extract_bullet_points(obj: Any, path: str = "root") -> list:
    """
    Extract strings that look like bullet points.
    Filters: length > 20 chars, not a URL, not a date string.
    """
    return [
        (p, t) for p, t in _extract_all_text(obj, path)
        if len(t) > 20
        and not t.startswith("http")
        and not _DATE_PATTERN.match(t.strip())
    ]
