"""Email in -> claim packet out.

    parse .eml -> regex ids -> resolve load (FreightPro) -> read PODs
      -> [model] classify + extract claimed facts (redacted text only)
      -> deterministic checks -> documents to request / next steps
      -> [model] draft acknowledgement (DRAFT, never sent)

Non-claim emails stop after classification and are routed to their queue: out of scope for
this slice, by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checks import BLOCKING_RESOLUTIONS, Finding, documents_to_request, next_steps, run_checks
from .emailparse import Email, parse_eml
from .freightpro import FreightPro, Resolution
from .identifiers import find_ids
from .llm import ClaimsLLM, Draft, Extraction
from .pod import PodDoc, count_images, is_pod, read_pod
from .redact import Redactor


@dataclass
class Result:
    email: Email
    ids: dict[str, list[str]]
    resolution: Resolution
    pods: list[PodDoc]
    extraction: Extraction | None
    in_scope: bool
    findings: list[Finding] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    draft: Draft | None = None
    error: str | None = None

    @property
    def category(self) -> str:
        return self.extraction.category if self.extraction else "unknown"

    @property
    def blocked(self) -> bool:
        return self.resolution.status in BLOCKING_RESOLUTIONS


class Pipeline:
    def __init__(self, data_dir: str | Path, use_llm: bool = True, client=None):
        self.fp = FreightPro(data_dir)
        names, phones = self.fp.known_driver_pii()
        self.redactor = Redactor(known_names=names, known_orgs=self.fp.shipper_names())
        self.llm = ClaimsLLM(self.redactor, names, phones, client=client) if use_llm else None

    def process(self, path: str | Path, draft: bool = True) -> Result:
        email = parse_eml(path)
        pods = [read_pod(a) for a in email.attachments if is_pod(a)]
        photo_count = sum(count_images(a) for a in email.attachments)

        ids = find_ids(f"{email.subject}\n{email.body}")
        if not ids:  # fall back to numbers printed on a readable POD
            ids = find_ids("\n".join(p.text for p in pods if p.readable))
        resolution = self.fp.resolve(ids)

        extraction = None
        if self.llm:
            extraction = self.llm.extract(
                subject=email.subject, body=email.body,
                date_header=email.date.strftime("%a, %d %b %Y %H:%M %z"), inbox=email.inbox,
            )
            in_scope = extraction.category == "claim" or "claim" in extraction.other_requests
        else:
            in_scope = email.inbox == "claims"  # offline fallback: trust the inbox

        result = Result(email=email, ids=ids, resolution=resolution, pods=pods,
                        extraction=extraction, in_scope=in_scope)
        if not in_scope:
            return result

        result.findings = run_checks(email, resolution, pods, extraction, self.fp, photo_count)
        result.documents = documents_to_request(result.findings, extraction, photo_count)
        result.steps = next_steps(result.findings)

        if self.llm and draft:
            result.draft = self.llm.draft_ack(
                subject=email.subject, body=email.body,
                load_number=resolution.load["LoadNumber"] if resolution.status == "resolved" else None,
                documents=result.documents,
            )
        return result
