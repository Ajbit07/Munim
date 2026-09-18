"""The simulated processor: what a payment actually got charged.

For every successful payment we compute:

  correct -- what the tariff says, using the merchant's REGISTERED MCC and the
             SIGNED agreement rates
  charged -- what the processor deducted, using its CONFIGURED rate card and
             whatever behavioural faults are active on the capture date

Faults are applied in three stages, and each stage's increment is attributed
to the fault that caused it, as a separate planted component:

  1. MDR stage   -- the MDR and the standard GST on that MDR     (L1, L2)
  2. GST stage   -- GST beyond 18% of the charged MDR, or GST on
                    an exempt settlement                         (L3)
  3. Tax stage   -- TCS and TDS beyond what applies              (L4)

Only `charged` reaches the settlement report.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime

from mfp.core.enums import Instrument, MerchantClass
from mfp.core.money import Rate
from mfp.data.generator.params import GenerationParams
from mfp.data.generator.tariff import CARD_INSTRUMENTS, Charges, ProcessorTariff
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
    correct: dict[str, Charges]
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
    turnover = m.get("annual_turnover_paise")
    agreement_rates = world.agreement_rates()

    result = ChargeResult({}, {}, [], [])

    for txn in ledger:
        if txn["kind"] != "PAYMENT" or txn["status"] != "SUCCESS":
            continue
        txn_id = txn["txn_id"]
        instrument = Instrument(txn["instrument"])
        amount = txn["amount_paise"]
        on = datetime.fromisoformat(txn["captured_at"]).date()
        mistag_draw = behaviour.random()  # drawn for every payment; the stream never shifts

        correct = tariff.correct_charges(
            instrument, amount, on, mcc=registered_mcc, rates=agreement_rates,
            upi_class=upi_class, is_ecommerce_participant=eco, turnover=turnover,
        )
        snap = world.snapshot_on(on)
        configured = tariff.correct_charges(
            instrument, amount, on, mcc=snap["pricing_mcc"], rates=world.snapshot_rates(snap),
            upi_class=upi_class, is_ecommerce_participant=eco, turnover=turnover,
        )

        # -- stage 1: MDR ------------------------------------------------
        stage1, mdr_fault = _mdr_stage(world, tariff, instrument, amount, on, configured, correct, mistag_draw)
        if mdr_fault is None and (stage1.mdr + stage1.gst) != (correct.mdr + correct.gst):
            mdr_fault = Fault.MCC_MISCONFIG if instrument is Instrument.RUPAY_CC_ON_UPI else Fault.CONTRACT_RATE_DRIFT

        # -- stage 2: GST ------------------------------------------------
        stage2, gst_fault = stage1, None
        if (
            world.active_fault(Fault.GST_ON_EXEMPT_CARD, on)
            and instrument in CARD_INSTRUMENTS
            and amount <= tariff.gst_exempt_ceiling
            and stage1.mdr > 0 and stage1.gst == 0
        ):
            stage2, gst_fault = replace(stage1, gst=tariff.pct(stage1.mdr, tariff.gst_rate)), Fault.GST_ON_EXEMPT_CARD
        elif world.active_fault(Fault.GST_TAX_ON_TAX, on) and stage1.gst > 0:
            stage2 = replace(stage1, gst=tariff.pct(stage1.mdr + stage1.gst, tariff.gst_rate))
            gst_fault = Fault.GST_TAX_ON_TAX

        # -- stage 3: tax ------------------------------------------------
        stage3, tax_fault = stage2, None
        if world.active_fault(Fault.TAX_ON_NON_ECO, on) and not eco:
            tcs, tds = tariff.taxes(amount, on, is_ecommerce_participant=True)
            if tcs or tds:
                stage3, tax_fault = replace(stage2, tcs=tcs, tds=tds), Fault.TAX_ON_NON_ECO

        result.charges[txn_id] = stage3
        result.correct[txn_id] = correct

        planted = False
        if mdr_fault is not None:
            before = correct.mdr + correct.gst
            after = stage1.mdr + stage1.gst
            if mdr_fault is Fault.PPI_PASSTHROUGH:
                planted |= _plant(result, world, txn_id, on, mdr_fault, "MDR", ExpectedAction.ESCALATE,
                                  after, after, None, ("MDR.PPI_ON_UPI.INTERCHANGE",))
            else:
                rule_ids = ("AGREEMENT",) if mdr_fault is Fault.CONTRACT_RATE_DRIFT else (correct.mdr_rule or "UNSOURCED",)
                if mdr_fault is Fault.TURNOVER_BAND_MISAPPLIED:
                    rule_ids = ("MDR_CAP.DEBIT_CARD.SMALL_MERCHANT",)
                planted |= _plant(result, world, txn_id, on, mdr_fault, "MDR", ExpectedAction.CLAIM,
                                  after - before, after, before, rule_ids)
        if gst_fault is not None:
            rule = "GST.EXEMPT.PA_SETTLEMENT_UPTO_2000" if gst_fault is Fault.GST_ON_EXEMPT_CARD else "GST.MDR.STANDARD"
            planted |= _plant(result, world, txn_id, on, gst_fault, "GST", ExpectedAction.CLAIM,
                              stage2.gst - stage1.gst, stage2.gst, stage1.gst, (rule,))
        if tax_fault is not None:
            planted |= _plant(result, world, txn_id, on, tax_fault, "TAX", ExpectedAction.CLAIM,
                              stage3.tcs + stage3.tds - stage2.tcs - stage2.tds, stage3.tcs + stage3.tds,
                              stage2.tcs + stage2.tds, ("TCS.PA_FLOW.NOT_APPLICABLE", "TDS.PA_FLOW.NOT_APPLICABLE"))

        if not planted and instrument is Instrument.RUPAY_CC_ON_UPI and stage3.mdr > 0:
            result.lookalikes.append(Lookalike(
                lookalike_id=f"LKA-{txn_id}", merchant_id=world.merchant_id,
                resembles="L2_NIL_MDR_VIOLATION", expected_action=ExpectedAction.DO_NOT_CLAIM,
                txn_id=txn_id,
                reason="RuPay credit card on UPI above Rs 2,000 is outside nil-MDR protection "
                       "and was charged at the correct MCC rate",
            ))
        if not planted and eco and (stage3.tcs or stage3.tds):
            result.lookalikes.append(Lookalike(
                lookalike_id=f"LKA-TAX-{txn_id}", merchant_id=world.merchant_id,
                resembles="L4_TAX_MISAPPLICATION", expected_action=ExpectedAction.DO_NOT_CLAIM,
                txn_id=txn_id,
                reason="E-commerce participant; TCS under CGST s.52 and TDS under s.194-O correctly applied",
            ))

    return result


def _plant(result: ChargeResult, world: MerchantWorld, txn_id: str, on: date, fault: Fault,
           component: str, action: ExpectedAction, amount: int, charged: int,
           correct: int | None, rule_ids: tuple[str, ...]) -> bool:
    if amount <= 0:
        return False
    dtype, subtype = FAULT_SUBTYPE[fault]
    result.plants.append(PlantedDiscrepancy(
        plant_id=f"PLT-{txn_id}-{component}", merchant_id=world.merchant_id, fault=fault,
        discrepancy_type=dtype, subtype=subtype, expected_action=action, txn_id=txn_id,
        captured_on=on, component=component, amount_paise=amount, charged_paise=charged,
        correct_paise=correct, rule_ids=rule_ids,
    ))
    return True


def _mdr_stage(
    world: MerchantWorld,
    tariff: ProcessorTariff,
    instrument: Instrument,
    amount: int,
    on: date,
    configured: Charges,
    correct: Charges,
    mistag_draw: float,
) -> tuple[Charges, Fault | None]:
    ceiling = tariff.small_value_ceiling
    snap_rates = world.snapshot_rates(world.snapshot_on(on))

    def processed_as_debit_card() -> Charges:
        mdr = tariff.pct(amount, Rate.from_percent(snap_rates.card_debit))
        gst, gst_rule = tariff.gst(Instrument.CARD_DEBIT, amount, mdr, on)
        return replace(configured, mdr=mdr, gst=gst, mdr_rule="AGREEMENT", gst_rule=gst_rule)

    if instrument is Instrument.CARD_DEBIT and world.active_fault(Fault.TURNOVER_BAND_MISAPPLIED, on):
        # The processor believes this is a large merchant and prices at the large-merchant ceiling.
        mdr = min(tariff.pct(amount, tariff.large_debit_rate), tariff.large_debit_cap)
        if mdr > configured.mdr:
            gst, gst_rule = tariff.gst(Instrument.CARD_DEBIT, amount, mdr, on)
            return replace(configured, mdr=mdr, gst=gst, mdr_rule="MDR_CAP.DEBIT_CARD.OTHER_MERCHANT",
                           gst_rule=gst_rule), Fault.TURNOVER_BAND_MISAPPLIED

    if instrument is Instrument.RUPAY_DEBIT and world.active_fault(Fault.RUPAY_DEBIT_AS_DEBIT, on):
        return processed_as_debit_card(), Fault.RUPAY_DEBIT_AS_DEBIT

    if instrument is Instrument.UPI_P2M_BANK:
        small = world.active_fault(Fault.UPI_SMALL_MDR, on)
        if small and amount <= ceiling and mistag_draw < small.params["fraction"]:
            return processed_as_debit_card(), Fault.UPI_SMALL_MDR
        if world.active_fault(Fault.UPI_MDR_EARLY, on) and amount > ceiling and correct.mdr == 0:
            mdr = min(tariff.pct(amount, tariff.upi_mdr_rate), tariff.upi_mdr_cap)
            gst, _ = tariff.gst(instrument, amount, mdr, on)
            return (replace(configured, mdr=mdr, gst=gst, mdr_rule="MDR.UPI_P2M.STANDARD.ABOVE_2000",
                            gst_rule="GST.MDR.STANDARD"), Fault.UPI_MDR_EARLY)

    if instrument is Instrument.PPI_ON_UPI and amount > ceiling and world.active_fault(Fault.PPI_PASSTHROUGH, on):
        mdr = tariff.pct(amount, tariff.ppi_rate)
        gst, _ = tariff.gst(instrument, amount, mdr, on)
        return (replace(configured, mdr=mdr, gst=gst, mdr_rule="MDR.PPI_ON_UPI.INTERCHANGE",
                        gst_rule="GST.MDR.STANDARD"), Fault.PPI_PASSTHROUGH)

    return configured, None
