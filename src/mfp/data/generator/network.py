"""Signatures published to the network by other merchants' agents.

The 225 signature-only merchants have no ledger in this dataset. What the
network actually receives from them is a stream of privacy-safe signatures,
emitted by their own agents elsewhere. This module simulates that stream from
their fault profiles, using estimated volumes -- it is the network's observed
input, written to network_signatures.jsonl.

Full-fidelity merchants do NOT appear here. Their signatures are produced at
runtime by our own agents from proven cases.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from mfp.core.enums import Instrument
from mfp.data.generator.params import INSTRUMENT_MIX, GenerationParams
from mfp.data.generator.truth import FAULT_SUBTYPE, Fault
from mfp.data.generator.world import MerchantWorld
from mfp.schemas.network import emitter_hash, impact_floor, merchant_segment, occurrences_bucket

_TOTAL_WEIGHT = sum(INSTRUMENT_MIX.values())

# fault -> (pattern, instrument, violated rule, share of payments affected, rupees per occurrence)
_SHAPE: dict[Fault, tuple[str, Instrument | None, str, float, float]] = {
    Fault.MCC_MISCONFIG: ("mdr_above_mcc_rate", Instrument.RUPAY_CC_ON_UPI, "MDR.RUPAY_CC_UPI.RETAIL_110", 0.13, 30.0),
    Fault.CONTRACT_RATE_DRIFT: ("mdr_above_agreement", Instrument.CARD_CREDIT, "AGREEMENT", 1.0, 2.9),
    Fault.UPI_SMALL_MDR: ("mdr_on_protected_instrument", Instrument.UPI_P2M_BANK, "MDR.UPI_P2M.NIL.LEGACY", 0.026, 3.5),
    Fault.UPI_MDR_EARLY: ("mdr_on_protected_instrument", Instrument.UPI_P2M_BANK, "MDR.UPI_P2M.NIL.LEGACY", 0.13, 17.0),
    Fault.PPI_PASSTHROUGH: ("unverified_interchange_passthrough", Instrument.PPI_ON_UPI, "MDR.PPI_ON_UPI.INTERCHANGE", 0.13, 43.0),
    Fault.RUPAY_DEBIT_AS_DEBIT: ("mdr_on_protected_instrument", Instrument.RUPAY_DEBIT, "MDR.RUPAY_DEBIT.NIL.ALL_AMOUNTS", 1.0, 4.8),
    Fault.GST_ON_EXEMPT_CARD: ("gst_on_exempt_settlement", Instrument.CARD_CREDIT, "GST.EXEMPT.PA_SETTLEMENT_UPTO_2000", 0.87, 1.5),
    Fault.GST_TAX_ON_TAX: ("gst_above_standard_base", Instrument.CARD_CREDIT, "GST.MDR.STANDARD", 0.13, 0.9),
    Fault.TAX_ON_NON_ECO: ("tax_on_non_eco_flow", None, "TCS.PA_FLOW.NOT_APPLICABLE", 1.0, 5.4),
    Fault.DUPLICATE_REFUND_DEBIT: ("refund_debited_twice", None, "RECON.REFUND.SINGLE_DEBIT", 0.0003, 900.0),
    Fault.DROPPED_FROM_BATCH: ("payment_missing_from_settlement", None, "RECON.PAYMENT.SETTLED_ONCE", 0.002, 850.0),
    Fault.TURNOVER_BAND_MISAPPLIED: ("mdr_above_turnover_cap", Instrument.CARD_DEBIT, "MDR_CAP.DEBIT_CARD.SMALL_MERCHANT", 1.0, 1.4),
    Fault.RENTAL_AFTER_RETURN: ("rental_after_return", None, "CONTRACT.DEVICE.RENTAL_TERMS", 0.0, 199.0),
    Fault.RENTAL_DURING_WAIVER: ("rental_during_waiver", None, "CONTRACT.DEVICE.RENTAL_TERMS", 0.0, 199.0),
}
_MONTHLY = frozenset({Fault.RENTAL_AFTER_RETURN, Fault.RENTAL_DURING_WAIVER})


def _month_starts(start: date, end: date):
    current = date(start.year, start.month, 1)
    while current <= end:
        yield current
        current = date(current.year + (current.month // 12), current.month % 12 + 1, 1)


def background_signatures(params: GenerationParams, worlds: list[MerchantWorld], salt: str) -> list[dict]:
    rows: list[dict] = []
    for world in worlds:
        if world.is_full:
            continue
        rng = random.Random(f"{params.seed}/network/{world.merchant_id}")
        emitter = emitter_hash(salt, world.merchant_id)
        segment = merchant_segment(world.merchant["registered_mcc"])
        route = world.merchant["acquirer_id"]
        for profile in world.faults:
            if profile.fault not in _SHAPE:
                continue  # service breaches such as settlement delay are not discrepancy signatures
            pattern, instrument, rule_id, share, per_occurrence = _SHAPE[profile.fault]
            weight = INSTRUMENT_MIX[instrument] / _TOTAL_WEIGHT if instrument else 1.0
            for month in _month_starts(max(profile.active_from, params.window_start), params.as_of):
                month_end = min(date(month.year + (month.month // 12), month.month % 12 + 1, 1) - timedelta(days=1), params.as_of)
                days = (month_end - max(month, profile.active_from)).days + 1
                if days <= 0:
                    continue
                expected = world.txn_per_day * days * weight * share
                occurrences = max(0, int(round(rng.gauss(expected, max(1.0, expected ** 0.5)))))
                if profile.fault in _MONTHLY:
                    occurrences = 1
                if occurrences == 0:
                    continue
                impact = int(occurrences * per_occurrence * rng.uniform(0.8, 1.2) * 100)
                rows.append({
                    "signature_id": f"SIG-{emitter}-{profile.fault}-{month:%Y%m}",
                    "emitter": emitter,
                    "discrepancy_type": FAULT_SUBTYPE[profile.fault][0],
                    "pattern": pattern,
                    "instrument": str(instrument) if instrument else None,
                    "rule_id": rule_id,
                    "processor_route": route,
                    "merchant_segment": segment,
                    "month": f"{month:%Y-%m}",
                    "occurrences_bucket": occurrences_bucket(occurrences),
                    "impact_floor_paise": impact_floor(impact),
                })
    rows.sort(key=lambda r: r["signature_id"])
    return rows
