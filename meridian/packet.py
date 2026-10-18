"""Render a Result as the claim packet a coordinator reads (Markdown, terminal-friendly).

Driver name/phone are deliberately not shown: a claim decision doesn't need them.
"""

from __future__ import annotations

from .pipeline import Result

_ORDER = {"block": 0, "warn": 1, "info": 2}


def render(result: Result, fp) -> str:
    e = result.email
    lines = [f"# {e.path.rsplit('/', 1)[-1]}  ·  {e.subject}",
             f"From {e.sender} → {e.to}  ·  {e.date:%Y-%m-%d %H:%M %z}", ""]

    if not result.in_scope:
        lines.append(f"**Not a claim** (category: {result.category}"
                     + (f"; also asks: {', '.join(result.extraction.other_requests)}" if result.extraction and result.extraction.other_requests else "")
                     + "). Routed to the normal queue; out of scope for this slice.")
        if result.extraction:
            lines.append(f"Summary: {result.extraction.summary}")
        lines.append(f"Load lookup: {_resolution_line(result)}")
        return "\n".join(lines)

    warns = sum(f.severity == "warn" for f in result.findings)
    if result.blocked:
        status = "BLOCKED: load not identified, needs a coordinator"
    elif warns:
        status = f"NEEDS REVIEW: {warns} {'discrepancy' if warns == 1 else 'discrepancies'}"
    elif result.extraction is None:
        status = "PARTIAL: offline run, the shipper's claimed facts were not checked"
    else:
        status = "CLEAR: no discrepancies found"
    lines += [f"**Status: {status}**", ""]
    if result.extraction:
        lines += [f"Summary: {result.extraction.summary}", ""]

    lines += ["## Load", _resolution_line(result)]
    if result.resolution.status == "resolved":
        lines += _load_block(result.resolution.load, fp)
    lines.append("")

    if result.extraction:
        x = result.extraction
        claimed = [
            ("Issues", ", ".join(x.claim_issues) or "-"),
            ("Carrier named", x.claimed_carrier_name),
            ("Lane", " → ".join(c for c in [x.claimed_origin_city, x.claimed_dest_city] if c) or None),
            ("Equipment", x.claimed_equipment),
            ("Commodity", x.claimed_commodity),
            ("Pickup / delivery", " / ".join(d or "?" for d in [x.claimed_pickup_date, x.claimed_delivery_date])
             if (x.claimed_pickup_date or x.claimed_delivery_date) else None),
            ("Quantity affected", x.quantity_affected),
            ("Disputes POD", "yes" if x.disputes_pod else None),
            ("Says photos attached", "yes" if x.says_photos_attached else None),
        ]
        lines.append("## What the shipper claims (model-extracted, verify)")
        lines += [f"- {k}: {v}" for k, v in claimed if v]
        lines.append("")

    lines.append("## POD")
    if not result.pods:
        lines.append("- none attached")
    for pod in result.pods:
        if pod.readable:
            f = pod.fields
            lines.append(f"- {pod.filename}: readable · load {f.get('load', '?')} · delivered {f.get('delivered', '?')}"
                         f" · condition: \"{f.get('condition', '?')}\"")
        else:
            lines.append(f"- {pod.filename}: UNREADABLE ({pod.error})")
    lines.append("")

    lines.append("## Findings")
    for f in sorted(result.findings, key=lambda f: _ORDER[f.severity]):
        lines.append(f"- [{f.severity.upper()}] {f.code}: {f.message}")
    lines.append("")

    lines.append("## Next steps")
    lines += [f"{i}. {s}" for i, s in enumerate(result.steps, 1)]
    lines.append("")

    if result.documents:
        lines.append("## Documents to request")
        lines += [f"- {d}" for d in result.documents]
        lines.append("")

    if result.draft:
        lines += ["## DRAFT acknowledgement: NOT SENT, edit and approve before sending",
                  f"Subject: {result.draft.subject}", "", result.draft.body]
    return "\n".join(lines)


def _resolution_line(result: Result) -> str:
    r = result.resolution
    if r.status == "resolved":
        return f"{r.load['LoadNumber']} (matched by {r.matched_by})"
    if r.status in ("ambiguous", "conflict"):
        return f"{r.status.upper()}: candidates {', '.join(r.candidates)}"
    if r.status == "not_found":
        return f"NOT FOUND: {', '.join(r.unknown_ids)}"
    return "no reference number in the email"


def _load_block(load: dict, fp) -> list[str]:
    shipper = fp.shipper_for(load) or {}
    carrier = fp.carrier_for(load) or {}
    return [
        f"- {load['Status']} · {load['OriginCity']}, {load['OriginState']} → {load['DestCity']}, {load['DestState']}"
        f" · {load['EquipmentType']} · {load['Commodity']} · {int(load['WeightLbs']):,} lbs",
        f"- Pickup {load['PickupDate']} · Delivery {load['DeliveryDate']} · {load['PONumber']} · {load['BOLNumber']}"
        f"{' · ' + load['PRONumber'] if load['PRONumber'] else ''} · POD on file: {load['PODReceived']}",
        f"- Shipper: {shipper.get('Name')} (credit {shipper.get('CreditStatus')})",
        f"- Carrier: {carrier.get('LegalName', '-')} (MC {carrier.get('MCNumber', '-')}, {carrier.get('Status', '-')})",
        f"- Customer rate ${load['CustomerRate']} + FSC ${load['FuelSurcharge']} · Carrier rate ${load['CarrierRate'] or '-'}",
    ]
