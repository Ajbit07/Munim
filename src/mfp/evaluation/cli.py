"""Full evaluation: leaky dataset vs ground truth, clean baseline, red team.

    python evaluate.py --seed 42 [--merchants all|MER-0001,...]

Writes reports/evaluation-seed-<seed>.json and reports/evaluation-seed-<seed>.md.
docs/MISSES.md is written from the same run.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from mfp.evaluation.harness import evaluate, render_markdown
from mfp.evaluation.redteam import run_redteam
from mfp.runtime.system import Runtime

REPO = Path(__file__).resolve().parents[3]


def run_dataset(root: Path, merchants: list[str] | None, days: int = 60) -> tuple[Runtime, list[str], float]:
    started = time.perf_counter()
    as_of = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["as_of"]
    rt = Runtime(root, start=f"{as_of}T18:00:00")
    ids = merchants or rt.index.merchant_ids("FULL")
    for merchant_id in ids:
        rt.connect(merchant_id)
    rt.run_until_quiet(days)
    return rt, ids, time.perf_counter() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--merchants", default="all")
    parser.add_argument("--data", type=Path, default=REPO / "data" / "generated")
    parser.add_argument("--out", type=Path, default=REPO / "reports")
    args = parser.parse_args(argv)
    merchants = None if args.merchants == "all" else args.merchants.split(",")
    args.out.mkdir(parents=True, exist_ok=True)

    leaky_root = args.data / f"seed-{args.seed}"
    rt, ids, secs = run_dataset(leaky_root, merchants)
    leaky = evaluate(leaky_root, rt.export_results(), ids, rt.data_through)
    leaky_metrics = rt.metrics()
    print(f"leaky dataset: {len(ids)} merchants in {secs:.0f}s; false claims {leaky.false_claim_cases}")

    base_root = args.data / f"seed-{args.seed}-baseline"
    brt, bids, bsecs = run_dataset(base_root, merchants)
    baseline = brt.metrics()
    print(f"baseline: proven {baseline['proven_cases']}, claims {baseline['claims_filed']}, "
          f"recovered {baseline['recovered_paise']}, escalated {baseline['escalated']} ({bsecs:.0f}s)")

    with tempfile.TemporaryDirectory() as tmp:
        redteam = run_redteam(Path(tmp)).summary()
    print(f"red team: {redteam['correct']}/{redteam['generated']} correct, false claims {redteam['false_claims']}")

    patterns, suppressed = rt.network.patterns()
    payload = {
        "seed": args.seed, "leaky": leaky.to_dict(), "leaky_metrics": leaky_metrics,
        "baseline_metrics": baseline, "redteam": redteam,
        "network": {"patterns": [p.summary() for p in patterns], "suppressed_below_k": suppressed},
        "root_causes": [rc.summary() for rc in rt.root_causes()],
    }
    (args.out / f"evaluation-seed-{args.seed}.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    md = render_markdown(leaky, f"Leaky dataset, seed {args.seed}")
    md += (f"\n## Clean baseline, seed {args.seed}\n\n| Metric | Value |\n|---|---|\n"
           f"| Proven discrepancies | {baseline['proven_cases']} |\n| Claims filed | {baseline['claims_filed']} |\n"
           f"| Recovered | ₹{baseline['recovered_paise'] / 100:,.2f} |\n| Escalated | {baseline['escalated']} |\n"
           f"| Cases opened | {baseline['cases_total']} |\n")
    md += ("\n## Red team\n\n| Scenario | Lookalike of | Expected | Actual |\n|---|---|---|---|\n" +
           "".join(f"| {s['scenario']} | {s['lookalike_of']} | {s['expected']} | {s['actual']} |\n" for s in redteam["scenarios"]) +
           f"\n**{redteam['correct']} of {redteam['generated']} correct; false claims {redteam['false_claims']}.**\n")
    (args.out / f"evaluation-seed-{args.seed}.md").write_text(md, encoding="utf-8")
    return 0
