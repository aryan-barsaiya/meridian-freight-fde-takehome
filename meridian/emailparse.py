"""Read an RFC 822 .eml file into a plain dataclass.

In production this would be the Microsoft Graph message payload; the shape we need is the same.
"""

from __future__ import annotations

import email
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class Email:
    path: str
    sender: str
    to: str
    date: datetime
    subject: str
    body: str
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def sender_domain(self) -> str:
        return self.sender.rsplit("@", 1)[-1].strip(" >").lower()

    @property
    def inbox(self) -> str:
        return self.to.split("@", 1)[0].strip(" <").lower()


def parse_eml(path: str | Path) -> Email:
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    body_part = msg.get_body(preferencelist=("plain",))
    body = body_part.get_content() if body_part is not None else ""

    attachments = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            Attachment(
                filename=part.get_filename() or "unnamed",
                content_type=part.get_content_type(),
                data=payload,
            )
        )

    return Email(
        path=str(path),
        sender=str(msg.get("From", "")),
        to=str(msg.get("To", "")),
        date=parsedate_to_datetime(msg["Date"]),
        subject=str(msg.get("Subject", "")),
        body=body.strip(),
        attachments=attachments,
    )
