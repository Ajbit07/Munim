"""Run the red team through the production system and score it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mfp.redteam.generator import AS_OF, EXPECTATIONS, HIDDEN, write_dataset
from mfp.runtime.system import Runtime


@dataclass
class RedTeamResult:
    scenarios: list[dict[str, Any]] = field(default_factory=list)

    @property
    def generated(self) -> int:
        return len(self.scenarios)

    def count(self, **match) -> int:
        return sum(1 for s in self.scenarios if all(s[k] == v for k, v in match.items()))

    @property
    def false_claims(self) -> int:
        return sum(1 for s in self.scenarios if s["actual"] == "CLAIM" and s["expected"] != "CLAIM")

    @property
    def correct(self) -> int:
        return sum(1 for s in self.scenarios if s["actual"] == s["expected"])

    def summary(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "investigated": sum(1 for s in self.scenarios if s["cases"] > 0),
            "correctly_rejected": self.count(expected="DO_NOT_CLAIM", actual="DO_NOT_CLAIM"),
            "correctly_escalated": self.count(expected="ESCALATE", actual="ESCALATE"),
            "controls_claimed": self.count(expected="CLAIM", actual="CLAIM"),
            "controls": self.count(expected="CLAIM"),
            "false_claims": self.false_claims,
            "correct": self.correct,
            "scenarios": self.scenarios,
        }


def run_redteam(work_dir: Path, *, runtime_kwargs: dict[str, Any] | None = None) -> RedTeamResult:
    root = write_dataset(work_dir)
    expectations = json.loads((root / HIDDEN / EXPECTATIONS).read_text(encoding="utf-8"))
    rt = Runtime(root, start=f"{AS_OF}T18:00:00", **(runtime_kwargs or {}))
    for exp in expectations:
        rt.connect(exp["merchant_id"])
    rt.run_until_quiet(45)

    result = RedTeamResult()
    for exp in expectations:
        cases = rt.cases.all(exp["merchant_id"])
        filed = [c for c in cases if c.claim_id]
        escalated = [c for c in cases if c.state.name == "ESCALATED" and not c.claim_id]
        actual = "CLAIM" if filed else "ESCALATE" if escalated else "DO_NOT_CLAIM"
        reasons = [c.escalation["reason"] for c in escalated if c.escalation] + \
                  [c.proof.unproven_reason for c in cases if c.proof and c.proof.unproven_reason and c not in escalated]
        result.scenarios.append({
            **exp, "actual": actual, "cases": len(cases), "states": sorted(str(c.state) for c in cases),
            "reason": reasons[0] if reasons else None,
        })
    return result
