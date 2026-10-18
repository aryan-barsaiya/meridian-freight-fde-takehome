"""Read-only access to the FreightPro snapshot (CSV exports standing in for the SQL Server replica).

Two jobs:
  * resolve the reference numbers in an email to exactly one load, or say why not;
  * map a free-text carrier name to an MC number, the only reliable carrier key.
"""

from __future__ import annotations

import csv
import difflib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Only legal-entity noise is dropped. Words like "Transport" stay, because the data has
# different carriers that differ only by them (e.g. "Roehl Tank" vs "Roehl Transport").
_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "co", "corp", "company", "the"}

# Order matters: a load number is unique, the others "usually" map to one load.
_ID_PRIORITY = [("load", "LoadNumber"), ("po", "PONumber"), ("bol", "BOLNumber"), ("pro", "PRONumber")]


def normalize_name(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " and ")).split()
    return "".join(w for w in words if w not in _LEGAL_SUFFIXES)


@dataclass
class Resolution:
    status: str  # resolved | ambiguous | conflict | not_found | no_identifiers
    load: dict | None = None
    matched_by: str | None = None  # e.g. "BOL BOL986209"
    candidates: list[str] = field(default_factory=list)  # load numbers, when not uniquely resolved
    unknown_ids: list[str] = field(default_factory=list)  # quoted ids that match no load


@dataclass
class CarrierMatch:
    status: str  # matched | ambiguous | unknown
    mc_number: str | None = None
    matched_alias: str | None = None
    candidates: list[str] = field(default_factory=list)


class FreightPro:
    def __init__(self, data_dir: str | Path):
        data_dir = Path(data_dir)
        self.loads = _read(data_dir / "freightpro" / "loads.csv")
        self.carriers = {r["CarrierID"]: r for r in _read(data_dir / "freightpro" / "carriers.csv")}
        self.shippers = {r["ShipperID"]: r for r in _read(data_dir / "freightpro" / "shippers.csv")}

        self._index: dict[str, dict[str, list[dict]]] = {col: defaultdict(list) for _, col in _ID_PRIORITY}
        for load in self.loads:
            for _, col in _ID_PRIORITY:
                if load[col]:
                    self._index[col][load[col]].append(load)

        # alias (normalized legal name or SCAC) -> set of MC numbers
        self._carrier_alias: dict[str, set[str]] = defaultdict(set)
        self._mc_names: dict[str, list[str]] = defaultdict(list)
        for c in self.carriers.values():
            self._carrier_alias[normalize_name(c["LegalName"])].add(c["MCNumber"])
            if c["SCAC"]:
                self._carrier_alias[normalize_name(c["SCAC"])].add(c["MCNumber"])
            self._mc_names[c["MCNumber"]].append(c["LegalName"])

    # ---- lookups -------------------------------------------------------------------------

    def carrier_for(self, load: dict) -> dict | None:
        return self.carriers.get(load["CarrierID"])

    def shipper_for(self, load: dict) -> dict | None:
        return self.shippers.get(load["ShipperID"])

    def carrier_names(self, mc_number: str) -> list[str]:
        return self._mc_names.get(mc_number, [])

    def known_driver_pii(self) -> tuple[set[str], set[str]]:
        """Driver names and phones on file - used by the redaction guard, never sent anywhere."""
        names = {l["DriverName"] for l in self.loads if l["DriverName"]}
        phones = {l["DriverPhone"] for l in self.loads if l["DriverPhone"]}
        return names, phones

    def shipper_names(self) -> set[str]:
        return {s["Name"] for s in self.shippers.values()}

    # ---- resolution ----------------------------------------------------------------------

    def resolve(self, ids: dict[str, list[str]]) -> Resolution:
        """Resolve every quoted id; agree on one load or explain the disagreement."""
        if not ids:
            return Resolution(status="no_identifiers")

        hits: dict[str, set[str]] = {}  # "BOL BOL123456" -> {load numbers}
        unknown: list[str] = []
        for kind, col in _ID_PRIORITY:
            for value in ids.get(kind, []):
                loads = self._index[col].get(value, [])
                if loads:
                    hits[value] = {l["LoadNumber"] for l in loads}
                else:
                    unknown.append(value)

        if not hits:
            return Resolution(status="not_found", unknown_ids=unknown)

        # Ids that point at exactly one load are the strong evidence.
        unique = {value: next(iter(lns)) for value, lns in hits.items() if len(lns) == 1}
        agreed = set(unique.values())

        if len(agreed) > 1:
            return Resolution(
                status="conflict",
                candidates=sorted(agreed),
                unknown_ids=unknown,
                matched_by=", ".join(f"{v} -> {ln}" for v, ln in unique.items()),
            )
        if len(agreed) == 1:
            load_number = agreed.pop()
            # An ambiguous id is fine if it includes the agreed load; otherwise it's a conflict.
            for value, lns in hits.items():
                if load_number not in lns:
                    return Resolution(status="conflict", candidates=sorted(lns | {load_number}), unknown_ids=unknown)
            first = next(v for v, ln in unique.items() if ln == load_number)
            return Resolution(
                status="resolved",
                load=self._index["LoadNumber"][load_number][0],
                matched_by=first,
                unknown_ids=unknown,
            )

        # Only ambiguous ids (e.g. a BOL shared by two loads).
        candidates = sorted(set().union(*hits.values()))
        return Resolution(status="ambiguous", candidates=candidates, unknown_ids=unknown,
                          matched_by=", ".join(hits))

    # ---- carriers ------------------------------------------------------------------------

    def match_carrier(self, name: str) -> CarrierMatch:
        key = normalize_name(name)
        if not key:
            return CarrierMatch(status="unknown")

        exact = self._carrier_alias.get(key)
        if exact:
            if len(exact) == 1:
                return CarrierMatch(status="matched", mc_number=next(iter(exact)), matched_alias=name)
            return CarrierMatch(status="ambiguous", candidates=sorted(exact))

        close = difflib.get_close_matches(key, self._carrier_alias.keys(), n=3, cutoff=0.85)
        mcs = sorted({mc for alias in close for mc in self._carrier_alias[alias]})
        if len(mcs) == 1:
            return CarrierMatch(status="matched", mc_number=mcs[0], matched_alias=close[0])
        if len(mcs) > 1:
            return CarrierMatch(status="ambiguous", candidates=mcs)
        return CarrierMatch(status="unknown")


def _read(path: Path) -> list[dict]:
    # dtype-free on purpose: MC numbers like "023401" must keep their leading zero.
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
