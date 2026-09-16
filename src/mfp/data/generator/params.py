"""Generation parameters.

Everything here is a SIMULATION parameter -- volumes, mixes, probabilities.
None of it is a financial rule. Rates come from config/fee_rules.json; the
contract-rate ranges below describe how synthetic merchants negotiated, not
what any regulation requires.

Leakage is planted through probability multipliers and dated fault profiles,
never by editing individual rows, so patterns emerge across the dataset the
way they would from a real misconfiguration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from mfp.core.enums import Instrument

DEFAULT_AS_OF = date(2026, 9, 15)
GENERATOR_VERSION = "1.0.0"

ACQUIRERS = ("ACQ-A", "ACQ-B", "ACQ-C")
HERO_ACQUIRER = "ACQ-B"

# A supermarket-like instrument mix. Weights, not probabilities.
INSTRUMENT_MIX: dict[Instrument, int] = {
    Instrument.UPI_P2M_BANK: 60,
    Instrument.UPI_LITE: 3,
    Instrument.RUPAY_CC_ON_UPI: 7,
    Instrument.PPI_ON_UPI: 4,
    Instrument.RUPAY_DEBIT: 5,
    Instrument.CARD_DEBIT: 8,
    Instrument.CARD_CREDIT: 11,
    Instrument.NETBANKING: 2,
}

# (MCC, label, can be an e-commerce participant)
COHORT_MCCS: tuple[tuple[str, str, bool], ...] = (
    ("5411", "Supermart", False),
    ("5499", "General Store", False),
    ("5541", "Fuel Station", False),
    ("8220", "Coaching Centre", False),
    ("4812", "Mobile Store", False),
    ("5812", "Restaurant", False),
    ("5912", "Pharmacy", False),
    ("7230", "Salon", False),
    ("5999", "Online Seller", True),
)

CITIES = ("Mumbai", "Pune", "Thane", "Nagpur", "Nashik", "Ahmedabad", "Bengaluru", "Jaipur")

# Contract-rate negotiation ranges, in basis points (1 bp = 0.01%).
CARD_CREDIT_BPS = (160, 200)
CARD_DEBIT_BPS = (40, 90)
NETBANKING_BPS = (120, 190)


@dataclass(frozen=True)
class FaultProbabilities:
    """Per-merchant probability that each fault is assigned. Zeroed by --no-leakage."""

    acq_b_upi_mdr_early: float = 0.9        # acquirer-wide rollout, L2b
    acq_c_rupay_debit_as_debit: float = 0.7  # acquirer-wide, L2f
    mcc_misconfig: float = 0.2               # L1a
    contract_rate_drift: float = 0.15        # L1b
    upi_small_mdr: float = 0.2               # L2a
    ppi_passthrough: float = 0.25            # L2e, escalate-only
    gst_on_exempt_card: float = 0.15         # L3a
    gst_tax_on_tax: float = 0.1              # L3b
    tax_on_non_eco: float = 0.05             # L4
    duplicate_refund_debit: float = 0.15     # L5
    dropped_from_batch: float = 0.15         # L6

    @classmethod
    def none(cls) -> FaultProbabilities:
        return cls(*([0.0] * 11))


# Acquirer-wide fault start dates. Single dates, so they surface as systemic.
ACQ_B_UPI_MDR_EARLY_FROM = date(2026, 8, 20)
ACQ_C_RUPAY_DEBIT_FROM = date(2026, 4, 1)
RUPAY_CC_TABLE_LOADED_ON = date(2026, 6, 1)


@dataclass(frozen=True)
class HeroScenario:
    """The demo merchant's fault profile. Documented, not hidden: see docs/DATA.md."""

    legal_name: str = "Shree Ganesh Supermart"
    registered_mcc: str = "5411"
    city: str = "Mumbai"
    mcc_misconfig_from: date = RUPAY_CC_TABLE_LOADED_ON
    misconfigured_mcc: str = "5999"
    contract_drift_from: date = date(2026, 2, 10)
    contract_drift_bps: int = 25
    ppi_passthrough_from: date = date(2026, 5, 1)
    duplicate_refund_from: date = date(2026, 3, 1)
    dropped_from_batch_from: date = date(2025, 12, 1)
    dropped_payment_probability: float = 0.0002


@dataclass(frozen=True)
class GenerationParams:
    seed: int = 42
    merchants: int = 250
    months: int = 12
    as_of: date = DEFAULT_AS_OF
    leakage: bool = True
    hero_txn_per_day: int = 120
    cohort_size: int = 24
    cohort_txn_per_day: tuple[int, int] = (25, 60)
    upi_small_mdr_fraction: float = 0.03
    late_settlement_probability: float = 0.003
    failure_probability: float = 0.02
    refund_probability: float = 0.015
    chargeback_probability: float = 0.0005
    noise_credit_probability: float = 0.05
    dropped_payment_probability: float = 0.002
    duplicate_refund_probability: float = 0.02
    out_dir: Path = Path("data/generated")
    hero: HeroScenario = field(default_factory=HeroScenario)

    @property
    def faults(self) -> FaultProbabilities:
        return FaultProbabilities() if self.leakage else FaultProbabilities.none()

    @property
    def full_fidelity_count(self) -> int:
        return min(self.merchants, 1 + self.cohort_size)

    @property
    def window_start(self) -> date:
        year = self.as_of.year
        month = self.as_of.month - self.months
        while month <= 0:
            month += 12
            year -= 1
        day = min(self.as_of.day, 28)
        return date(year, month, day)
