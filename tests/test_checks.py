"""Claim checks on the sample claims, with the model's extraction stubbed."""

import pytest

from meridian.llm import ModelError
from meridian.packet import render
from meridian.pipeline import Pipeline

from conftest import DATA, FakeAnthropic, make_extraction


def run(email_id: str, **extraction):
    fake = FakeAnthropic(make_extraction(**extraction))
    pipeline = Pipeline(DATA, client=fake)
    result = pipeline.process(DATA / "emails" / f"{email_id}.eml")
    return result, {f.code for f in result.findings}, pipeline


def test_035_clean_pod_contradicts_damage_claim_and_photos_missing():
    result, codes, _ = run("035", claim_issues=["damage"], claimed_carrier_name="JBHT",
                           claimed_delivery_date="2026-08-06", says_photos_attached=True, disputes_pod=True)
    assert {"POD_CLEAN_VS_CLAIM", "PHOTOS_MISSING", "CARRIER_CONFIRMED"} <= codes
    assert "DATE_MISMATCH" not in codes
    assert "photos of the damaged freight and packaging" in result.documents


@pytest.mark.parametrize("email_id", ["036", "037", "038"])
def test_blank_scans_are_flagged_unreadable(email_id):
    result, codes, _ = run(email_id, claim_issues=["shortage"])
    assert "POD_UNREADABLE" in codes
    assert any("legible" in d for d in result.documents)
    assert any("shipped versus received" in d for d in result.documents)


def test_041_temperature_claim_on_flatbed():
    _, codes, _ = run("041", claim_issues=["late", "temperature"], claimed_equipment="Flatbed",
                      claimed_origin_city="Houston", claimed_dest_city="Los Angeles")
    assert {"TEMP_CLAIM_NON_REEFER", "LATE_UNVERIFIED"} <= codes
    assert "LANE_MISMATCH" not in codes and "EQUIPMENT_MISMATCH" not in codes


def test_042_shipper_on_credit_hold():
    _, codes, _ = run("042", claim_issues=["damage"])
    assert "SHIPPER_CREDIT_HOLD" in codes and "POD_NOT_ATTACHED" in codes


def test_wrong_carrier_named_is_flagged():
    _, codes, _ = run("035", claim_issues=["damage"], claimed_carrier_name="Swift Transportation")
    assert "CARRIER_MISMATCH" in codes


def test_lane_and_commodity_mismatch_are_flagged():
    _, codes, _ = run("039", claim_issues=["refused"], claimed_dest_city="Boise", claimed_commodity="Lumber")
    assert {"LANE_MISMATCH", "COMMODITY_MISMATCH"} <= codes


def test_non_claim_is_routed_not_processed():
    result, codes, pipeline = run("001", category="quote")
    assert not result.in_scope and codes == set() and result.draft is None
    assert "Not a claim" in render(result, pipeline.fp)


def test_offline_run_never_reports_clear_on_a_clean_pod():
    pipeline = Pipeline(DATA, use_llm=False)
    result = pipeline.process(DATA / "emails" / "035.eml")
    assert "POD_CLEAN_UNCHECKED" in {f.code for f in result.findings}
    assert "CLEAR" not in render(result, pipeline.fp)


def test_draft_is_marked_not_sent():
    result, _, pipeline = run("035", claim_issues=["damage"])
    assert "DRAFT acknowledgement: NOT SENT" in render(result, pipeline.fp)


def test_model_refusal_surfaces_as_error():
    fake = FakeAnthropic(stop_reason="refusal")
    with pytest.raises(ModelError):
        Pipeline(DATA, client=fake).process(DATA / "emails" / "035.eml")


def test_request_shape(fp):
    fake = FakeAnthropic(make_extraction(claim_issues=["damage"]))
    Pipeline(DATA, client=fake).process(DATA / "emails" / "035.eml")
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in req and "tool_choice" not in req
