"""Evaluation harness -- the only production-adjacent code allowed to read ground truth.

Scores a finished run against what the generator planted, per planted
component (transaction x MDR/GST/TAX/REFUND_DEBIT/SETTLEMENT):

  observable   could the auditor have seen it by the data horizon at all?
  detected     does any case own this transaction and component?
  proven       did that case's proof authorise a claim?
  claimed      was a claim filed?
  recovered    did money come back?

and, separately, the number that matters most:

  false claims  filed cases covering anything the generator did NOT plant as
                a CLAIM -- a lookalike, an escalate-only charge, or nothing

Every miss is recorded with its stage and reason. Nothing is hidden.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from mfp.data.store import MerchantIndex, ObservedDataset

TRUTH_PATH = ("_hidden", "ground_truth.json")
RECOVERED = {"RECOVERED", "PARTIALLY_RECOVERED"}


@dataclass
class Miss:
    plant_id: str
    merchant_id: str
    discrepancy_type: str
    subtype: str
    amount_paise: int
    stage: str
    reason: str


@dataclass
class EvaluationReport:
    dataset: str
    merchants: list[str]
    data_through: str
    planted: int = 0
    planted_paise: int = 0
    observable: int = 0
    observable_paise: int = 0
    detected: int = 0
    proven: int = 0
    claimed: int = 0
    recovered: int = 0
    correctly_escalated: int = 0
    expected_escalations: int = 0
    proven_paise: int = 0
    recovered_paise: int = 0
    false_claim_cases: int = 0
    false_claim_components: int = 0
    false_claim_paise: int = 0
    lookalikes_total: int = 0
    lookalikes_claimed: int = 0
    exact_amount_cases: int = 0
    amount_checked_cases: int = 0
    amount_delta_paise: int = 0
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)
    misses: list[Miss] = field(default_factory=list)
    unobservable: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["misses"] = [m.__dict__ for m in self.misses]
        d["unobservable"] = dict(self.unobservable)
        d["miss_stages"] = dict(Counter(m.stage for m in self.misses))
        return d


def load_truth(dataset_root: Path) -> dict[str, Any]:
    return json.loads((Path(dataset_root).joinpath(*TRUTH_PATH)).read_text(encoding="utf-8"))


def evaluate(dataset_root: Path | str, results: list[dict[str, Any]], merchants: list[str],
             data_through: date) -> EvaluationReport:
    root = Path(dataset_root)
    truth = load_truth(root)
    index = MerchantIndex(ObservedDataset(root))
    views = {m: index.view(m) for m in merchants}
    report = EvaluationReport(root.name, merchants, data_through.isoformat())

    owner: dict[tuple[str, str], dict[str, Any]] = {}
    for case in results:
        component = case["component"]
        for txn_id in case["txn_ids"]:
            owner[(txn_id, component)] = case

    plants = [p for p in truth["plants"] if p["merchant_id"] in merchants]
    claim_keys = {(p["txn_id"], p["component"]) for p in plants if p["expected_action"] == "CLAIM"}
    plant_amounts: dict[tuple[str, str], int] = {(p["txn_id"], p["component"]): p["amount_paise"] for p in plants}
    by_type: dict[str, Counter] = defaultdict(Counter)

    def settled_lines(view, txn_id):
        return [l for l in view.lines_by_txn.get(txn_id, []) if view.batches_by_id[l.batch_id].settlement_date <= data_through]

    for p in plants:
        view = views[p["merchant_id"]]
        key = (p["txn_id"], p["component"])
        t = by_type[p["discrepancy_type"]]
        report.planted += 1
        report.planted_paise += p["amount_paise"]
        t["planted"] += 1
        lines = settled_lines(view, p["txn_id"])
        observable = (p["component"] == "SETTLEMENT"
                      or (p["component"] == "REFUND_DEBIT" and len(lines) >= 2)
                      or (p["component"] in ("MDR", "GST", "TAX") and len(lines) == 1))
        if not observable:
            report.unobservable[p["subtype"]] += 1
            t["unobservable"] += 1
            continue
        report.observable += 1
        report.observable_paise += p["amount_paise"]
        t["observable"] += 1

        case = owner.get(key)
        if p["expected_action"] == "ESCALATE":
            report.expected_escalations += 1
            if case is not None and case["state"] == "ESCALATED" and not case["filed"]:
                report.correctly_escalated += 1
                t["correctly_escalated"] += 1
            elif case is None:
                report.misses.append(Miss(p["plant_id"], p["merchant_id"], p["discrepancy_type"], p["subtype"],
                                          p["amount_paise"], "DETECTION", "ambiguous charge was not surfaced"))
            continue

        if case is None:
            report.misses.append(Miss(p["plant_id"], p["merchant_id"], p["discrepancy_type"], p["subtype"],
                                      p["amount_paise"], "DETECTION", "no case owns this transaction and component"))
            continue
        report.detected += 1
        t["detected"] += 1
        if case["verdict"] != "PROVEN":
            reason = (case.get("escalation") or {}).get("reason") or f"verdict {case['verdict']}"
            report.misses.append(Miss(p["plant_id"], p["merchant_id"], p["discrepancy_type"], p["subtype"],
                                      p["amount_paise"], "PROOF", reason))
            continue
        report.proven += 1
        t["proven"] += 1
        if not case["filed"]:
            report.misses.append(Miss(p["plant_id"], p["merchant_id"], p["discrepancy_type"], p["subtype"],
                                      p["amount_paise"], "ACTION", f"proven but not filed (state {case['state']})"))
            continue
        report.claimed += 1
        t["claimed"] += 1
        if case["recovered_paise"] > 0:
            report.recovered += 1
            t["recovered"] += 1
        else:
            report.misses.append(Miss(p["plant_id"], p["merchant_id"], p["discrepancy_type"], p["subtype"],
                                      p["amount_paise"], "RECOVERY", f"claim ended {case['state']}"))

    resembles_component = {"L1_WRONG_MDR_BAND": "MDR", "L2_NIL_MDR_VIOLATION": "MDR", "L3_GST_BASE_ERROR": "GST",
                           "L4_TAX_MISAPPLICATION": "TAX", "L5_ORPHAN_REFUND": "REFUND_DEBIT",
                           "L6_UNSETTLED_TRANSACTION": "SETTLEMENT"}
    lookalike_keys = {(l["txn_id"], resembles_component[l["resembles"]])
                      for l in truth["lookalikes"] if l["merchant_id"] in merchants}
    report.lookalikes_total = len(lookalike_keys)
    for case in results:
        if case["merchant_id"] not in merchants:
            continue
        if case["verdict"] == "PROVEN":
            report.proven_paise += case["proven_paise"]
        report.recovered_paise += case["recovered_paise"]
        if not case["filed"]:
            continue
        keys = [(t, case["component"]) for t in case["txn_ids"]]
        bad = [k for k in keys if k not in claim_keys]
        if bad:
            report.false_claim_cases += 1
            report.false_claim_components += len(bad)
            report.false_claim_paise += case["proven_paise"]
        report.lookalikes_claimed += sum(1 for k in keys if k in lookalike_keys)
        planted_total = sum(plant_amounts.get(k, 0) for k in keys)
        report.amount_checked_cases += 1
        report.amount_delta_paise += planted_total - case["proven_paise"]
        if planted_total == case["proven_paise"]:
            report.exact_amount_cases += 1

    report.by_type = {k: dict(v) for k, v in sorted(by_type.items())}
    return report


def render_markdown(report: EvaluationReport, title: str) -> str:
    r = report
    rs = lambda p: f"₹{p / 100:,.2f}"  # noqa: E731
    lines = [
        f"## {title}", "",
        f"Dataset `{r.dataset}`, {len(r.merchants)} merchant(s), observed through {r.data_through}.", "",
        "| Stage | Count |", "|---|---|",
        f"| Planted components | {r.planted:,} ({rs(r.planted_paise)}) |",
        f"| Observable by the data horizon | {r.observable:,} ({rs(r.observable_paise)}) |",
        f"| Detected | {r.detected:,} |",
        f"| Proven | {r.proven:,} |",
        f"| Claimed | {r.claimed:,} |",
        f"| Recovered | {r.recovered:,} |",
        f"| Escalate-only charges correctly escalated | {r.correctly_escalated:,} of {r.expected_escalations:,} |",
        f"| **False claims (cases)** | **{r.false_claim_cases}** |",
        f"| Lookalikes present / claimed | {r.lookalikes_total:,} / {r.lookalikes_claimed} |",
        f"| Filed cases whose proven amount equals planted amount exactly | {r.exact_amount_cases} of {r.amount_checked_cases} |",
        f"| Planted minus proven across filed cases | {rs(r.amount_delta_paise)} |", "",
        "### By discrepancy type", "",
        "| Type | Planted | Unobservable | Observable | Detected | Proven | Claimed | Recovered |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for dtype, c in r.by_type.items():
        lines.append(f"| {dtype} | {c.get('planted', 0)} | {c.get('unobservable', 0)} | {c.get('observable', 0)} | "
                     f"{c.get('detected', 0)} | {c.get('proven', 0)} | {c.get('claimed', 0)} | {c.get('recovered', 0)} |")
    stages = Counter(m.stage for m in r.misses)
    lines += ["", "### Misses by stage", "", "| Stage | Components | Amount | Reasons |", "|---|---|---|---|"]
    for stage, n in stages.most_common():
        amount = sum(m.amount_paise for m in r.misses if m.stage == stage)
        reasons = Counter(m.reason for m in r.misses if m.stage == stage).most_common(3)
        lines.append(f"| {stage} | {n} | {rs(amount)} | " + "; ".join(f"{why[:140]} ({k})" for why, k in reasons) + " |")
    if r.unobservable:
        lines += ["", f"Unobservable by subtype (transactions not yet settled at the data horizon): {dict(r.unobservable)}"]
    return "\n".join(lines) + "\n"
