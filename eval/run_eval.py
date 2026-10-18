"""Run the slice over all 60 sample emails and score it against eval/labels.json.

    python eval/run_eval.py              # full run (model calls, needs ANTHROPIC_API_KEY)
    python eval/run_eval.py --offline    # load resolution + PII only, no key needed

Scores:
  1. routing   - is it a claim? (the gate for this slice) + full category accuracy
  2. lookup    - did we land on the right load, or correctly refuse (ambiguous / none)?
  3. findings  - for the 8 claims: expected discrepancies raised, false alarms
  4. PII       - known driver names/phones in anything sent to the model (must be 0)
  5. cost / latency per email, measured
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meridian.emailparse import parse_eml  # noqa: E402
from meridian.llm import require_api_key  # noqa: E402
from meridian.pipeline import Pipeline  # noqa: E402
from meridian.redact import PHONE  # noqa: E402

DATA = ROOT / "data"
LABELS = json.loads((ROOT / "eval" / "labels.json").read_text())["labels"]


def run_one(path: Path, offline: bool, draft: bool) -> dict:
    # One pipeline per email keeps token/latency stats per email and threads independent.
    pipeline = Pipeline(DATA, use_llm=not offline)
    started = time.monotonic()
    try:
        result = pipeline.process(path, draft=draft)
        error = None
    except Exception as exc:  # an eval should report failures, not stop at the first one
        result, error = None, f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started

    llm = pipeline.llm
    names, phones = pipeline.fp.known_driver_pii()
    if llm:
        sent = "\n".join(llm.sent_payloads)
    else:  # offline: score what *would* be sent
        e = parse_eml(path)
        sent = pipeline.redactor.redact(f"Subject: {e.subject}\n\n{e.body}").text
    leaks = sum(n.lower() in sent.lower() for n in names) + sum(p in sent for p in phones) + len(PHONE.findall(sent))

    row = {"id": path.stem, "error": error, "seconds": round(elapsed, 2), "pii_leaks": leaks}
    if llm:
        row.update(calls=llm.stats.calls, input_tokens=llm.stats.input_tokens,
                   output_tokens=llm.stats.output_tokens, cost_usd=llm.stats.cost_usd)
    if result:
        r = result.resolution
        row.update(
            category=result.extraction.category if result.extraction else None,
            other_requests=result.extraction.other_requests if result.extraction else [],
            in_scope=result.in_scope,
            resolution=r.status,
            load=r.load["LoadNumber"] if r.load else (r.candidates or None),
            findings=sorted(f.code for f in result.findings if f.severity in ("warn", "block")),
        )
    return row


def score(rows: list[dict], offline: bool) -> list[str]:
    out = []
    ok_rows = [r for r in rows if not r["error"]]
    errors = [r for r in rows if r["error"]]

    # 2. lookup (deterministic) -------------------------------------------------------
    lookup_ok, lookup_miss = 0, []
    for r in ok_rows:
        want = LABELS[r["id"]]["load"]
        got = r["load"]
        if (sorted(want) if isinstance(want, list) else want) == (sorted(got) if isinstance(got, list) else got):
            lookup_ok += 1
        else:
            lookup_miss.append(f"{r['id']} expected {want} got {got}")

    # 4. PII --------------------------------------------------------------------------
    leaks = sum(r["pii_leaks"] for r in rows)

    if not offline:
        # 1. routing ------------------------------------------------------------------
        tp = sum(1 for r in ok_rows if r["in_scope"] and "claim" in LABELS[r["id"]]["category"])
        fp_ = [r["id"] for r in ok_rows if r["in_scope"] and "claim" not in LABELS[r["id"]]["category"]]
        fn = [r["id"] for r in ok_rows if not r["in_scope"] and "claim" in LABELS[r["id"]]["category"]]
        cat_ok = [r for r in ok_rows if r["category"] in LABELS[r["id"]]["category"]]
        cat_miss = [f"{r['id']} expected {'/'.join(LABELS[r['id']]['category'])} got {r['category']}"
                    for r in ok_rows if r not in cat_ok]
        multi = [r for r in ok_rows if "multi" in LABELS[r["id"]]]
        multi_ok = [r for r in multi if set(LABELS[r["id"]]["multi"]) <= {r["category"], *r["other_requests"]}]

        out += ["## 1. Routing (is it a claim?)",
                f"- Claims caught: {tp}/8 · missed: {fn or 'none'} · non-claims wrongly pulled in: {fp_ or 'none'}",
                f"- Full category accuracy: {len(cat_ok)}/{len(ok_rows)}",
                *[f"  - miss: {m}" for m in cat_miss],
                f"- Two-requests-in-one detected: {len(multi_ok)}/{len(multi)}", ""]

    out += ["## 2. Load lookup (deterministic)",
            f"- Correct load, or correctly refused: {lookup_ok}/{len(ok_rows)}",
            *[f"  - miss: {m}" for m in lookup_miss], ""]

    if not offline:
        # 3. claim findings -----------------------------------------------------------
        out.append("## 3. Claim packets: expected discrepancies")
        tp_f = fn_f = fp_f = 0
        for r in ok_rows:
            label = LABELS[r["id"]]
            if "expect_findings" not in label:
                continue
            want, got = set(label["expect_findings"]), set(r.get("findings") or [])
            hit, missed, extra = want & got, want - got, got - want
            tp_f, fn_f, fp_f = tp_f + len(hit), fn_f + len(missed), fp_f + len(extra)
            mark = "ok" if not missed and not extra else "CHECK"
            out.append(f"- {r['id']} [{mark}] expected {sorted(want) or '-'} · missed {sorted(missed) or '-'}"
                       f" · extra {sorted(extra) or '-'}")
        out += [f"- Expected discrepancies raised: {tp_f}/{tp_f + fn_f} · false alarms: {fp_f}", ""]

    out += ["## 4. PII sent to the model",
            f"- Known driver names / phone numbers found in outbound payloads: **{leaks}** (must be 0)", ""]

    if not offline and ok_rows:
        secs = sorted(r["seconds"] for r in ok_rows)
        claims = [r for r in ok_rows if r["in_scope"]]
        others = [r for r in ok_rows if not r["in_scope"]]
        total = sum(r["cost_usd"] for r in ok_rows)

        def avg(rs, k):
            return statistics.mean(r[k] for r in rs) if rs else 0

        out += ["## 5. Cost and latency (measured, Claude Opus 5.5 list price)",
                f"- Total: ${total:.3f} for {len(ok_rows)} emails · {sum(r['calls'] for r in ok_rows)} model calls",
                f"- Claim email: ${avg(claims, 'cost_usd'):.4f} avg · {avg(claims, 'seconds'):.1f}s avg"
                + (" (classify + draft)" if any(r["calls"] > 1 for r in claims) else " (classify only; drafts off)"),
                f"- Non-claim email (classify only): ${avg(others, 'cost_usd'):.4f} avg · {avg(others, 'seconds'):.1f}s avg",
                f"- Latency p50 {statistics.median(secs):.1f}s · p95 {secs[int(0.95 * (len(secs) - 1))]:.1f}s", ""]

    if errors:
        out += ["## Errors", *[f"- {r['id']}: {r['error']}" for r in errors], ""]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="no model calls: lookup + PII only")
    # Drafts aren't scored here, so by default we don't pay for them.
    ap.add_argument("--with-drafts", action="store_true", help="also generate drafts (adds cost + latency)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if not args.offline:
        require_api_key()

    paths = sorted((DATA / "emails").glob("*.eml"))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(lambda p: run_one(p, args.offline, args.with_drafts), paths))

    mode = "offline (no model)" if args.offline else "full"
    report = [f"# Eval: {len(rows)} emails, {mode}", ""] + score(rows, args.offline)
    text = "\n".join(report)
    print(text)

    out_dir = ROOT / "eval" / "out"
    out_dir.mkdir(exist_ok=True)
    suffix = "offline" if args.offline else "full"
    (out_dir / f"report_{suffix}.md").write_text(text + "\n")
    (out_dir / f"results_{suffix}.json").write_text(json.dumps(rows, indent=1))
    print(f"\n(wrote eval/out/report_{suffix}.md and results_{suffix}.json)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
