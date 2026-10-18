import pytest

from meridian.freightpro import normalize_name


@pytest.mark.parametrize("ids,expected", [
    ({"load": ["MF-10487"], "bol": ["BOL243910"], "po": ["PO-8300487"]}, "MF-10487"),  # all agree
    ({"po": ["PO-4500032"]}, "MF-10032"),   # 041: PO only
    ({"bol": ["BOL986209"]}, "MF-10034"),   # 042: BOL only
    ({"pro": ["PRO4052834"]}, "MF-10067"),  # 054: PRO only
])
def test_resolves_by_any_reference(fp, ids, expected):
    res = fp.resolve(ids)
    assert res.status == "resolved" and res.load["LoadNumber"] == expected


def test_shared_bol_is_ambiguous_not_guessed(fp):  # 060
    res = fp.resolve({"bol": ["BOL218661"]})
    assert res.status == "ambiguous" and res.candidates == ["MF-10333", "MF-10587"] and res.load is None


def test_footer_id_does_not_break_resolution(fp):  # 058: "ticket MF-99999" in a bot footer
    res = fp.resolve({"load": ["MF-10066", "MF-99999"], "bol": ["BOL236074"]})
    assert res.status == "resolved" and res.load["LoadNumber"] == "MF-10066"
    assert res.unknown_ids == ["MF-99999"]


def test_ids_pointing_at_different_loads_is_a_conflict(fp):
    res = fp.resolve({"load": ["MF-10487"], "bol": ["BOL591465"]})  # BOL belongs to MF-10005
    assert res.status == "conflict" and set(res.candidates) == {"MF-10487", "MF-10005"}


def test_ambiguous_id_plus_unique_id_outside_its_candidates_is_a_conflict(fp):
    res = fp.resolve({"load": ["MF-10487"], "bol": ["BOL218661"]})
    assert res.status == "conflict"


def test_not_found_and_no_identifiers(fp):
    assert fp.resolve({"load": ["MF-99999"]}).status == "not_found"
    assert fp.resolve({}).status == "no_identifiers"


@pytest.mark.parametrize("name", ["JB Hunt", "J.B. Hunt", "JBHT", "J B Hunt Transport", "J.B. Hunt Transport Inc"])
def test_carrier_spellings_map_to_one_mc(fp, name):
    m = fp.match_carrier(name)
    assert m.status == "matched" and m.mc_number == "023401"


def test_knight_swift_is_knight_not_swift(fp):
    # In this data "Knight-Swift" is a Knight row (MC 191142); Swift is MC 129914.
    assert fp.match_carrier("Knight-Swift").mc_number == "191142"
    assert fp.match_carrier("Swift Trans").mc_number == "129914"


def test_unknown_carrier_is_not_forced_to_a_match(fp):
    assert fp.match_carrier("Acme Haulers").status == "unknown"


def test_normalize_keeps_distinguishing_words():
    assert normalize_name("Roehl Tank") != normalize_name("Roehl Transport")
    assert normalize_name("Prime Inc") == normalize_name("Prime Incorporated") == "prime"
