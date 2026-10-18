"""CLI:  python -m meridian data/emails/035.eml [more.eml ...] [--no-llm] [--no-draft]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .llm import ModelError, require_api_key
from .packet import render
from .pipeline import Pipeline

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meridian", description="Turn a claims email into a coordinator claim packet.")
    ap.add_argument("emails", nargs="+", help=".eml file(s)")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="folder with freightpro/ (default: ./data)")
    ap.add_argument("--no-llm", action="store_true", help="deterministic parts only; no API key needed")
    ap.add_argument("--no-draft", action="store_true", help="skip the acknowledgement draft")
    args = ap.parse_args(argv)

    if not args.no_llm:
        require_api_key()

    pipeline = Pipeline(args.data, use_llm=not args.no_llm)
    for i, path in enumerate(args.emails):
        if i:
            print("\n" + "-" * 80 + "\n")
        try:
            result = pipeline.process(path, draft=not args.no_draft)
        except ModelError as exc:
            print(f"{path}: model call failed: {exc}", file=sys.stderr)
            return 1
        print(render(result, pipeline.fp))

    if pipeline.llm and pipeline.llm.stats.calls:
        s = pipeline.llm.stats
        print(f"\n[{s.calls} model call(s) · {s.input_tokens} in / {s.output_tokens} out tokens · "
              f"~${s.cost_usd:.4f} · {s.seconds:.1f}s]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
