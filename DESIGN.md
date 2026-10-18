# Meridian Freight ops inbox: the first six weeks

**To:** Dana Whitfield (COO), Marcus Oyelaran (Ops Lead), Priya Natarajan (IT Manager)
**Based on:** the kickoff email, both calls, and the FreightPro snapshot of 2026-08-26 (60 emails, 800 loads)

## Summary

Start with **claims**, not with automating 90% of all email. Claims are 4% of email but about 35%
of coordinator time, and most of that time goes on one repetitive task: comparing the POD against
the load record. We'll build an assistant that does that comparison and hands the coordinator a
one-screen packet listing what doesn't match. A human still decides every claim and sends every
reply. A working version of this runs on your sample data today.

In parallel, the same system **classifies all four inboxes** (60/60 on your sample, though that is the set we tuned on),
which builds the measured accuracy record we need before anything is allowed to reply on its own.

## Where your goals conflict, and what we propose

| Topic | What we heard | What the data shows | Proposal |
|---|---|---|---|
| Automation level | Dana: 90% automated end to end in 6 weeks. Marcus: no AI reply to a shipper without a human reading it. | Every category in the sample has emails that would go wrong if answered automatically. Examples: a quote request from a shipper on credit hold, a BOL that matches two loads, a forward from another broker. | For the first 6 weeks, **AI drafts and humans approve**. Measure *coordinator hours saved*, not *% auto-sent*. Each category earns autonomy separately, once its measured error rate is low enough that Marcus agrees. |
| Data quality | Dana: "the data is clean". Priya: carrier names are free text; 70% of emails cite a load number. | One carrier appears under 5 spellings. "Knight-Swift" is filed under Knight, not Swift. A BOL is shared by two loads. 28 of 120 shippers are on credit hold. | Find loads and carriers with rules, not the model: by MF, then PO, then BOL, then PRO, and by carrier MC number. When a lookup is ambiguous or conflicting, say so instead of guessing. |
| Legal | Dana: "fine if nobody trains on our data". Legal's memo: no PII to a third party without a DPA. | Most dispatch emails contain driver names and phone numbers. | Remove personal data before every model call, with an automated test that proves it. Start the vendor DPA review in week 1. PODs are not sent to the model (they carry signatures). |
| Write access | Dana wants work done end to end. Priya: write access takes 6–8 weeks; IT has one engineer at 20%. | Not needed for this phase. | Read-only replica plus Microsoft Graph. Output goes to a coordinator queue, not into FreightPro. |
| Headcount | Dana: move 8 of 14 coordinators to sales. Marcus: keep the best coordinators on ops. | Claims ≈ 35% of coordinator time ≈ 4.9 coordinators' worth. | If a claim drops from about 20 to about 7 minutes, that frees roughly **3 coordinators' worth** of time. That is a target to measure, not a promise. Redeployment decisions should follow week-6 data, not precede it. |

## What we build in six weeks

**Weeks 1–2: claims packets in shadow mode.**
- Read `claims@` through Graph, and load data from the read replica.
- Each claim email produces a packet:
  - the load (found from any reference number);
  - what the shipper claims;
  - what the POD says;
  - the discrepancies (for example: POD says "no exceptions" but the shipper claims damage; temperature claim on a flatbed; carrier named isn't the carrier of record; shipper on credit hold; unreadable scan);
  - documents to request;
  - a draft acknowledgement.
- Coordinators work claims exactly as today, and compare against the packet. This produces our accuracy numbers without any risk.

**Weeks 3–4: coordinators work claims from the packet.**
- A simple review screen.
- The draft cannot be sent without being opened and edited or approved.
- Every finding gets an agree/disagree click, and that feedback becomes the test set.

**Weeks 3–6: classification on all four inboxes.**
- Each email is tagged as quote, status, tender, invoice or claim, and with the load it refers to.
- Emails asking two things are flagged.
- No replies. This alone saves triage time, and it measures accuracy per category.

**Weeks 5–6: choose the next category, using data.**
- The likely candidate is "where is my truck". It's high volume and low risk, but it needs live status: the reporting copy is 14 hours behind, and an answer from it would often be wrong.
- Quotes come after that, and only once the rule for choosing between competing rates is written down.

## What we will not build yet, and why

- **Automatic sending of any reply.** Marcus's condition, and the right one until we have the error rates to show otherwise.
- **Quote automation.**
  - One sample lane had six valid rates, from $1,485 to $5,189, and no rule for choosing between them.
  - Carrier availability lives in a Google Sheet that is often weeks out of date.
  - A wrong rate confirmation is the most expensive mistake on the table.
- **Writing to FreightPro.** Waits on the change board. Not needed yet.
- **Reading scanned PODs with OCR or vision.** The sample scans are unreadable even to a person. Sending images also sends signatures and names to the vendor, which needs the DPA first.
- **Answering status questions from the 14-hour-old reporting database.** Answers must come from the live replica, or not at all.

## How we will know it worked

| Measure | Baseline | Week-6 target |
|---|---|---|
| Coordinator minutes per claim | ~20 (Marcus; to be timed in week 1) | ≤ 8 |
| Discrepancies the packet missed that a coordinator found later | n/a | 0 on the "must catch" list; every miss is reviewed |
| Coordinator disagreement with packet findings | n/a | < 10%, falling week on week |
| Draft acknowledgements sent unedited vs edited | n/a | tracked; heavy editing means the drafts aren't helping |
| Inbox classification accuracy, weekly labelled sample | sample: 60/60 | ≥ 95% per category before that category is considered for drafting |
| Personal data in anything sent to a vendor | n/a | 0, enforced by an automated test and a send-time guard |
| Model cost | measured: ~$0.013 to classify an email | ~$15/day to classify all of 1,200 emails |

**Marcus's worry, "if I just click send, I stop reading":**
- The packet leads with the discrepancies, not with the draft.
- Every finding needs an agree/disagree click.
- We track approvals made in under 10 seconds as a rubber-stamp warning sign.
- Each week, a supervisor re-checks 5% of approved packets.

**Weather spikes (1,500+ emails a day):**
- Classification runs in parallel at about 3 seconds per email, so volume isn't the risk.
- Stale data is the risk. Every packet shows "as of" timestamps for the data it relied on.

## What we need from each of you

**Dana**
- Agree that phase-1 success means hours saved with human approval, and that redeployment follows the week-6 numbers.
- Sponsor the vendor DPA review so it starts in week 1.

**Marcus**
- One week of timed claims, to set the baseline.
- Two coordinators for about 2 hours a week, to review packets and label emails.
- The claims playbook written down: which documents to request, filing deadlines, when to involve the carrier.
- Later, the rule for choosing between competing rates.

**Priya**
- Read-replica access next week.
- A read-only Graph app registration for `claims@` (all four inboxes by week 3).
- A place to run the service.
- The 20% engineer for access and operations.
- Legal's sign-off path for the DPA.

## Assumptions we made (please correct)

- The four CSV exports match the replica's tables and columns.
- A claim is the shipper's request to be compensated. The packet is internal; the only text meant for the shipper is the draft acknowledgement.
- The model vendor counts as a third party under Legal's memo until a DPA is signed. So redaction applies to every call, including the business details we keep (load numbers, cities, commodities).
- "Now" is the snapshot date. Relative dates in emails are read against each email's date header.
- Credit hold doesn't stop a claim from being processed, but credit should hear about it before any settlement.
