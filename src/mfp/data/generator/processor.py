"""The simulated processor: what a payment actually got charged.

For every successful payment we compute two things:

  correct -- what the tariff says, using the merchant's REGISTERED MCC and the
             SIGNED agreement rates
  charged -- what the processor deducted, using its CONFIGURED rate card and
             whatever behavioural faults are active on the capture date

Only `charged` reaches the settlement report. The difference, where one
exists, is written to hidden ground truth as a planted discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from mfp.core.enums import Instrument, MerchantClass
from mfp.core.money import Rate
from mfp.data.generator.params import GenerationParams
from mfp.data.generator.tariff import Charges, ProcessorTariff
from mfp.data.generator.truth import (
    FAULT_SUBTYPE,
    ExpectedAction,
    Fault,
    Lookalike,
    PlantedDiscrepancy,
)
from mfp.data.generator.world import MerchantWorld, stream


@dataclass
class ChargeResult:
    charges: dict[str, Charges]
    plants: list[PlantedDiscrepancy]
    lookalikes: list[Lookalike]


def charge_ledger(
    params: GenerationParams, world: MerchantWorld, ledger: list[dict], tariff: ProcessorTariff
) -> ChargeResult:
    behaviour = stream(params, "behaviour", world.merchant_id)
    m = world.merchant
    upi_class = MerchantClass(m["upi_class"])
    eco = m["is_ecommerce_participant"]
    registered_mcc = m["registered_mcc"]
    agreement_rates = world.agreement_rates()

    charges: dict[str, Charges] = {}
    plants: list[PlantedDiscrepancy] = []
    lookalikes: list[Lookalike] = []

    for txn in ledger:
        if txn["kind"] != "PAYMENT" or txn["status"] != "SUCCESS":
            continue
        txn_id = txn["txn_id"]
        instrument = Instrument(txn["instrument"])
        amount = txn["amount_paise"]
        on = datetime.fromisoformat(txn["captured_at"]).date()

        # Drawn for every payment, so the stream never depends on which faults exist.
        mistag_draw = behaviour.random()

        correct = tariff.correct_charges(
            instrument, amount, on, mcc=registered_mcc, rates=agreement_rates,
            upi_class=upi_class, is_ecommerce_participant=eco,
        )

        snap = world.snapshot_on(on)
        configured = tariff.correct_charges(
            instrument, amount, on, mcc=snap["pricing_mcc"], rates=world.snapshot_rates(snap),
            upi_class=upi_class, is_ecommerce_participant=eco,
        )

        charged, fault = _apply_behaviour(
            world, tariff, instrument, amount, on, configured, correct, mistag_draw, params
        )

        if fault is None and charged.total != correct.total:
            # Only RuPay-CC-on-UPI pricing depends on MCC; every other drift is contractual.
            fault = (
                Fault.MCC_MISCONFIG
                if instrument is Instrument.RUPAY_CC_ON_UPI
                else Fault.CONTRACT_RATE_DRIFT
            )

        charges[txn_id] = charged

        if fault is not None:
            dtype, subtype = FAULT_SUBTYPE[fault]
            if fault is Fault.PPI_PASSTHROUGH:
                action, amount_owed, correct_paise = ExpectedAction.ESCALATE, charged.total, None
            else:
                action = ExpectedAction.CLAIM
                amount_owed = charged.total - correct.total
                correct_paise = correct.total
            if amount_owed > 0:
                plants.append(PlantedDiscrepancy(
                    plant_id=f"PLT-{txn_id}", merchant_id=world.merchant_id, fault=fault,
                    discrepancy_type=dtype, subtype=subtype, expected_action=action,
                    txn_id=txn_id, captured_on=on, amount_paise=amount_owed,
                    charged_paise=charged.total, correct_paise=correct_paise,
                    rule_ids=_rule_ids(fault, correct),
                ))
                continue

        if instrument is Instrument.RUPAY_CC_ON_UPI and charged.mdr > 0:
            lookalikes.append(Lookalike(
                lookalike_id=f"LKA-{txn_id}", merchant_id=world.merchant_id,
                resembles="L2_NIL_MDR_VIOLATION", expected_action=ExpectedAction.DO_NOT_CLAIM,
                txn_id=txn_id,
                reason="RuPay credit card on UPI above Rs 2,000 is outside nil-MDR protection "
                       "and was charged at the correct MCC rate",
            ))

    return ChargeResult(charges, plants, lookalikes)


def _apply_behaviour(
    world: MerchantWorld,
    tariff: ProcessorTariff,
    instrument: Instrument,
    amount: int,
    on: date,
    configured: Charges,
    correct: Charges,
    mistag_draw: float,
    params: GenerationParams,
) -> tuple[Charges, Fault | None]:
    """Return (charged, behavioural fault or None)."""
    ceiling = tariff.small_value_ceiling
    snap_rates = world.snapshot_rates(world.snapshot_on(on))

    def as_debit_card(treated_as: Instrument) -> Charges:
        mdr = tariff.pct(amount, Rate.from_percent(snap_rates.card_debit))
        gst, gst_rule = tariff.gst(treated_as, amount, mdr, on)
        return Charges(mdr=mdr, gst=gst, tcs=configured.tcs, tds=configured.tds,
                       mdr_rule="AGREEMENT", gst_rule=gst_rule)

    if instrument is Instrument.RUPAY_DEBIT and world.active_fault(Fault.RUPAY_DEBIT_AS_DEBIT, on):
        return as_debit_card(Instrument.CARD_DEBIT), Fault.RUPAY_DEBIT_AS_DEBIT

    if instrument is Instrument.UPI_P2M_BANK:
        small = world.active_fault(Fault.UPI_SMALL_MDR, on)
        if small and amount <= ceiling and mistag_draw < small.params["fraction"]:
            return as_debit_card(Instrument.CARD_DEBIT), Fault.UPI_SMALL_MDR
        if world.active_fault(Fault.UPI_MDR_EARLY, on) and amount > ceiling and correct.mdr == 0:
            mdr = min(tariff.pct(amount, tariff.upi_mdr_rate), tariff.upi_mdr_cap)
            gst, _ = tariff.gst(instrument, amount, mdr, on)
            return (Charges(mdr=mdr, gst=gst, tcs=configured.tcs, tds=configured.tds,
                            mdr_rule="MDR.UPI_P2M.STANDARD.ABOVE_2000", gst_rule="GST.MDR.STANDARD"),
                    Fault.UPI_MDR_EARLY)

    if instrument is Instrument.PPI_ON_UPI and amount > ceiling and world.active_fault(Fault.PPI_PASSTHROUGH, on):
        mdr = tariff.pct(amount, tariff.ppi_rate)
        gst, _ = tariff.gst(instrument, amount, mdr, on)
        return (Charges(mdr=mdr, gst=gst, tcs=configured.tcs, tds=configured.tds,
                        mdr_rule="MDR.PPI_ON_UPI.INTERCHANGE", gst_rule="GST.MDR.STANDARD"),
                Fault.PPI_PASSTHROUGH)

    return configured, None


def _rule_ids(fault: Fault, correct: Charges) -> tuple[str, ...]:
    if fault is Fault.PPI_PASSTHROUGH:
        return ("MDR.PPI_ON_UPI.INTERCHANGE",)
    ids = [correct.mdr_rule or "UNSOURCED"]
    if fault is Fault.CONTRACT_RATE_DRIFT:
        ids = ["AGREEMENT"]
    ids.append(correct.gst_rule or "GST.MDR.STANDARD")
    return tuple(dict.fromkeys(ids))
