import re
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, List, Set, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Violation:
    """A single detected issue in the AI output."""
    code:        str          # Machine-readable. e.g. "BANNED_VERB", "HALLUCINATED_SKILL"
    field:       str          # Where in the output. e.g. "data.experience[0].bullets[2]"
    description: str          # Human-readable. e.g. "Banned verb 'assisted' found."
    severity:    str = "error"  # "error" = reject | "warning" = flag but pass


# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

BANNED_VERBS = [
    "helped", "help", "helping",
    "assisted", "assist", "assisting",
    "participated", "participate", "participating",
    "contributed", "contribute", "contributing",
    "supported", "support", "supporting",
    "worked on", "was involved in", "was responsible for",
    "I ", "We ", "Me ", "My "
]

# Pre-compiled: match banned verb anywhere in the string (word boundary enforced).
# v1.1.0: Removed the start-of-line anchor to catch "I was responsible for" or "He helped..."
_BANNED_VERB_PATTERNS = [
    re.compile(
        r"\b" + re.escape(v).strip() + r"\b",
        re.IGNORECASE | re.MULTILINE,
    )
    for v in BANNED_VERBS
]

# ──────────────────────────────────────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ──────────────────────────────────────────────────────────────────────────────

def run_diff_check(
    output_data: dict,
    input_data:  dict,
    fields_to_check: Optional[list[str]] = None,
) -> list[Violation]:
    """
    THE MOAT — Compare output vs input for hallucinated data.

    Checks that every item in these output collections exists in the input.
    Any output item not traceable to input is a hallucination.
    """
    if fields_to_check is None:
        fields_to_check = ["skills", "languages", "certifications", "tools"]

    violations = []

    # Flatten input values for lookup (case-insensitive)
    input_flat = _flatten_to_strings(input_data)
    input_lower = {s.lower().strip() for s in input_flat}

    for field_name in fields_to_check:
        output_items = output_data.get(field_name, [])
        if not isinstance(output_items, list):
            continue

        for item in output_items:
            item_str = _item_to_string(item).lower().strip()
            if not item_str:
                continue

            # Check if this item (or a close substring) appears anywhere in input
            if not _is_in_input(item_str, input_lower):
                violations.append(Violation(
                    code="HALLUCINATED_DATA",
                    field=f"{field_name}",
                    description=(
                        f"'{_item_to_string(item)}' appears in output but cannot be "
                        f"traced to input data. Possible hallucination — removing."
                    ),
                    severity="error",
                ))

    return violations


def run_verb_check(output_data: dict) -> list[Violation]:
    """
    Scan all bullet points and text fields for banned verbs.
    Deterministic — regex-based, not LLM-judged.
    """
    violations = []
    bullets = _extract_all_text(output_data)

    for path, text in bullets:
        for pattern, verb in zip(_BANNED_VERB_PATTERNS, BANNED_VERBS):
            if pattern.search(text):
                violations.append(Violation(
                    code="BANNED_VERB",
                    field=path,
                    description=f"Banned verb '{verb}' found in: \"{text[:80]}...\"",
                    severity="error",
                ))
                break  # One violation per bullet is enough

    return violations


def run_metric_check(output_data: dict) -> list[Violation]:
    """
    Verify every bullet point without a metric ends with [ADD METRIC].
    A bullet 'has a metric' if it contains a number, percentage, or currency symbol.
    """
    violations = []
    _METRIC_PATTERN    = re.compile(r"\d+[%xX]?|\$|€|£|¥|\d+[kKmMbB]")
    _PLACEHOLDER       = "[ADD METRIC]"

    bullets = _extract_bullet_points(output_data)

    for path, bullet in bullets:
        has_metric      = bool(_METRIC_PATTERN.search(bullet))
        has_placeholder = _PLACEHOLDER in bullet

        if not has_metric and not has_placeholder:
            violations.append(Violation(
                code="MISSING_METRIC_PLACEHOLDER",
                field=path,
                description=(
                    f"Bullet has no metric and no [ADD METRIC] placeholder: "
                    f"\"{bullet[:80]}...\""
                ),
                severity="warning",
            ))

    return violations


# ──────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _flatten_to_strings(obj: Any, depth: int = 0) -> list[str]:
    """Recursively extract all string values from a nested dict/list."""
    if depth > 10:
        return []
    results = []
    if isinstance(obj, str):
        results.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            results.extend(_flatten_to_strings(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_flatten_to_strings(item, depth + 1))
    return results


def _item_to_string(item: Any) -> str:
    """Convert a skill/language/cert item to a string for comparison."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("name", item.get("label", item.get("value", str(item))))
    return str(item)


def _is_in_input(item_lower: str, input_lower: set) -> bool:
    """
    Check if an output item exists in the flattened input.
    Uses substring matching to handle slight reformatting.
    """
    if item_lower in input_lower:
        return True
    # Check if the item is a substring of any input string (handles "Python 3" vs "Python")
    for inp in input_lower:
        if item_lower in inp or inp in item_lower:
            return True
    return False


def _extract_all_text(obj: Any, path: str = "root") -> list:
    """Extract all string values with their JSON path, for verb/spelling checks."""
    results = []
    if isinstance(obj, str) and len(obj) > 3:
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
    Extract strings that look like bullet points (longer than 20 chars, likely
    achievement/responsibility text). Used for metric check.
    """
    all_texts = _extract_all_text(obj, path)
    return [(p, t) for p, t in all_texts if len(t) > 20 and not t.startswith("http")]
