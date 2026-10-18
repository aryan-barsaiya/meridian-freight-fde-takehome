import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian.freightpro import FreightPro
from meridian.llm import Extraction

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EMAILS = sorted((DATA / "emails").glob("*.eml"))


def make_extraction(**overrides) -> Extraction:
    base = dict(
        category="claim", other_requests=[], claim_issues=[], claimed_origin_city=None,
        claimed_dest_city=None, claimed_carrier_name=None, claimed_commodity=None,
        claimed_equipment=None, claimed_pickup_date=None, claimed_delivery_date=None,
        quantity_affected=None, disputes_pod=False, says_photos_attached=False,
        forwarded_or_third_party=False, summary="test",
    )
    base.update(overrides)
    return Extraction(**base)


class FakeAnthropic:
    """Stands in for anthropic.Anthropic(): records every request, returns canned JSON."""

    def __init__(self, extraction: Extraction | None = None, stop_reason: str = "end_turn"):
        self.requests: list[dict] = []
        self.extraction = extraction or make_extraction()
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        schema = kwargs["output_config"]["format"]["schema"]
        if "subject" in schema["properties"]:
            payload = {"subject": "Re: your claim", "body": "We received your claim. Meridian Freight Claims"}
        else:
            payload = self.extraction.model_dump()
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            _request_id="req_fake",
        )

    def sent_text(self) -> str:
        return "\n".join(r["system"] + "\n" + r["messages"][0]["content"] for r in self.requests)


@pytest.fixture(scope="session")
def fp() -> FreightPro:
    return FreightPro(DATA)
