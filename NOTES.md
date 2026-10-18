# Notes for reviewers

## Time (~7.5 h)

Reading the brief, all 60 emails, the attachments and CSVs: 1 h · choosing the slice: 0.5 h ·
deterministic core and tests: 2 h · model layer, live runs, prompt fixes: 1 h · eval and
independent cross-check: 1 h · DESIGN, README, notes, video: 2 h.

## How I used AI tools

Claude Code (Opus 5.5) did the first full read of the data, proposed candidate slices and wrote
most of the code under my review. I made the scope calls and checked the outputs. The paid API was
a last resort: prompt problems were found by reading outputs, tests use a fake client, and the eval
skips drafts because it doesn't score them. Building cost about $2 in API calls.

**What the tools got wrong, and what I overrode**

- **Silent skip.** Offline mode printed "CLEAR" on 035 because the clean-POD-vs-damage check never
  ran. Now a packet never says "clear" when a check was skipped.
- **Prompt overcorrection, caught by the eval.**
  - The model counted "Scanned POD (photo of the paper)" as photos of damage, which gave false alarms on 036–038.
  - My first fix then missed 035, where the shipper says "Photos in the attached POD".
  - The final wording separates the two cases.
- **Carrier matching.**
  - The obvious normalisation would merge "Roehl Tank" with "Roehl Transport".
  - Fuzzy matching would map "Knight-Swift" to Swift, but it's filed under Knight.
  - Now only legal suffixes are stripped, and a fuzzy hit on two MC numbers comes back as ambiguous.
- **Unbacked claims.**
  - A code comment called an effort setting "measured" before anything had been measured.
  - Drafts said "we have opened a file", an action nobody took.
  - Both are fixed.
- **My own labels missed one.** Reading a demo of 039, I saw the packet said "CLEAR" on a
  refused-freight claim whose load FreightPro records as Delivered with a POD on file. The labels
  agreed with the system, so the eval couldn't catch it. Added a check, and fixed the labels for 039 and 040.
- **Scope.** The brief says "at most one" stretch. I treat PII redaction as core, because Legal
  requires it before any model call, so the 60-email eval is the one stretch.

**Where the AI is kept out on purpose:**
- Reference numbers come from a regex.
- The load lookup uses fixed rules.
- All checks are plain Python; the model only reads language.

A regex can't make up a load number, and a coordinator can see why each finding fired.

**Independent cross-check.** A fresh agent on a different model (Sonnet) labelled 18 emails from the
raw data, with no access to my labels or code: 12 random plus 6 hard cases.
- It agreed with the labels and the system on all 18, with no false alarms.
- Two misses it raised became fixes: a late-delivery claim can't be verified from FreightPro's
  scheduled dates, and shortage claims should ask for the count shipped versus received.
- I rejected its "Seafood shipper, retail-freight load" flag, because a shipper's name isn't a commodity.

**Limit of the eval.** It scores 60/60 routing, 60/60 lookup, 9/9 discrepancies and 0 false alarms.
But the prompts were tuned on these emails, so treat it as a regression suite, not proof of
accuracy on new email.

## What I'd do next

1. Run the packets in shadow mode on real claims, using coordinators' agree/disagree clicks as a held-out test set.
2. A review UI that can't send a draft without opening the findings, and that flags approvals made in under 10 seconds.
3. Put the PII test in CI, and log outbound payloads for audit.
4. Classify all four inboxes. Then draft "where is my truck" replies, but only from the live replica, never the 14-hour-old copy. A reply must never reveal another shipper's load: 060's BOL matches two.
5. Measure cheaper models and effort levels against the eval before scaling (about $0.013 per email today).
