"""PII redaction before any text leaves the building.

Legal's memo: no personal data to a third party without a DPA, and none has been reviewed.
The Anthropic API is a third party, so every model call goes through `Redactor.redact` and
then `assert_no_pii` (a second, independent check) right before the request is built.

Layers, in order:
  1. email addresses
  2. phone numbers (with or without area code)
  3. driver names already on file in FreightPro (exact, case-insensitive)
  4. names introduced by a cue word: "Driver was X", "Receiver: X", "Last note was X", ...
  5. a person's name on the sign-off line after "Thanks," / "Regards," ...

Known gaps (written down, not hidden): a bare first name with no cue ("ask Bob"), names
inside attachments (we never send attachments), and non-US phone formats.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w-])(?:\+?1[\s.-]?)?(?:(?:\(\d{3}\)|\d{3})[\s.-]?)?\d{3}[\s.-]\d{4}(?![\w-])")

_NAME = r"(?:[A-Z]\.\s?[A-Z][a-z'’-]+|[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){0,2})"
_CUE = (
    r"(?i:driver(?:'s)?(?:\s+name)?|drv|receiver|received\s+by|signed(?:\s+by)?|"
    r"contact|attn|last\s+note\s+was|ask\s+for|dispatcher)"
)
CUED_NAME = re.compile(rf"\b{_CUE}(?:\s*(?:is|was|:|-|/|=))*\s*(?P<name>{_NAME})")

_CLOSING = re.compile(r"^(?i:thanks|thank\s+you|regards|best(?:\s+regards)?|cheers|sincerely)[,!.]?\s*(?P<name>.*)$")
_SIGNOFF_NAME = re.compile(r"[A-Z][a-z'’-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'’-]+)?")
# Sign-off lines that are teams or companies, not people.
_ROLE_WORDS = {"shipping", "procurement", "team", "dispatch", "billing", "ops", "operations",
               "logistics", "claims", "accounts", "payable", "receiving", "customer", "service"}


class PIILeakError(RuntimeError):
    pass


@dataclass
class Redaction:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # token -> original (stays local)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for token in self.mapping:
            out[token.strip("[]").rsplit("_", 1)[0]] += 1
        return dict(out)


class Redactor:
    def __init__(self, known_names: Iterable[str] = (), known_orgs: Iterable[str] = ()):
        # Longest first so "Mei Walsh" wins over a shorter overlapping name.
        self.known_names = sorted({n for n in known_names if n.strip()}, key=len, reverse=True)
        self.known_orgs = {o.lower() for o in known_orgs}

    def redact(self, text: str) -> Redaction:
        tokens: dict[str, str] = {}  # original -> token
        counters: dict[str, int] = defaultdict(int)

        def token_for(original: str, kind: str) -> str:
            key = original.lower() if kind == "PERSON" else original
            if key not in tokens:
                counters[kind] += 1
                tokens[key] = f"[{kind}_{counters[kind]}]"
            return tokens[key]

        def replace_literal(s: str, original: str, kind: str) -> str:
            pattern = re.compile(rf"(?<!\w){re.escape(original)}(?!\w)", re.I)
            return pattern.sub(lambda m: token_for(original, kind), s)

        out = EMAIL.sub(lambda m: token_for(m.group(0), "EMAIL"), text)
        out = PHONE.sub(lambda m: token_for(m.group(0), "PHONE"), out)

        for name in self.known_names:
            out = replace_literal(out, name, "PERSON")

        # Cue-word names: find them, then replace every occurrence (not just the cued one).
        for name in {m.group("name") for m in CUED_NAME.finditer(out)}:
            out = replace_literal(out, name, "PERSON")

        lines = out.splitlines()
        for i, line in enumerate(lines):
            m = _CLOSING.match(line.strip())
            if not m:
                continue
            candidate = m.group("name").strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
            if self._looks_like_person(candidate):
                out = replace_literal(out, candidate, "PERSON")

        mapping = {tok: orig for orig, tok in tokens.items()}
        return Redaction(text=out, mapping=mapping)

    def _looks_like_person(self, candidate: str) -> bool:
        if not candidate or not _SIGNOFF_NAME.fullmatch(candidate):
            return False
        words = {w.lower().strip(".") for w in candidate.split()}
        return not (words & _ROLE_WORDS) and candidate.lower() not in self.known_orgs


def assert_no_pii(text: str, known_names: Iterable[str], known_phones: Iterable[str]) -> None:
    """Independent last check before a request leaves. Raises instead of sending."""
    lowered = text.lower()
    leaks = [n for n in known_names if n and n.lower() in lowered]
    leaks += [p for p in known_phones if p and p in text]
    leaks += PHONE.findall(text)
    leaks += EMAIL.findall(text)
    if leaks:
        raise PIILeakError(f"refusing to send: {len(leaks)} PII value(s) still present")
