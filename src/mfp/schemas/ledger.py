"""Observed artifacts.

These are the only records the production system may see: what a merchant, a
payment aggregator's settlement report and a bank statement would actually
show. Nothing here reveals what was planted, and nothing here carries a flag
that names a fault. A misbehaving processor is visible only through its
numbers and its rate-card history, as it would be in reality.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from mfp.core.enums import Instrument, MerchantClass


class Fidelity(StrEnum):
    FULL = "FULL"                      # full ledger, settlement and bank history
    SIGNATURE_ONLY = "SIGNATURE_ONLY"  # merchant record only; network tier


class TxnKind(StrEnum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    CHARGEBACK = "CHARGEBACK"
    REVERSAL = "REVERSAL"      # a captured payment reversed by the network after capture


class TxnStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class LineType(StrEnum):
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    CHARGEBACK = "CHARGEBACK"
    REVERSAL = "REVERSAL"
    RENTAL = "RENTAL"          # device rental debited from settlement
    CARRY_FORWARD = "CARRY_FORWARD"


DEBIT_LINE_TYPES = frozenset({LineType.REFUND, LineType.CHARGEBACK, LineType.REVERSAL})


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Merchant(_Record):
    merchant_id: str
    legal_name: str
    registered_mcc: str            # from KYC; the merchant knows its own business
    city: str
    acquirer_id: str
    fidelity: Fidelity
    onboarded_on: date
    is_ecommerce_participant: bool
    upi_class: MerchantClass
    annual_turnover_paise: int = 0     # previous financial year; decides the RBI debit-card MDR band


class MerchantAgreement(_Record):
    """The commercial contract. Card and netbanking MDR are contractual, not
    regulated, so L1 on those instruments is proven against this record."""

    agreement_id: str
    merchant_id: str
    signed_on: date
    settlement_sla_days: int       # banking days; RBI PA Directions 2025 make this contractual
    card_credit_rate_percent: Decimal
    card_debit_rate_percent: Decimal
    netbanking_rate_percent: Decimal


class ProcessorConfigSnapshot(_Record):
    """What the processor had configured from effective_from onward. The
    merchant-visible rate card. A divergence from the agreement or from the
    registered MCC is evidence, but it does not by itself name a fault."""

    snapshot_id: str
    merchant_id: str
    effective_from: date
    pricing_mcc: str
    card_credit_rate_percent: Decimal
    card_debit_rate_percent: Decimal
    netbanking_rate_percent: Decimal


class Transaction(_Record):
    txn_id: str
    merchant_id: str
    kind: TxnKind
    status: TxnStatus
    instrument: Instrument
    amount_paise: int              # always positive; direction comes from kind
    captured_at: datetime
    parent_txn_id: str | None = None
    order_ref: str

    @model_validator(mode="after")
    def _shape(self) -> Transaction:
        if self.amount_paise <= 0:
            raise ValueError(f"{self.txn_id}: amount_paise must be positive")
        if self.kind is not TxnKind.PAYMENT and not self.parent_txn_id:
            raise ValueError(f"{self.txn_id}: {self.kind} must reference a parent payment")
        return self


class SettlementLine(_Record):
    line_id: str
    batch_id: str
    merchant_id: str
    line_type: LineType
    txn_id: str | None             # None for CARRY_FORWARD and RENTAL
    instrument: Instrument | None
    captured_at: datetime | None
    gross_paise: int               # positive for payments, negative for debits
    mdr_paise: int
    gst_paise: int
    tcs_paise: int
    tds_paise: int
    net_paise: int
    ref_batch_id: str | None = None
    charge_ref: str | None = None  # RENTAL: "<device_id>:<YYYY-MM>"

    @model_validator(mode="after")
    def _net_is_consistent(self) -> SettlementLine:
        expected = (
            self.gross_paise - self.mdr_paise - self.gst_paise - self.tcs_paise - self.tds_paise
        )
        if self.net_paise != expected:
            raise ValueError(
                f"{self.line_id}: net_paise {self.net_paise} != gross - deductions {expected}"
            )
        return self


class SettlementBatch(_Record):
    batch_id: str
    merchant_id: str
    settlement_date: date
    cycle_dates: tuple[date, ...]  # capture cycles aggregated into this batch
    line_count: int
    gross_paise: int
    mdr_paise: int
    gst_paise: int
    tcs_paise: int
    tds_paise: int
    net_paise: int
    utr: str | None                # None when net <= 0 and the balance carries forward
    carried_forward: bool = False


class Device(_Record):
    """A rented acceptance device (for example a payment soundbox) and its terms.

    Rental is debited from settlement once a month. It is chargeable only for
    months that start on or after `rental_free_until` and before `returned_on`.
    """

    device_id: str
    merchant_id: str
    device_type: str
    monthly_rental_paise: int
    activated_on: date
    rental_free_until: date
    returned_on: date | None = None
    return_ref: str | None = None


class BankCredit(_Record):
    """A line on the merchant's bank statement. Not every credit is a settlement."""

    credit_id: str
    merchant_id: str
    value_date: date
    amount_paise: int
    utr: str
    narration: str
