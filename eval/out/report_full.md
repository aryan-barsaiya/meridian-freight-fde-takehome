# Eval: 60 emails, full

## 1. Routing (is it a claim?)
- Claims caught: 8/8 · missed: none · non-claims wrongly pulled in: none
- Full category accuracy: 60/60
- Two-requests-in-one detected: 3/3

## 2. Load lookup (deterministic)
- Correct load, or correctly refused: 60/60

## 3. Claim packets: expected discrepancies
- 035 [ok] expected ['PHOTOS_MISSING', 'POD_CLEAN_VS_CLAIM'] · missed - · extra -
- 036 [ok] expected ['POD_UNREADABLE'] · missed - · extra -
- 037 [ok] expected ['POD_UNREADABLE'] · missed - · extra -
- 038 [ok] expected ['POD_UNREADABLE'] · missed - · extra -
- 039 [ok] expected - · missed - · extra -
- 040 [ok] expected - · missed - · extra -
- 041 [ok] expected ['TEMP_CLAIM_NON_REEFER'] · missed - · extra -
- 042 [ok] expected ['SHIPPER_CREDIT_HOLD'] · missed - · extra -
- Expected discrepancies raised: 7/7 · false alarms: 0

## 4. PII sent to the model
- Known driver names / phone numbers found in outbound payloads: **0** (must be 0)

## 5. Cost and latency (measured, Claude Opus 5.5 list price)
- Total: $0.787 for 60 emails · 60 model calls
- Claim email: $0.0130 avg · 3.0s avg (classify only; drafts off)
- Non-claim email (classify only): $0.0131 avg · 3.3s avg
- Latency p50 3.3s · p95 4.1s

