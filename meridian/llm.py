"""The only module that talks to the Anthropic API.

Division of labour: the model reads language (what kind of email is this, what does the
shipper claim happened, which carrier do they name); plain Python checks those claims
against FreightPro. The model never sees FreightPro rows with PII and never decides whether
a claim is valid.

Every outbound payload goes through Redactor.redact + assert_no_pii first.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict

from .redact import Redactor, assert_no_pii

MODEL = "claude-opus-5-5"
EXTRACT_EFFORT = "low"  # short classification/extraction; the eval checks whether low is enough
DRAFT_EFFORT = "medium"

Category = Literal["claim", "quote", "status", "tender", "invoice", "other"]
ClaimIssue = Literal["damage", "shortage", "refused", "late", "temperature", "other"]
Equipment = Literal["Van", "Reefer", "Flatbed"]


class Extraction(BaseModel):
    # extra="forbid" -> additionalProperties: false, which structured outputs require.
    model_config = ConfigDict(extra="forbid")

    category: Category
    other_requests: list[Category]
    claim_issues: list[ClaimIssue]
    claimed_origin_city: str | None
    claimed_dest_city: str | None
    claimed_carrier_name: str | None
    claimed_commodity: str | None
    claimed_equipment: Equipment | None
    claimed_pickup_date: str | None
    claimed_delivery_date: str | None
    quantity_affected: str | None
    disputes_pod: bool
    says_photos_attached: bool
    forwarded_or_third_party: bool
    summary: str


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    body: str


EXTRACT_SYSTEM = """You triage inbound email for Meridian Freight, a freight broker. Shippers and carriers email
four shared inboxes (ops@, quotes@, claims@, dispatch@).

Classify the email and extract only what the email itself states. Never infer facts that are not written.

category (the main request):
- claim: damage, shortage, refused freight, late delivery or temperature complaint the sender wants compensated or a claim opened for
- quote: asks for a price/rate
- status: where is a load / ETA / driver at a gate
- tender: hands Meridian a load to cover
- invoice: a carrier billing Meridian
- other: anything else
other_requests: any additional categories the same email also asks for (empty list if none).

For claims fill claim_issues (all that apply). For any category, fill the claimed_* fields only if stated.
Dates: output YYYY-MM-DD. Resolve relative or partial dates ("8/6", "next Tuesday") against the email's
Date header; if the year is missing, assume the same year as the Date header.
claimed_carrier_name: the carrier name exactly as written, or null.
disputes_pod: true only if the sender says the POD, delivery receipt or Meridian's record is wrong.
says_photos_attached: true if the sender says photos of the freight or damage are attached or included,
including inside another attachment ("photos in the attached POD"). A document that is itself a scan or photo
("scanned POD attached (photo of the paper)") is not a damage photo: false.
forwarded_or_third_party: true if the email is forwarded or the sender is not the shipper/carrier itself.
summary: one plain sentence for a coordinator.

Personal data has been replaced with tokens like [PERSON_1] or [PHONE_1]. Keep tokens as-is; do not guess
what they stand for."""

DRAFT_SYSTEM = """You draft a short acknowledgement email from Meridian Freight's claims team to a shipper. A
coordinator will read and edit it before anything is sent; you are not sending it.

Rules:
- Acknowledge receipt and give the Meridian load number only if it was verified.
- Ask for exactly the items listed under "documents to request" (if any), nothing else.
- Do not admit liability, promise payment, quote amounts, give dates or say who is at fault.
- Do not say a claim, file or OS&D report has been opened or filed; say only that the message was received.
- Do not mention internal checks, discrepancies, credit status or other customers.
- Plain text, under 120 words, sign as "Meridian Freight Claims"."""


@dataclass
class CallStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    request_ids: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        # Claude Opus 5.5 list price: $4 / MTok input, $20 / MTok output.
        return self.input_tokens * 4 / 1e6 + self.output_tokens * 20 / 1e6


class ModelError(RuntimeError):
    pass


def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY is not set.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...   then re-run.\n"
            "(Tests and `--no-llm` runs don't need it.)"
        )


class ClaimsLLM:
    def __init__(self, redactor: Redactor, known_names: set[str], known_phones: set[str], client=None):
        self.redactor = redactor
        self.known_names = known_names
        self.known_phones = known_phones
        self.client = client if client is not None else anthropic.Anthropic()
        self.stats = CallStats()
        self.sent_payloads: list[str] = []  # kept so tests and the eval can prove what left

    def extract(self, *, subject: str, body: str, date_header: str, inbox: str) -> Extraction:
        red = self.redactor.redact(f"Subject: {subject}\n\n{body}")
        prompt = f"Inbox: {inbox}@\nDate header: {date_header}\n\n<email>\n{red.text}\n</email>"
        raw = self._call(EXTRACT_SYSTEM, prompt, Extraction, EXTRACT_EFFORT, max_tokens=4000)
        return Extraction.model_validate_json(raw)

    def draft_ack(self, *, subject: str, body: str, load_number: str | None, documents: list[str]) -> Draft:
        red = self.redactor.redact(f"Subject: {subject}\n\n{body}")
        facts = {
            "verified_load_number": load_number,
            "documents_to_request": documents,
        }
        prompt = f"<email>\n{red.text}\n</email>\n\n<facts>\n{json.dumps(facts, indent=2)}\n</facts>"
        raw = self._call(DRAFT_SYSTEM, prompt, Draft, DRAFT_EFFORT, max_tokens=4000)
        return Draft.model_validate_json(raw)

    def _call(self, system: str, prompt: str, schema: type[BaseModel], effort: str, max_tokens: int) -> str:
        assert_no_pii(system + prompt, self.known_names, self.known_phones)
        self.sent_payloads.append(system + "\n" + prompt)

        started = time.monotonic()
        try:
            resp = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema.model_json_schema()},
                },
                # Server-side refusal fallback: if the model declines, the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise ModelError("Anthropic API rejected the key (401). Check ANTHROPIC_API_KEY.") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("Rate limited by the Anthropic API; retry shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError("Could not reach the Anthropic API (network).") from exc

        self.stats.calls += 1
        self.stats.seconds += time.monotonic() - started
        self.stats.input_tokens += resp.usage.input_tokens
        self.stats.output_tokens += resp.usage.output_tokens
        self.stats.request_ids.append(getattr(resp, "_request_id", None) or "")

        if resp.stop_reason == "refusal":
            raise ModelError("model declined the request (refusal)")
        if resp.stop_reason == "max_tokens":
            raise ModelError("model output was truncated (max_tokens)")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ModelError("model returned no text block")
        return text
