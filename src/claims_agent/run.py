"""Run the agent on one claim and show each tool call as it happens.

  python -m claims_agent.run CL-0011
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from claims_agent.agent import DEFAULT_MODEL, run_claim
from claims_agent.evaluate import ROOT, fresh_data_dir


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("claim_id")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default="medium")
    args = p.parse_args()
    run_dir = ROOT / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.claim_id}"
    data = fresh_data_dir(run_dir / "data")
    r = asyncio.run(run_claim(args.claim_id, data, run_dir, model=args.model, effort=args.effort, verbose=True))
    print(json.dumps(r.__dict__, indent=2, default=str))
    print(f"Trace: {run_dir}")


if __name__ == "__main__":
    main()
