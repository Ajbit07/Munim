"""Hidden ground truth.

Written by the generator into an underscore-prefixed directory that the
observed-data store refuses to open, and readable only by mfp.evaluation
(tools/check_firewall.py, firewall 2). The production pipeline never learns
what was planted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum

HIDDEN_DIR = "_hidden"
TRUTH_FILE = "ground_truth.json"


class ExpectedAction(StrEnum):
    CLAIM = "CLAIM"
    DO_NOT_CLAIM = "DO_NOT_CLAIM"
    ESCALATE = "ESCALATE"


class Fault(StrEnum):
    MCC_MISCONFIG = "MCC_MISCONFIG"                  # L1a, config-visible
    CONTRACT_RATE_DRIFT = "CONTRACT_RATE_DRIFT"      # L1b, config-visible
    UPI_SMALL_MDR = "UPI_SMALL_MDR"                  # L2a, behavioural
    UPI_MDR_EARLY = "UPI_MDR_EARLY"                  # L2b, behavioural, acquirer-wide
    PPI_PASSTHROUGH = "PPI_PASSTHROUGH"              # L2e, behavioural, ambiguous
    RUPAY_DEBIT_AS_DEBIT = "RUPAY_DEBIT_AS_DEBIT"    # L2f, behavioural, acquirer-wide


FAULT_SUBTYPE = {
    Fault.MCC_MISCONFIG: ("L1_WRONG_MDR_BAND", "L1a"),
    Fault.CONTRACT_RATE_DRIFT: ("L1_WRONG_MDR_BAND", "L1b"),
    Fault.UPI_SMALL_MDR: ("L2_NIL_MDR_VIOLATION", "L2a"),
    Fault.UPI_MDR_EARLY: ("L2_NIL_MDR_VIOLATION", "L2b"),
    Fault.PPI_PASSTHROUGH: ("L2_NIL_MDR_VIOLATION", "L2e"),
    Fault.RUPAY_DEBIT_AS_DEBIT: ("L2_NIL_MDR_VIOLATION", "L2f"),
}


@dataclass(frozen=True)
class FaultProfile:
    merchant_id: str
    fault: Fault
    active_from: date
    root_cause: str
    params: dict = field(default_factory=dict)
    systemic_scope: str | None = None  # e.g. "acquirer:ACQ-B" when rolled out across merchants


@dataclass(frozen=True)
class PlantedDiscrepancy:
    plant_id: str
    merchant_id: str
    fault: Fault
    discrepancy_type: str
    subtype: str
    expected_action: ExpectedAction
    txn_id: str
    captured_on: date
    amount_paise: int           # overcharge (CLAIM) or disputed amount (ESCALATE)
    charged_paise: int
    correct_paise: int | None   # None where the correct charge is genuinely unknown
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class Lookalike:
    lookalike_id: str
    merchant_id: str
    resembles: str
    expected_action: ExpectedAction
    txn_id: str
    reason: str


@dataclass
class GroundTruth:
    seed: int
    generator_version: str
    leakage: bool
    as_of: date
    window_start: date
    fault_profiles: list[FaultProfile] = field(default_factory=list)
    plants: list[PlantedDiscrepancy] = field(default_factory=list)
    lookalikes: list[Lookalike] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "seed": self.seed,
            "generator_version": self.generator_version,
            "leakage": self.leakage,
            "as_of": self.as_of.isoformat(),
            "window_start": self.window_start.isoformat(),
            "summary": {
                "fault_profiles": len(self.fault_profiles),
                "plants": len(self.plants),
                "plants_by_subtype": _count(p.subtype for p in self.plants),
                "claimable_paise": sum(
                    p.amount_paise for p in self.plants if p.expected_action is ExpectedAction.CLAIM
                ),
                "escalate_paise": sum(
                    p.amount_paise for p in self.plants if p.expected_action is ExpectedAction.ESCALATE
                ),
                "lookalikes": len(self.lookalikes),
            },
            "fault_profiles": [asdict(f) for f in self.fault_profiles],
            "plants": [asdict(p) for p in self.plants],
            "lookalikes": [asdict(item) for item in self.lookalikes],
        }


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return dict(sorted(out.items()))
