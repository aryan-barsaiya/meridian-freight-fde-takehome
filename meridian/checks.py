"""Deterministic claim checks: the 20 minutes a coordinator spends comparing POD vs load record.

No model calls here. Each check produces a Finding with evidence, so a coordinator can see
*why* the packet says what it says. Severity:
  block - the packet can't be trusted until a human fixes it (wrong / unknown load)
  warn  - a discrepancy a coordinator must look at before deciding the claim
  info  - context worth having on screen
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .emailparse import Email
from .freightpro import FreightPro, Resolution
from .llm import Extraction
from .pod import PodDoc

BLOCKING_RESOLUTIONS = {"not_found", "ambiguous", "conflict", "no_identifiers"}


@dataclass
class Finding:
    severity: str  # block | warn | info
    code: str
    message: str


def run_checks(
    email: Email,
    resolution: Resolution,
    pods: list[PodDoc],
    extraction: Extraction | None,
    fp: FreightPro,
    photo_count: int,
) -> list[Finding]:
    out: list[Finding] = []
    out += _check_resolution(resolution)
    if resolution.status != "resolved":
        return out

    load = resolution.load
    shipper = fp.shipper_for(load) or {}
    carrier = fp.carrier_for(load) or {}

    out += _check_load_and_parties(email, load, shipper, carrier)
    out += _check_pods(load, pods, extraction)
    if extraction is not None:
        out += _check_claimed_facts(load, carrier, extraction, fp, pods, photo_count)
    return out


def _check_resolution(res: Resolution) -> list[Finding]:
    out = []
    if res.status == "no_identifiers":
        out.append(Finding("block", "NO_IDENTIFIERS", "No load, PO, BOL or PRO number in the email or attachments."))
    elif res.status == "not_found":
        out.append(Finding("block", "LOAD_NOT_FOUND", f"None of {', '.join(res.unknown_ids)} match a FreightPro load."))
    elif res.status == "ambiguous":
        out.append(Finding("block", "LOAD_AMBIGUOUS",
                           f"{res.matched_by} matches more than one load: {', '.join(res.candidates)}. Confirm which."))
    elif res.status == "conflict":
        out.append(Finding("block", "ID_CONFLICT",
                           f"Reference numbers point at different loads ({', '.join(res.candidates)})."))
    if res.unknown_ids and res.status == "resolved":
        out.append(Finding("info", "UNKNOWN_ID",
                           f"Ignored {', '.join(res.unknown_ids)}: not in FreightPro (signature/footer or typo?)."))
    return out


def _check_load_and_parties(email: Email, load: dict, shipper: dict, carrier: dict) -> list[Finding]:
    out = []
    if load["Status"] != "Delivered":
        out.append(Finding("warn", "LOAD_NOT_DELIVERED",
                           f"FreightPro status is '{load['Status']}' (as of nightly ETL), not Delivered."))

    shipper_domain = shipper.get("ContactEmail", "").rsplit("@", 1)[-1].lower()
    if shipper_domain and email.sender_domain != shipper_domain:
        out.append(Finding("warn", "SENDER_NOT_SHIPPER",
                           f"Sender {email.sender_domain} is not the shipper of record "
                           f"({shipper.get('Name')}, {shipper_domain}). Confirm they may file."))

    if shipper.get("CreditStatus") == "Hold":
        out.append(Finding("warn", "SHIPPER_CREDIT_HOLD",
                           f"{shipper.get('Name')} is on credit hold. Loop in credit before any settlement."))

    if carrier and carrier.get("Status") != "Active":
        out.append(Finding("info", "CARRIER_STATUS", f"Carrier of record is '{carrier.get('Status')}'."))

    if email.inbox != "claims":
        out.append(Finding("info", "WRONG_INBOX", f"Claim arrived at {email.inbox}@, not claims@."))

    if load["DeliveryDate"]:
        age = (email.date.date() - date.fromisoformat(load["DeliveryDate"])).days
        out.append(Finding("info", "CLAIM_AGE", f"Filed {age} days after scheduled delivery ({load['DeliveryDate']})."))
    return out


def _check_pods(load: dict, pods: list[PodDoc], extraction: Extraction | None) -> list[Finding]:
    out = []
    on_file = load["PODReceived"] == "Y"
    if not pods:
        where = "on file in FreightPro: pull it" if on_file else "not on file in FreightPro either: request it"
        out.append(Finding("info", "POD_NOT_ATTACHED", f"No POD attached; {where}."))
        return out

    for pod in pods:
        if not pod.readable:
            out.append(Finding("warn", "POD_UNREADABLE",
                               f"{pod.filename}: {pod.error}. FreightPro POD on file: {'yes' if on_file else 'no'}."))
            continue

        expected = {"load": load["LoadNumber"], "bol": load["BOLNumber"], "po": load["PONumber"],
                    "delivered": load["DeliveryDate"]}
        for key, want in expected.items():
            got = pod.fields.get(key)
            if got and want and got != want:
                out.append(Finding("warn", "POD_FIELD_MISMATCH", f"{pod.filename}: {key} is {got}, FreightPro has {want}."))

        if not on_file:
            out.append(Finding("info", "POD_NOT_ON_FILE", f"{pod.filename} is readable but FreightPro has no POD on file."))

        issues = set(extraction.claim_issues) if extraction else set()
        if pod.exceptions_noted is False and extraction is None:
            out.append(Finding("warn", "POD_CLEAN_UNCHECKED",
                               f"{pod.filename} says '{pod.fields.get('condition')}'; claimed issues were not "
                               "extracted (offline run), so compare against the email by hand."))
        elif pod.exceptions_noted is False and issues & {"damage", "shortage"}:
            out.append(Finding("warn", "POD_CLEAN_VS_CLAIM",
                               f"{pod.filename} says '{pod.fields.get('condition')}' "
                               f"(signed by receiver) but the shipper claims {', '.join(sorted(issues))}. "
                               "A clean POD weakens recovery from the carrier."))
        elif pod.exceptions_noted:
            out.append(Finding("info", "POD_EXCEPTIONS", f"{pod.filename} notes: '{pod.fields.get('condition')}'."))
    return out


def _check_claimed_facts(load, carrier, ex: Extraction, fp: FreightPro, pods: list[PodDoc], photo_count: int):
    out = []

    for label, claimed, actual in [
        ("origin", ex.claimed_origin_city, load["OriginCity"]),
        ("destination", ex.claimed_dest_city, load["DestCity"]),
    ]:
        if claimed and _norm(claimed) != _norm(actual):
            out.append(Finding("warn", "LANE_MISMATCH", f"Email says {label} {claimed}; load has {actual}."))

    if ex.claimed_equipment and ex.claimed_equipment != load["EquipmentType"]:
        out.append(Finding("warn", "EQUIPMENT_MISMATCH",
                           f"Email says {ex.claimed_equipment}; load is {load['EquipmentType']}."))

    if ex.claimed_commodity and not _loosely_same(ex.claimed_commodity, load["Commodity"]):
        out.append(Finding("warn", "COMMODITY_MISMATCH",
                           f"Email says '{ex.claimed_commodity}'; load commodity is '{load['Commodity']}'."))

    if "refused" in ex.claim_issues and (load["Status"] == "Delivered" or load["PODReceived"] == "Y"):
        out.append(Finding("warn", "REFUSED_BUT_DELIVERED",
                           f"Shipper says the freight was refused, but FreightPro shows '{load['Status']}' with "
                           f"POD on file: {load['PODReceived']}. Check the POD for a refusal notation."))

    if "late" in ex.claim_issues:
        out.append(Finding("info", "LATE_UNVERIFIED",
                           f"Late-delivery claim: FreightPro only has the scheduled date ({load['DeliveryDate']}), "
                           "not the actual arrival time."))

    if "temperature" in ex.claim_issues and load["EquipmentType"] != "Reefer":
        out.append(Finding("warn", "TEMP_CLAIM_NON_REEFER",
                           f"Temperature claim on a {load['EquipmentType']} load (no temperature control)."))

    for label, claimed, actual in [
        ("pickup", ex.claimed_pickup_date, load["PickupDate"]),
        ("delivery", ex.claimed_delivery_date, load["DeliveryDate"]),
    ]:
        if claimed and actual and claimed != actual:
            out.append(Finding("warn", "DATE_MISMATCH", f"Email says {label} {claimed}; FreightPro has {actual}."))

    if ex.claimed_carrier_name:
        match = fp.match_carrier(ex.claimed_carrier_name)
        actual_mc = carrier.get("MCNumber")
        if match.status == "matched" and match.mc_number == actual_mc:
            out.append(Finding("info", "CARRIER_CONFIRMED",
                               f"'{ex.claimed_carrier_name}' = {carrier.get('LegalName')} (MC {actual_mc}), carrier of record."))
        elif match.status == "matched":
            out.append(Finding("warn", "CARRIER_MISMATCH",
                               f"Email names '{ex.claimed_carrier_name}' (MC {match.mc_number}); "
                               f"carrier of record is {carrier.get('LegalName')} (MC {actual_mc})."))
        else:
            out.append(Finding("info", "CARRIER_UNRESOLVED",
                               f"Could not map '{ex.claimed_carrier_name}' to one MC number ({match.status})."))

    if ex.says_photos_attached and photo_count == 0:
        out.append(Finding("warn", "PHOTOS_MISSING", "Email says photos are attached, but no images were found."))
    return out


def documents_to_request(findings: list[Finding], extraction: Extraction | None, photo_count: int) -> list[str]:
    codes = {f.code for f in findings}
    docs = []
    if codes & {"NO_IDENTIFIERS", "LOAD_NOT_FOUND", "LOAD_AMBIGUOUS", "ID_CONFLICT"}:
        docs.append("the Meridian load number (MF-#####) for this shipment")
    if "POD_UNREADABLE" in codes:
        docs.append("a legible copy of the signed delivery receipt (POD)")
    if any(f.code == "POD_NOT_ATTACHED" and "request it" in f.message for f in findings):
        docs.append("the signed delivery receipt (POD), including any exceptions noted by the receiver")
    issues = set(extraction.claim_issues) if extraction else set()
    if "refused" in issues:
        docs.append("the receiver's reason for refusal and where the freight is now")
    if "shortage" in issues:
        docs.append("the piece or pallet count shipped versus received")
    if "damage" in issues and photo_count == 0:
        docs.append("photos of the damaged freight and packaging")
    if issues & {"damage", "shortage", "refused", "temperature"}:
        docs.append("an itemized claim amount with the commercial invoice for the goods")
    return docs


def next_steps(findings: list[Finding]) -> list[str]:
    codes = {f.code for f in findings}
    steps = []
    if codes & {"NO_IDENTIFIERS", "LOAD_NOT_FOUND", "LOAD_AMBIGUOUS", "ID_CONFLICT"}:
        steps.append("Identify the correct load before opening a claim file.")
    if "PHOTOS_MISSING" in codes:
        steps.append("Ask for the photos the shipper says they sent: none arrived with the email.")
    if "POD_CLEAN_VS_CLAIM" in codes:
        photos = "Once the photos arrive, compare them" if "PHOTOS_MISSING" in codes else "Compare the shipper's photos"
        steps.append(f"{photos} with the clean POD; ask the carrier for their delivery notes.")
    if "REFUSED_BUT_DELIVERED" in codes:
        steps.append("Pull the POD on file and check it for a refusal; confirm where the freight is now "
                     "(returned, stored, disposed) and whether storage charges are accruing.")
    if "LATE_UNVERIFIED" in codes:
        steps.append("Check the actual delivery time on the POD before responding to the late-delivery claim.")
    if "POD_UNREADABLE" in codes:
        steps.append("Request a legible POD; do not decide the claim on an unreadable scan.")
    if "TEMP_CLAIM_NON_REEFER" in codes:
        steps.append("Clarify the temperature allegation with the shipper: the load moved on non-refrigerated equipment.")
    if "SENDER_NOT_SHIPPER" in codes:
        steps.append("Confirm the sender is authorised to file for the shipper of record.")
    if "SHIPPER_CREDIT_HOLD" in codes:
        steps.append("Notify credit: shipper is on hold; any settlement may be offset.")
    if codes & {"LANE_MISMATCH", "COMMODITY_MISMATCH", "EQUIPMENT_MISMATCH", "DATE_MISMATCH", "CARRIER_MISMATCH",
                "POD_FIELD_MISMATCH"}:
        steps.append("Resolve the mismatched details above with the shipper before filing with the carrier.")
    if not steps:
        steps.append("No discrepancies found: open the OS&D file and notify the carrier of record.")
    return steps


def _norm(city: str) -> str:
    return city.lower().replace("saint ", "st. ").replace("st ", "st. ").strip(" .,")


def _loosely_same(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return a in b or b in a or bool(set(a.split()) & set(b.split()) - {"and", "of", "the"})
