"""Settlement batching and the bank statement.

Many transactions collapse into one settlement batch, and one batch into one
bank credit. The bank statement also carries credits that are not settlements
at all, so reconciliation must match on UTR rather than assume every credit
belongs to a batch.

Cycle rules (config/settlement_rules.json):
  - a capture at or after the cutoff time belongs to the next day's cycle
  - a cycle settles `settlement_sla_days` banking days later (weekends skipped)
  - a batch whose settlement date is after as_of has not happened yet
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from mfp.data.generator.params import GenerationParams
from mfp.data.generator.tariff import Charges
from mfp.data.generator.truth import FAULT_SUBTYPE, ExpectedAction, Fault, Lookalike, PlantedDiscrepancy
from mfp.data.generator.world import MerchantWorld, stream

_LINE_TYPE = {"PAYMENT": "PAYMENT", "REFUND": "REFUND", "CHARGEBACK": "CHARGEBACK"}


def is_banking_day(day: date) -> bool:
    return day.weekday() < 5


def add_banking_days(day: date, n: int) -> date:
    current = day
    remaining = n
    while remaining > 0:
        current += timedelta(days=1)
        if is_banking_day(current):
            remaining -= 1
    return current


def cycle_date(captured_at: datetime, cutoff: time) -> date:
    local = captured_at.date()
    if captured_at.timetz().replace(tzinfo=None) >= cutoff:
        return local + timedelta(days=1)
    return local


@dataclass
class SettlementResult:
    batches: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    credits: list[dict] = field(default_factory=list)
    lookalikes: list[Lookalike] = field(default_factory=list)
    plants: list[PlantedDiscrepancy] = field(default_factory=list)


def settle(
    params: GenerationParams,
    world: MerchantWorld,
    ledger: list[dict],
    charges: dict[str, Charges],
    settlement_rules: dict,
    correct: dict[str, Charges] | None = None,
) -> SettlementResult:
    mid = world.merchant_id
    sla = world.agreement["settlement_sla_days"]
    cutoff = time.fromisoformat(settlement_rules["default_merchant_sla"]["cutoff_local_time"])
    slip_rng = stream(params, "settlement", mid)
    bank_rng = stream(params, "bank", mid)
    grace = settlement_rules["lifecycle"]["unsettled_after_sla_days"]
    correct = correct or {}
    result = SettlementResult()

    by_date: dict[date, list[tuple[dict, date]]] = defaultdict(list)
    for txn in ledger:
        if txn["status"] != "SUCCESS":
            continue
        captured = datetime.fromisoformat(txn["captured_at"])
        cycle = cycle_date(captured, cutoff)
        settles_on = add_banking_days(cycle, sla)
        # Every draw happens for every transaction, so the stream never shifts.
        slip = slip_rng.random() < params.late_settlement_probability
        drop_draw = slip_rng.random()
        dup_draw = slip_rng.random()
        on = captured.date()

        drop = world.active_fault(Fault.DROPPED_FROM_BATCH, on)
        if (
            txn["kind"] == "PAYMENT" and drop and drop_draw < drop.params["probability"]
            and add_banking_days(settles_on, grace) <= params.as_of
        ):
            owed = txn["amount_paise"] - correct.get(txn["txn_id"], Charges()).total
            dtype, subtype = FAULT_SUBTYPE[Fault.DROPPED_FROM_BATCH]
            result.plants.append(PlantedDiscrepancy(
                plant_id=f"PLT-{txn['txn_id']}-SETTLEMENT", merchant_id=mid, fault=Fault.DROPPED_FROM_BATCH,
                discrepancy_type=dtype, subtype=subtype, expected_action=ExpectedAction.CLAIM,
                txn_id=txn["txn_id"], captured_on=on, component="SETTLEMENT", amount_paise=owed,
                charged_paise=0, correct_paise=owed,  # nothing settled; owed is the correct net
                rule_ids=("RECON.PAYMENT.SETTLED_ONCE",),
            ))
            continue

        dup = world.active_fault(Fault.DUPLICATE_REFUND_DEBIT, on)
        if txn["kind"] == "REFUND" and dup and dup_draw < dup.params["probability"]:
            again_on = add_banking_days(settles_on, 1)
            if again_on <= params.as_of:
                by_date[again_on].append((txn, cycle))
                dtype, subtype = FAULT_SUBTYPE[Fault.DUPLICATE_REFUND_DEBIT]
                result.plants.append(PlantedDiscrepancy(
                    plant_id=f"PLT-{txn['txn_id']}-REFUND_DEBIT", merchant_id=mid,
                    fault=Fault.DUPLICATE_REFUND_DEBIT, discrepancy_type=dtype, subtype=subtype,
                    expected_action=ExpectedAction.CLAIM, txn_id=txn["txn_id"], captured_on=on,
                    component="REFUND_DEBIT", amount_paise=txn["amount_paise"],
                    charged_paise=2 * txn["amount_paise"], correct_paise=txn["amount_paise"],
                    rule_ids=("RECON.REFUND.SINGLE_DEBIT",),
                ))
        if txn["kind"] == "PAYMENT" and slip:
            settles_on = add_banking_days(settles_on, 1)
            if settles_on <= params.as_of:
                result.lookalikes.append(Lookalike(
                    lookalike_id=f"LKA-LATE-{txn['txn_id']}", merchant_id=mid,
                    resembles="L6_UNSETTLED_TRANSACTION", expected_action=ExpectedAction.DO_NOT_CLAIM,
                    txn_id=txn["txn_id"],
                    reason="Settled one banking day after its contracted cycle; inside the "
                           "unsettled_after_sla_days grace window",
                ))
        if settles_on > params.as_of:
            continue  # not yet due: a natural AWAITING_CYCLE case, not an unsettled one
        by_date[settles_on].append((txn, cycle))

    carry = 0
    carry_from: str | None = None
    for settles_on in sorted(by_date):
        entries = sorted(by_date[settles_on], key=lambda e: (e[0]["captured_at"], e[0]["txn_id"]))
        batch_id = f"STL-{mid}-{settles_on:%Y%m%d}"
        lines: list[dict] = []

        if carry < 0:
            lines.append(_line(batch_id, mid, len(lines) + 1, "CARRY_FORWARD", None, carry,
                               Charges(), ref_batch_id=carry_from))
            carry, carry_from = 0, None

        seen_in_batch: set[str] = set()
        for txn, _cycle in entries:
            kind = txn["kind"]
            if txn["txn_id"] in seen_in_batch:
                continue
            seen_in_batch.add(txn["txn_id"])
            gross = txn["amount_paise"] if kind == "PAYMENT" else -txn["amount_paise"]
            deductions = charges.get(txn["txn_id"], Charges()) if kind == "PAYMENT" else Charges()
            lines.append(_line(batch_id, mid, len(lines) + 1, _LINE_TYPE[kind], txn, gross, deductions))

        totals = {k: sum(line[k] for line in lines)
                  for k in ("gross_paise", "mdr_paise", "gst_paise", "tcs_paise", "tds_paise", "net_paise")}
        net = totals["net_paise"]
        utr = None
        if net > 0:
            utr = f"UTR{bank_rng.randrange(10**12):012d}"
            result.credits.append({
                "credit_id": f"CR-{mid}-{settles_on:%Y%m%d}", "merchant_id": mid,
                "value_date": settles_on.isoformat(), "amount_paise": net, "utr": utr,
                "narration": f"PG SETTLEMENT {batch_id}",
            })
        elif net < 0:
            carry, carry_from = net, batch_id

        result.batches.append({
            "batch_id": batch_id, "merchant_id": mid, "settlement_date": settles_on.isoformat(),
            "cycle_dates": sorted({c.isoformat() for _, c in entries}),
            "line_count": len(lines), **totals, "utr": utr, "carried_forward": net < 0,
        })
        result.lines.extend(lines)

    # Unrelated credits on the same statement: invoices, transfers, cash deposits.
    day = params.window_start
    noise_n = 0
    while day <= params.as_of:
        if is_banking_day(day) and bank_rng.random() < params.noise_credit_probability:
            noise_n += 1
            result.credits.append({
                "credit_id": f"CR-{mid}-N{noise_n:05d}", "merchant_id": mid,
                "value_date": day.isoformat(), "amount_paise": bank_rng.randint(50_000, 5_000_000),
                "utr": f"UTR{bank_rng.randrange(10**12):012d}",
                "narration": bank_rng.choice(("NEFT CR INVOICE PAYMENT", "IMPS CR TRANSFER", "CASH DEPOSIT")),
            })
        day += timedelta(days=1)

    result.credits.sort(key=lambda c: (c["value_date"], c["credit_id"]))
    return result


def _line(batch_id: str, mid: str, n: int, line_type: str, txn: dict | None,
          gross: int, c: Charges, ref_batch_id: str | None = None) -> dict:
    return {
        "line_id": f"{batch_id}-L{n:05d}", "batch_id": batch_id, "merchant_id": mid,
        "line_type": line_type,
        "txn_id": txn["txn_id"] if txn else None,
        "instrument": txn["instrument"] if txn else None,
        "captured_at": txn["captured_at"] if txn else None,
        "gross_paise": gross, "mdr_paise": c.mdr, "gst_paise": c.gst,
        "tcs_paise": c.tcs, "tds_paise": c.tds,
        "net_paise": gross - c.mdr - c.gst - c.tcs - c.tds,
        "ref_batch_id": ref_batch_id,
    }
