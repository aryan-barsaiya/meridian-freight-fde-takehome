"""Proof-of-delivery reading.

Born-digital PODs have a text layer we can parse. Scans (photos of paper) don't; the slice
says so explicitly instead of guessing. OCR / vision is a deliberate non-goal for week 1:
the sample scans are unreadable even to a human, and a POD sent to a vision model would
also carry the receiver's signature and name to a third party.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from pypdf import PdfReader

from .emailparse import Attachment

_FIELDS = {
    "load": re.compile(r"\b(MF-\d{5})\b"),
    "bol": re.compile(r"\b(BOL\d{6})\b"),
    "po": re.compile(r"\b(PO-\d{7})\b"),
    "delivered": re.compile(r"Delivered\s+(\d{4}-\d{2}-\d{2})"),
    "condition": re.compile(r"Condition:\s*(.+)"),
    "receiver": re.compile(r"Receiver:\s*(.+)"),
}

_NO_EXCEPTIONS = re.compile(r"\bno exceptions?\b", re.I)
_EXCEPTION = re.compile(r"\b(damaged?|short(age)?|refused|crushed|wet|torn|exceptions?)\b", re.I)
_CLEAN = re.compile(r"\b(clean|in good (order|condition)|seals? intact)\b", re.I)


@dataclass
class PodDoc:
    filename: str
    readable: bool
    text: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def exceptions_noted(self) -> bool | None:
        """True/False if the condition line clearly says so; None if absent or unclear."""
        condition = self.fields.get("condition")
        if condition is None:
            return None
        if _NO_EXCEPTIONS.search(condition):
            return False
        if _EXCEPTION.search(condition):
            return True
        if _CLEAN.search(condition):
            return False
        return None


def is_pod(att: Attachment) -> bool:
    return att.content_type == "application/pdf" or att.filename.lower().endswith(".pdf")


def count_images(att: Attachment) -> int:
    """Photos attached directly, or embedded in a PDF."""
    if att.content_type.startswith("image/"):
        return 1
    if not is_pod(att):
        return 0
    try:
        return sum(len(page.images) for page in PdfReader(io.BytesIO(att.data)).pages)
    except Exception:
        return 0


def read_pod(att: Attachment) -> PodDoc:
    try:
        reader = PdfReader(io.BytesIO(att.data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as exc:  # corrupt PDF: report it, don't crash the packet
        return PodDoc(filename=att.filename, readable=False, error=f"could not parse PDF: {exc}")

    if not text:
        return PodDoc(filename=att.filename, readable=False,
                      error="no text layer (scanned image); needs a human or a legible copy")

    fields = {}
    for name, pattern in _FIELDS.items():
        m = pattern.search(text)
        if m:
            fields[name] = m.group(1).strip()
    return PodDoc(filename=att.filename, readable=True, text=text, fields=fields)
