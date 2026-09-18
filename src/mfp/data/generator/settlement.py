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

_LINE_TYPE = {"PAYMENT": "PAYMENT", "REFUND": "REFUND", "CHARGEBACK": "CHARGEBACK", "REVERSAL": "REVERSAL"}
HOLIDAYS: frozenset[date] = frozenset()


def is_banking_day(day: date, holidays: frozenset[date] | None = None) -> bool:
    return day.weekday() < 5 and day not in (HOLIDAYS if holidays is None else holidays)


def add_banking_days(day: date, n: int, holidays: frozenset[date] | None = None) -> date:
    current = day
    remaining = n
    while remaining > 0:
        current += timedelta(days=1)
        if is_banking_day(current, holidays):
            remaining -= 1
    return current


def next_banking_day(day: date, holidays: frozenset[date] | None = None) -> date:
    current = day
    while not is_banking_day(current, holidays):
        current += timedelta(days=1)
    return current


def month_starts(start: date, end: date):
    current = date(start.year, start.month, 1)
    if current < start:
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)
    while current <= end:
        yield current
        current = date(current.year + current.month // 12, current.month % 12 + 1, 1)


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
    holidays = frozenset(date.fromisoformat(d) for d in settlement_rules["holiday_calendar"].get("national_holidays", []))
    correct = correct or {}
    result = SettlementResult()

    by_date: dict[date, list[tuple[dict, date]]] = defaultdict(list)
    for txn in ledger:
        if txn["status"] != "SUCCESS":
            continue
        captured = datetime.fromisoformat(txn["captured_at"])
        cycle = cycle_date(captured, cutoff)
        settles_on = add_banking_days(cycle, sla, holidays)
        # Every draw happens for every transaction, so the stream never shifts.
        slip = slip_rng.random() < params.late_settlement_probability
        drop_draw = slip_rng.random()
        dup_draw = slip_rng.random()
        delay_draw, delay_days = slip_rng.random(), slip_rng.randint(3, 6)
        on = captured.date()

        drop = world.active_fault(Fault.DROPPED_FROM_BATCH, on)
        if (
            txn["kind"] == "PAYMENT" and drop and drop_draw < drop.params["probability"]
            and add_banking_days(settles_on, grace, holidays) <= params.as_of
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
            again_on = add_banking_days(settles_on, 1, holidays)
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
        delay = world.active_fault(Fault.SETTLEMENT_DELAY, on)
        if txn["kind"] == "PAYMENT" and delay and delay_draw < delay.params["probability"]:
            late_on = add_banking_days(settles_on, grace + delay_days - 2, holidays)
            if late_on <= params.as_of:
                dtype, subtype = FAULT_SUBTYPE[Fault.SETTLEMENT_DELAY]
                result.plants.append(PlantedDiscrepancy(
                    plant_id=f"PLT-{txn['txn_id']}-DELAY", merchant_id=mid, fault=Fault.SETTLEMENT_DELAY,
                    discrepancy_type=dtype, subtype=subtype, expected_action=ExpectedAction.REPORT,
                    txn_id=txn["txn_id"], captured_on=on, component="DELAY", amount_paise=txn["amount_paise"],
                    charged_paise=0, correct_paise=None, rule_ids=("SLA.AGREEMENT.ON_TIME",),
                ))
                by_date[late_on].append((txn, cycle))
                continue
        if txn["kind"] == "PAYMENT" and slip:
            settles_on = add_banking_days(settles_on, 1, holidays)
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

    # -- device rental debits, once a month ------------------------------------
    for device in world.devices:
        activated = date.fromisoformat(device["activated_on"])
        free_until = date.fromisoformat(device["rental_free_until"])
        returned = date.fromisoformat(device["returned_on"]) if device["returned_on"] else None
        for month in month_starts(max(activated, params.window_start), params.as_of):
            chargeable = month >= free_until and (returned is None or month < returned)
            after_return = world.active_fault(Fault.RENTAL_AFTER_RETURN, month) and returned is not None and month >= returned
            in_waiver = world.active_fault(Fault.RENTAL_DURING_WAIVER, month) and month < free_until
            if not (chargeable or after_return or in_waiver):
                continue
            debit_on = next_banking_day(month, holidays)
            if debit_on > params.as_of:
                continue
            ref = f"{device['device_id']}:{month:%Y-%m}"
            by_date[debit_on].append(({"txn_id": None, "kind": "RENTAL", "instrument": None, "captured_at": "",
                                       "amount_paise": device["monthly_rental_paise"], "charge_ref": ref}, debit_on))
            if after_return or in_waiver:
                fault = Fault.RENTAL_AFTER_RETURN if after_return else Fault.RENTAL_DURING_WAIVER
                dtype, subtype = FAULT_SUBTYPE[fault]
                result.plants.append(PlantedDiscrepancy(
                    plant_id=f"PLT-{ref}-RENTAL", merchant_id=mid, fault=fault, discrepancy_type=dtype,
                    subtype=subtype, expected_action=ExpectedAction.CLAIM, txn_id=ref, captured_on=month,
                    component="RENTAL", amount_paise=device["monthly_rental_paise"],
                    charged_paise=device["monthly_rental_paise"], correct_paise=0,
                    rule_ids=("CONTRACT.DEVICE.RENTAL_TERMS",),
                ))
            elif returned is not None and (month.year, month.month) == (returned.year, returned.month):
                result.lookalikes.append(Lookalike(
                    lookalike_id=f"LKA-{ref}", merchant_id=mid, resembles="L7_DEVICE_RENTAL",
                    expected_action=ExpectedAction.DO_NOT_CLAIM, txn_id=ref,
                    reason="Rental for the month the device was returned is billed at the start of that month, "
                           "before the return, and is chargeable",
                ))

    carry = 0
    carry_from: str | None = None
    for settles_on in sorted(by_date):
        entries = sorted(by_date[settles_on], key=lambda e: (e[0]["captured_at"], e[0]["txn_id"] or e[0].get("charge_ref", "")))
        batch_id = f"STL-{mid}-{settles_on:%Y%m%d}"
        lines: list[dict] = []

        if carry < 0:
            lines.append(_line(batch_id, mid, len(lines) + 1, "CARRY_FORWARD", None, carry,
                               Charges(), ref_batch_id=carry_from))
            carry, carry_from = 0, None

        seen_in_batch: set[str] = set()
        for txn, _cycle in entries:
            kind = txn["kind"]
            if kind == "RENTAL":
                lines.append(_line(batch_id, mid, len(lines) + 1, "RENTAL", None, -txn["amount_paise"], Charges(),
                                   charge_ref=txn["charge_ref"]))
                continue
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
            "cycle_dates": sorted({c.isoformat() for t, c in entries if t["kind"] != "RENTAL"}),
            "line_count": len(lines), **totals, "utr": utr, "carried_forward": net < 0,
        })
        result.lines.extend(lines)

    # Unrelated credits on the same statement: invoices, transfers, cash deposits.
    day = params.window_start
    noise_n = 0
    while day <= params.as_of:
        if is_banking_day(day, holidays) and bank_rng.random() < params.noise_credit_probability:
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
          gross: int, c: Charges, ref_batch_id: str | None = None, charge_ref: str | None = None) -> dict:
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
        "charge_ref": charge_ref,
    }
