"""Headless demo: python demo.py [--seed 42] [--pause]

Runs the full twelve-step autonomous story in the terminal. The command
center (python serve.py) drives the same steps in the browser.
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mfp.demo.director import DemoDirector  # noqa: E402


def rs(paise):
    return f"Rs {Decimal(paise) / 100:,.2f}"


def show(step):
    d = step["data"]
    print(f"\n{step['n']:>2}. {step['title'].upper()}")
    print(f"    {step['narrative']}")
    key = step["key"]
    if key == "zero_complaints":
        print(f"    {d['merchant']} (MCC {d['mcc']}, {d['acquirer']}) - open complaints: {d['open_complaints']}")
    elif key == "monitor_wakes":
        print(f"    {d['batches_seen']} settlement batches since {d['first_settlement']}; latest {d['latest_batch']}")
    elif key == "backfill":
        print(f"    {d['months']} months scanned, {d['transactions']:,} transactions, {d['lines']:,} settlement lines, "
              f"{d['batches']:,} batches, {d['credits_matched']:,} bank credits matched by UTR")
        print(f"    {d['cases_total']} cases opened -> {d['proven_cases']} proven, {d['escalated']} escalated; "
              f"{rs(d['identified_paise'])} identified across {d['months_affected']} months")
    elif key == "investigation":
        c = d["case"]
        print(f"    {c['case_id']}: {c['pattern']} on {c['instrument']}, {c['month']}, {c['transactions']} transactions")
        print(f"    reasoning ({c.get('reasoner')}): {d['rationale']}")
    elif key == "proof":
        p = d["proof"]
        print(f"    {p['verdict']}  expected {rs(p['expected_paise'])}  actual {rs(p['actual_paise'])}  "
              f"verified difference {rs(p['discrepancy_paise'])}")
        for s in p["computation"][:3]:
            print(f"      - {s['label']}: {s['expression'][:120]}")
    elif key == "claim":
        cl = d["claim"]
        print(f"    {cl['claim_id']} ref {cl['reference']} via {cl['workflow']} for {rs(cl['amount_paise'])}; "
              f"attachments {', '.join(cl['attachments'])}")
    elif key == "follow_up":
        print(f"    {d['days_advanced']} days -> {d['today']}: {d['new_batches']} new batches, {d['follow_ups']} follow-ups, "
              f"{d['represented']} re-presentations, {d['recoveries']} recoveries; showcase now {d['showcase']['state']}")
    elif key == "recovery":
        m = d["metrics"]
        print(f"    identified {rs(m['identified_paise'])} | recovered {rs(m['recovered_paise'])} | "
              f"future leakage prevented {rs(m['future_leakage_prevented_paise'])} | escalated {m['escalated']} | "
              f"memory-assisted claims {m['memory_assisted_claims']}")
        for rc in d["root_causes"][:4]:
            print(f"      root cause: {rc['cause']} -> {rc['prevention_action']} [{rc['status']}]")
    elif key == "network":
        print(f"    {d['signatures']:,} signatures, k={d['k']}, {d['suppressed_below_k']} patterns suppressed below k")
        for p in d["patterns"][:4]:
            print(f"      {p['merchants_affected']:>3} merchants  {p['pattern']} on {p['instrument']}  "
                  f"{int(p['top_route_share'] * 100)}% via {p['top_route']}  impact >= {rs(p['aggregate_impact_floor_paise'])}")
    elif key == "red_team":
        print(f"    {d['generated']} generated, {d['investigated']} investigated, {d['correctly_rejected']} correctly rejected, "
              f"{d['correctly_escalated']} escalated, {d['controls_claimed']}/{d['controls']} controls claimed, "
              f"FALSE CLAIMS = {d['false_claims']}")
    elif key == "baseline":
        print(f"    {d['lines']:,} lines reconciled: discrepancies {d['proven_cases']}, claims {d['claims_filed']}, "
              f"recovered {rs(d['recovered_paise'])}, escalated {d['escalated']}")
    elif key == "notify":
        print(f"    [{d['channel']} / {d['source']}] {d['text']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pause", action="store_true", help="wait for Enter between steps")
    args = parser.parse_args()
    director = DemoDirector(seed=args.seed)
    while not director.finished:
        show(director.next())
        if args.pause:
            input()
    print("\nThe merchant didn't ask. The agent found it, proved it, acted on it, and followed it through.")


if __name__ == "__main__":
    main()
