# Meridian Freight: claims intake slice

A damage, shortage or refused-freight email arrives at `claims@`. The system finds the load in
FreightPro, reads the attached POD, checks what the shipper says against the record, and gives
a coordinator a **claim packet**: the discrepancies with evidence, next steps, documents to
request, and a draft acknowledgement marked **NOT SENT**. Nothing is ever sent automatically.

Non-claim emails are classified and routed, then left alone (out of scope for this slice).
Why this slice, and what we'd build next: [`DESIGN.md`](DESIGN.md).

## Quick start (cold clone → demo)

Needs Python 3.10+ and `make`.

```bash
git clone <this-repo> meridian && cd meridian
export ANTHROPIC_API_KEY=sk-ant-...
make demo                     # creates .venv, installs pinned deps, runs email 035
```

Run any sample email:

```bash
make demo EMAIL=data/emails/041.eml
.venv/bin/python -m meridian data/emails/036.eml data/emails/042.eml   # several at once
.venv/bin/python -m meridian data/emails/035.eml --no-llm             # no key, deterministic parts only
```

| Command | What it does | Needs key |
|---|---|---|
| `make demo [EMAIL=…]` | one email end to end | yes |
| `make claims` | all 8 sample claims (035–042) | yes |
| `make test` | 39 unit tests, fake model client | no |
| `make eval` | all 60 emails, scored against `eval/labels.json` (~$0.80) | yes |
| `make eval-offline` | load lookup + PII check on all 60 | no |

If `ANTHROPIC_API_KEY` is missing, the CLI exits with a clear message.

## How it works

```
.eml ─▶ parse ─▶ regex ids (MF / PO / BOL / PRO) ─▶ resolve load in FreightPro ─▶ read POD (text layer)
                                                         │
          redact PII ─▶ [Claude] classify + extract what the shipper claims (structured JSON)
                                                         │
                           deterministic checks (no model) ─▶ claim packet ─▶ [Claude] draft ack (NOT SENT)
```

The split of work is the main design choice. **The model reads language** (what kind of email
this is, which carrier the shipper names, what they say happened). **Plain Python checks facts**:
which load it is, whether the POD contradicts the claim, whether the carrier matches by MC number.
The model never decides whether a claim is valid, and it never sees FreightPro rows.

| Module | Job |
|---|---|
| `meridian/identifiers.py` | regex for load/PO/BOL/PRO numbers (a regex can't invent a number) |
| `meridian/freightpro.py` | resolve ids to one load, or say *ambiguous* / *conflict* / *not found*; free-text carrier → MC number |
| `meridian/pod.py` | parse born-digital PODs; flag scans with no text layer as unreadable |
| `meridian/redact.py` | strip driver names, phones, emails before any model call, plus an independent guard that refuses to send |
| `meridian/llm.py` | the only module that calls the Anthropic API (`claude-opus-5-5`, structured outputs) |
| `meridian/checks.py` | 28 finding types (4 block, 14 warn, 10 info), documents to request, next steps |
| `meridian/packet.py` | render the packet |
| `eval/run_eval.py` | score all 60 emails: routing, lookup, findings, PII, cost/latency |

## What the slice catches in the sample

| Email | Packet says |
|---|---|
| 035 | POD reads "no exceptions noted" but shipper claims damage; email says photos attached but none arrived; "JBHT" = JB Hunt, MC 023401, carrier of record |
| 036–038 | "Scanned" POD has no text layer; don't decide on it, request a legible copy |
| 041 | Only a PO given (load found); temperature claim on a **Flatbed** |
| 042 | Only a BOL given (load found); shipper on **credit hold** |
| 060 (not a claim) | BOL218661 matches **two** loads; refuses to guess |
| 058 (not a claim) | ignores `MF-99999` from a bot footer |

## Evaluation (all 60 emails)

Last full run ([`eval/out/report_full.md`](eval/out/report_full.md)):

| Measure | Result |
|---|---|
| Claims caught / non-claims wrongly pulled in | 8/8 · 0 |
| Category accuracy · two-requests-in-one detected | 60/60 · 3/3 |
| Correct load, or correctly refused (ambiguous / no reference) | 60/60 |
| Expected claim discrepancies raised · false alarms | 7/7 · 0 |
| PII values in outbound model requests | 0 |
| Cost · latency per email (classification) | ~$0.013 · p50 3.3s, p95 4.1s |

**Caveat:** the prompts were tuned on these same 60 emails and the labels in `eval/labels.json`
were written by us, so read this as a regression suite rather than an accuracy estimate for
new email. The next step is a held-out set of real claims.

## PII

Legal's memo forbids sending personal data to a third party without a DPA, and the Anthropic API
is a third party. So every model request passes through `Redactor.redact` and then `assert_no_pii`,
which raises instead of sending. `tests/test_redact.py` runs all 60 emails through the real
pipeline with a recording fake client and asserts that no driver name or phone from FreightPro,
no phone pattern and no email address appears in any request. Attachments are never sent to the
model. Known gaps are listed at the top of `meridian/redact.py`.

## Assumptions

- "Today" is the snapshot date (2026-08-26); relative dates resolve against each email's `Date` header.
- The CSVs stand in for the read-only replica; reading `.eml` files stands in for the Graph API.
- PODs are read from their text layer only. OCR/vision is out of scope until there's a DPA,
  because a POD image carries the receiver's name and signature.
