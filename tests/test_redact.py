"""PII must never reach the model. These tests are the evidence for Legal's memo."""

import pytest

from meridian.llm import Draft
from meridian.pipeline import Pipeline
from meridian.redact import EMAIL, PHONE, PIILeakError, Redactor, assert_no_pii

from conftest import DATA, EMAILS, FakeAnthropic


@pytest.fixture(scope="module")
def redactor(fp):
    names, _ = fp.known_driver_pii()
    return Redactor(known_names=names, known_orgs=fp.shipper_names())


def test_end_to_end_no_pii_in_any_model_request_across_all_60_emails(fp):
    """Run every sample email through the real pipeline; inspect what would have been sent."""
    fake = FakeAnthropic()
    pipeline = Pipeline(DATA, client=fake)
    for path in EMAILS:
        pipeline.process(path)

    sent = fake.sent_text()
    names, phones = fp.known_driver_pii()
    assert len(fake.requests) >= len(EMAILS)
    assert not [n for n in names if n.lower() in sent.lower()]
    assert not [p for p in phones if p in sent]
    assert not PHONE.findall(sent)
    assert not EMAIL.findall(sent)


def test_unseen_driver_name_and_phone_formats(redactor):
    text = "Driver: Bob Smithers, cell (312) 555-0199. Backup driver is Ana Ruiz at 312.555.0142 or 555-0100."
    out = redactor.redact(text).text
    for leaked in ["Bob Smithers", "Ana Ruiz", "(312) 555-0199", "312.555.0142", "555-0100"]:
        assert leaked not in out


def test_receiver_and_cue_names(redactor):
    out = redactor.redact("Receiver: D. Alvarez signed. Last note was Hannah Chen. Ask for Tom Baker.").text
    assert "Alvarez" not in out and "Hannah Chen" not in out and "Tom Baker" not in out


def test_same_person_gets_same_token_everywhere(redactor):
    red = redactor.redact("Driver was Lena Park. Please call Lena Park back.")
    assert red.text.count("[PERSON_1]") == 2 and "Lena Park" not in red.text


def test_signoff_person_redacted_but_team_and_company_kept(redactor):
    assert "Maria Lopez" not in redactor.redact("Thanks,\nMaria Lopez").text
    kept = redactor.redact("Thanks,\nShipping\nTundra Refrigeration").text
    assert "Shipping" in kept and "Tundra Refrigeration" in kept


def test_business_identifiers_survive_redaction(redactor):
    text = "MF-10003 PO-5400037 BOL236074 PRO1234567 pickup 2026-08-18, 42000 lbs, MC 023401, $1,753.00"
    assert redactor.redact(text).text == text


def test_carrier_company_names_are_not_treated_as_people(redactor):
    text = "Carrier showing as Midland Transport. JBHT hauled this."
    assert redactor.redact(text).text == text


def test_guard_refuses_to_send_leftover_pii():
    with pytest.raises(PIILeakError):
        assert_no_pii("call me at 555-292-8110", known_names=[], known_phones=[])
    with pytest.raises(PIILeakError):
        assert_no_pii("driver mei walsh", known_names=["Mei Walsh"], known_phones=[])
    assert_no_pii("Driver [PERSON_1] [PHONE_1]", known_names=["Mei Walsh"], known_phones=["555-292-8110"])


def test_guard_runs_even_if_redaction_is_bypassed(fp):
    """If someone calls the model layer with raw text, the request is never made."""
    fake = FakeAnthropic()
    llm = Pipeline(DATA, client=fake).llm
    with pytest.raises(PIILeakError):
        llm._call("system", "Driver was Jamal Chen 555-512-1986", schema=Draft, effort="low", max_tokens=10)
    assert fake.requests == []
