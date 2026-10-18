"""Regex extraction of the four reference numbers an email can quote.

Deliberately not done by the model: these formats are fixed, and a regex can't invent a number.
"""

from __future__ import annotations

import re

PATTERNS = {
    "load": re.compile(r"\bMF-\d{5}\b"),
    "po": re.compile(r"\bPO-\d{7}\b"),
    "bol": re.compile(r"\bBOL\d{6}\b"),
    "pro": re.compile(r"\bPRO\d{7}\b"),
}


def find_ids(text: str) -> dict[str, list[str]]:
    """Return each id type -> unique matches, in order of first appearance."""
    found: dict[str, list[str]] = {}
    for kind, pattern in PATTERNS.items():
        seen = list(dict.fromkeys(pattern.findall(text)))
        if seen:
            found[kind] = seen
    return found
