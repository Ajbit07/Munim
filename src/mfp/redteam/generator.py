"""Red team: legitimate cases built to look like discrepancies.

Each scenario is one synthetic merchant with a handful of records written in
the exact observed-artifact format the production system reads. The whole
production path runs on them -- Monitor, Reconciliation, Investigation, Proof,
Follow-up -- and each scenario is scored as CLAIM, DO_NOT_CLAIM or ESCALATE.

Scenario amounts are computed here from the published rates written out in
plain arithmetic, not by calling the Fee Engine, so a red-team pass is not the
auditor agreeing with itself.

A handful of CONTROL scenarios are genuine discrepancies. Without them, a
system that never filed anything would score a perfect zero false claims.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, ROUND_DOWN, Decimal
from pathlib import Path

from mfp.core.clock import IST

AS_OF = date(2026, 10, 30)
HIDDEN = "_hidden"
EXPECTATIONS = "redteam_expectations.json"


def pct(paise: int, percent: str, rounding=ROUND_HALF_UP) -> int:
    return int((Decimal(paise) * Decimal(percent) / 100).quantize(Decimal(1), rounding=rounding))


def banking_add(day: date, n: int) -> date:
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


@dataclass
class Scenario:
    key: str
    expected: str                  # CLAIM | DO_NOT_CLAIM | ESCALATE
    lookalike_of: str
    explanation: str
    mcc: str = "5411"
    upi_class: str = "P2M"
    eco: bool = False
    acquirer: str = "ACQ-A"
    sla: int = 1
    agreements: list[tuple[str, str, str, str]] = field(default_factory=lambda: [("2024-01-01", "1.60", "0.50", "1.50")])
    turnover: int = 1_00_00_000_00          # Rs 1 crore previous-year turnover unless a scenario says otherwise
    payments: list[dict] = field(default_factory=list)
    refunds: list[dict] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    rentals: list[tuple[str, str]] = field(default_factory=list)   # (device_id, "YYYY-MM") debited

    def pay(self, when: str, instrument: str, amount: int, mdr: int = 0, gst: int = 0, tcs: int = 0, tds: int = 0,
            *, late_days: int = 0, drop: bool = False, failed: bool = False, ref: str | None = None) -> Scenario:
        self.payments.append(dict(when=when, instrument=instrument, amount=amount, mdr=mdr, gst=gst, tcs=tcs, tds=tds,
                                  late_days=late_days, drop=drop, failed=failed, ref=ref))
        return self

    def refund(self, parent_ref: str, when: str, amount: int, *, kind: str = "REFUND", debits: int = 1) -> Scenario:
        self.refunds.append(dict(parent=parent_ref, when=when, amount=amount, kind=kind, debits=debits))
        return self

    def device(self, activated: str, free_until: str, returned: str | None = None) -> Scenario:
        self.devices.append(dict(activated_on=activated, rental_free_until=free_until, returned_on=returned))
        return self

    def rent(self, month: str) -> Scenario:
        self.rentals.append((len(self.devices) - 1, month))
        return self


def scenarios() -> list[Scenario]:
    s: list[Scenario] = []
    add = s.append

    add(Scenario("rupay_cc_upi_correct_mcc_rate", "DO_NOT_CLAIM", "L2",
                 "RuPay credit on UPI above Rs 2,000 carries MDR at the MCC rate; not nil-protected")
        .pay("2026-07-10T12:00", "RUPAY_CC_ON_UPI", 450_000, pct(450_000, "1.10"), pct(pct(450_000, "1.10"), "18")))
    add(Scenario("rupay_cc_upi_exactly_2000", "DO_NOT_CLAIM", "L2",
                 "Exactly Rs 2,000.00 sits inside the nil band")
        .pay("2026-07-11T12:00", "RUPAY_CC_ON_UPI", 200_000))
    add(Scenario("card_gst_exempt_at_2000", "DO_NOT_CLAIM", "L3",
                 "Card settlement of exactly Rs 2,000.00 is GST-exempt for an RBI-regulated PA")
        .pay("2026-07-14T12:00", "CARD_DEBIT", 200_000, pct(200_000, "0.50")))
    add(Scenario("card_gst_applies_at_2000_01", "DO_NOT_CLAIM", "L3",
                 "One paise above the ceiling, GST at 18% on MDR is correct")
        .pay("2026-07-14T13:00", "CARD_DEBIT", 200_001, pct(200_001, "0.50"), pct(pct(200_001, "0.50"), "18")))
    sc = Scenario("amended_agreement_rate_increase", "DO_NOT_CLAIM", "L1",
                  "Rate rose because a later agreement amendment was signed, not by drift")
    sc.agreements = [("2024-01-01", "1.60", "0.50", "1.50"), ("2026-05-01", "1.90", "0.50", "1.50")]
    add(sc.pay("2026-06-10T12:00", "CARD_CREDIT", 120_000, pct(120_000, "1.90"), 0))
    add(Scenario("upi_mdr_after_effective_date", "DO_NOT_CLAIM", "L2",
                 "From 15 Oct 2026 the 0.4% UPI MDR is legitimate for P2M above Rs 2,000")
        .pay("2026-10-20T12:00", "UPI_P2M_BANK", 500_000, 2_000, pct(2_000, "18")))
    add(Scenario("upi_mdr_cap_binds", "DO_NOT_CLAIM", "L2", "The Rs 300 cap applies at Rs 1,00,000")
        .pay("2026-10-20T13:00", "UPI_P2M_BANK", 10_000_000, 30_000, pct(30_000, "18")))
    add(Scenario("essential_fuel_flat_fee", "DO_NOT_CLAIM", "L2", "Fuel above Rs 2,000 pays a flat Rs 5", mcc="5541")
        .pay("2026-10-21T12:00", "UPI_P2M_BANK", 300_000, 500, pct(500, "18")))
    add(Scenario("eco_merchant_tcs_tds", "DO_NOT_CLAIM", "L4",
                 "An e-commerce participant correctly bears TCS 0.5% and TDS 0.1%", mcc="5999", eco=True)
        .pay("2026-08-05T12:00", "CARD_CREDIT", 150_000, pct(150_000, "1.60"), 0, pct(150_000, "0.5"), pct(150_000, "0.1")))
    add(Scenario("late_settlement_within_grace", "DO_NOT_CLAIM", "L6", "Settled one banking day late, inside grace")
        .pay("2026-09-02T12:00", "UPI_P2M_BANK", 90_000, late_days=1))
    add(Scenario("capture_after_cutoff", "DO_NOT_CLAIM", "L6", "Captured 23:30 Friday; belongs to Saturday's cycle")
        .pay("2026-09-04T23:30", "UPI_P2M_BANK", 70_000))
    add(Scenario("not_yet_due_at_as_of", "DO_NOT_CLAIM", "L6", "Captured on the audit date; settlement not yet due")
        .pay(f"{AS_OF}T11:00", "UPI_P2M_BANK", 80_000, drop=True))
    add(Scenario("failed_payment_never_settled", "DO_NOT_CLAIM", "L6", "A failed payment should never settle")
        .pay("2026-09-08T12:00", "UPI_P2M_BANK", 60_000, failed=True))
    add(Scenario("partial_refunds_two_events", "DO_NOT_CLAIM", "L5", "Two partial refunds, two refund events, one debit each")
        .pay("2026-09-09T12:00", "UPI_P2M_BANK", 300_000, ref="P1")
        .refund("P1", "2026-09-10T12:00", 100_000).refund("P1", "2026-09-11T12:00", 50_000))
    add(Scenario("same_amount_two_refunds", "DO_NOT_CLAIM", "L5", "Two refunds of equal amount are two events, not a duplicate")
        .pay("2026-09-09T12:00", "UPI_P2M_BANK", 200_000, ref="P1")
        .refund("P1", "2026-09-10T12:00", 40_000).refund("P1", "2026-09-10T12:05", 40_000))
    add(Scenario("chargeback_debit", "DO_NOT_CLAIM", "L5", "A chargeback debited once is legitimate")
        .pay("2026-08-01T12:00", "CARD_CREDIT", 250_000, pct(250_000, "1.60"), pct(pct(250_000, "1.60"), "18"), ref="P1")
        .refund("P1", "2026-09-01T12:00", 250_000, kind="CHARGEBACK"))
    add(Scenario("ppi_interchange_charged", "ESCALATE", "L2",
                 "Wallet interchange charged; whether the merchant contract allows pass-through is not established")
        .pay("2026-08-12T12:00", "PPI_ON_UPI", 500_000, pct(500_000, "1.10"), pct(pct(500_000, "1.10"), "18")))
    add(Scenario("rupay_cc_upi_before_june_charged", "ESCALATE", "L1",
                 "RuPay credit on UPI charged before 1 Jun 2026, when no sourced rule exists")
        .pay("2026-04-10T12:00", "RUPAY_CC_ON_UPI", 400_000, pct(400_000, "1.75"), pct(pct(400_000, "1.75"), "18")))
    # 0.50% of Rs 2,001.00 is 1000.5 paise: half-up gives 1001, half-even and truncation give 1000.
    # The processor rounded half-up. An auditor that assumed truncation would "find" one paise.
    amount = 200_100
    add(Scenario("rounding_convention_half_paise", "DO_NOT_CLAIM", "L1",
                 "A half-paise MDR rounded up is a rounding convention, not an overcharge")
        .pay("2026-08-20T12:00", "CARD_DEBIT", amount, pct(amount, "0.50"), pct(pct(amount, "0.50"), "18")))

    small = 12_00_000_00  # Rs 12 lakh previous-year turnover: RBI small-merchant band
    sc = Scenario("small_merchant_debit_within_ceiling", "DO_NOT_CLAIM", "L1",
                  "Small merchant charged 0.40% on a debit card: exactly the RBI ceiling", turnover=small)
    sc.agreements = [("2024-01-01", "1.60", "0.40", "1.50")]
    add(sc.pay("2026-08-18T12:00", "CARD_DEBIT", 300_000, pct(300_000, "0.40"), pct(pct(300_000, "0.40"), "18")))
    add(Scenario("rental_in_an_active_month", "DO_NOT_CLAIM", "L7",
                 "Soundbox rental debited for a month the device was in use")
        .device("2025-01-10", "2025-04-10").rent("2026-08"))
    add(Scenario("rental_for_the_month_of_return", "DO_NOT_CLAIM", "L7",
                 "Rental billed on 1 Aug for a device returned on 20 Aug is chargeable")
        .device("2025-01-10", "2025-04-10", "2026-08-20").rent("2026-08"))
    add(Scenario("reversal_debited_once", "DO_NOT_CLAIM", "L5", "A network reversal debited once is legitimate")
        .pay("2026-08-03T12:00", "CARD_CREDIT", 280_000, pct(280_000, "1.60"), pct(pct(280_000, "1.60"), "18"), ref="P1")
        .refund("P1", "2026-08-05T12:00", 280_000, kind="REVERSAL"))
    p2pm = Scenario("p2pm_merchant_reclassified_after_three_months", "DO_NOT_CLAIM", "L2",
                    "A small (P2PM) merchant above Rs 1 lakh of UPI a month for three months is P2M from then on, "
                    "so the 0.4% UPI MDR after 15 Oct is legitimate", upi_class="P2PM")
    for month in ("07", "08", "09"):
        for day in ("05", "12", "19"):
            p2pm.pay(f"2026-{month}-{day}T12:00", "UPI_P2M_BANK", 4_000_000)
    add(p2pm.pay("2026-10-20T12:00", "UPI_P2M_BANK", 300_000, 1_200, pct(1_200, "18")))
    add(Scenario("late_settlement_beyond_grace", "DO_NOT_CLAIM", "L6",
                 "Settled five banking days late: a reportable SLA breach, but no money is owed")
        .pay("2026-09-07T12:00", "UPI_P2M_BANK", 150_000, late_days=5))

    # -- controls: genuine discrepancies -------------------------------------------------
    add(Scenario("control_upi_mdr_before_oct15", "CLAIM", "control",
                 "0.4% UPI MDR charged on 1 Sep 2026, before its effective date")
        .pay("2026-09-01T12:00", "UPI_P2M_BANK", 500_000, 2_000, pct(2_000, "18")))
    add(Scenario("control_rupay_debit_mdr", "CLAIM", "control", "RuPay debit charged MDR")
        .pay("2026-09-03T12:00", "RUPAY_DEBIT", 80_000, pct(80_000, "0.50"), 0))
    add(Scenario("control_p2pm_charged_after_oct15", "CLAIM", "control",
                 "A P2PM small merchant charged the 0.4% UPI MDR after 15 Oct", upi_class="P2PM")
        .pay("2026-10-20T12:00", "UPI_P2M_BANK", 300_000, 1_200, pct(1_200, "18")))
    add(Scenario("control_duplicate_refund", "CLAIM", "control", "One refund debited twice")
        .pay("2026-09-14T12:00", "UPI_P2M_BANK", 300_000, ref="P1")
        .refund("P1", "2026-09-15T12:00", 150_000, debits=2))
    sc = Scenario("control_small_merchant_priced_as_large", "CLAIM", "control",
                  "Small merchant charged the 0.90% large-merchant debit ceiling", turnover=12_00_000_00)
    sc.agreements = [("2024-01-01", "1.60", "0.40", "1.50")]
    add(sc.pay("2026-08-18T12:00", "CARD_DEBIT", 300_000, pct(300_000, "0.90"), pct(pct(300_000, "0.90"), "18")))
    add(Scenario("control_rental_after_return", "CLAIM", "control",
                 "Soundbox returned on 10 Jul, rental still debited in September")
        .device("2025-01-10", "2025-04-10", "2026-07-10").rent("2026-09"))
    add(Scenario("control_dropped_payment", "CLAIM", "control", "A payment missing from every batch")
        .pay("2026-09-16T12:00", "UPI_P2M_BANK", 450_000, drop=True)
        .pay("2026-09-16T12:10", "UPI_P2M_BANK", 20_000)
        .pay("2026-10-05T12:00", "UPI_P2M_BANK", 30_000))
    return s


def _row(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def write_dataset(out_dir: Path) -> Path:
    """Write every scenario as one red-team dataset in the observed format."""
    root = Path(out_dir) / "redteam"
    (root / HIDDEN).mkdir(parents=True, exist_ok=True)
    files = {n: [] for n in ("merchants", "agreements", "processor_config", "transactions",
                             "settlement_batches", "settlement_lines", "bank_credits", "network_signatures", "devices")}
    expectations = []
    utr = 0
    for i, sc in enumerate(scenarios(), start=1):
        mid = f"RT-{i:03d}"
        expectations.append({"merchant_id": mid, "scenario": sc.key, "expected": sc.expected,
                             "lookalike_of": sc.lookalike_of, "explanation": sc.explanation})
        files["merchants"].append({"merchant_id": mid, "legal_name": f"Red Team {sc.key}", "registered_mcc": sc.mcc,
                                   "city": "Mumbai", "acquirer_id": sc.acquirer, "fidelity": "FULL",
                                   "onboarded_on": "2024-01-01", "is_ecommerce_participant": sc.eco,
                                   "upi_class": sc.upi_class, "annual_turnover_paise": sc.turnover})
        for n, (signed, credit, debit, nb) in enumerate(sc.agreements, start=1):
            files["agreements"].append({"agreement_id": f"AGR-{mid}-{n}", "merchant_id": mid, "signed_on": signed,
                                        "settlement_sla_days": sc.sla, "card_credit_rate_percent": credit,
                                        "card_debit_rate_percent": debit, "netbanking_rate_percent": nb})
            files["processor_config"].append({"snapshot_id": f"CFG-{mid}-{n:02d}", "merchant_id": mid,
                                              "effective_from": signed, "pricing_mcc": sc.mcc,
                                              "card_credit_rate_percent": credit, "card_debit_rate_percent": debit,
                                              "netbanking_rate_percent": nb})
        refs: dict[str, str] = {}
        by_date: dict[date, list[dict]] = {}
        seq = 0
        for p in sc.payments:
            seq += 1
            txn_id = f"TXN-{mid}-{seq:04d}"
            if p["ref"]:
                refs[p["ref"]] = txn_id
            captured = datetime.fromisoformat(p["when"]).replace(tzinfo=IST)
            files["transactions"].append({"txn_id": txn_id, "merchant_id": mid, "kind": "PAYMENT",
                                          "status": "FAILED" if p["failed"] else "SUCCESS", "instrument": p["instrument"],
                                          "amount_paise": p["amount"], "captured_at": captured.isoformat(),
                                          "parent_txn_id": None, "order_ref": f"ORD-{seq}"})
            if p["failed"] or p["drop"]:
                continue
            cycle = captured.date() + timedelta(days=1) if captured.time() >= time(23, 0) else captured.date()
            settles = banking_add(cycle, sc.sla + p["late_days"])
            if settles > AS_OF:
                continue
            net = p["amount"] - p["mdr"] - p["gst"] - p["tcs"] - p["tds"]
            by_date.setdefault(settles, []).append({"txn_id": txn_id, "line_type": "PAYMENT", "instrument": p["instrument"],
                                                    "captured_at": captured.isoformat(), "gross_paise": p["amount"],
                                                    "mdr_paise": p["mdr"], "gst_paise": p["gst"], "tcs_paise": p["tcs"],
                                                    "tds_paise": p["tds"], "net_paise": net})
        for r in sc.refunds:
            seq += 1
            txn_id = f"RFD-{mid}-{seq:04d}"
            parent = next(t for t in files["transactions"] if t["txn_id"] == refs[r["parent"]])
            captured = datetime.fromisoformat(r["when"]).replace(tzinfo=IST)
            files["transactions"].append({"txn_id": txn_id, "merchant_id": mid, "kind": r["kind"], "status": "SUCCESS",
                                          "instrument": parent["instrument"], "amount_paise": r["amount"],
                                          "captured_at": captured.isoformat(), "parent_txn_id": parent["txn_id"],
                                          "order_ref": parent["order_ref"]})
            settles = banking_add(captured.date(), sc.sla)
            for extra in range(r["debits"]):
                day = banking_add(settles, extra)
                by_date.setdefault(day, []).append({"txn_id": txn_id, "line_type": r["kind"], "instrument": parent["instrument"],
                                                    "captured_at": captured.isoformat(), "gross_paise": -r["amount"],
                                                    "mdr_paise": 0, "gst_paise": 0, "tcs_paise": 0, "tds_paise": 0,
                                                    "net_paise": -r["amount"]})
        for n, dev in enumerate(sc.devices, start=1):
            files["devices"].append({"device_id": f"SBX-{mid}-{n}", "merchant_id": mid, "device_type": "SOUNDBOX",
                                     "monthly_rental_paise": 19_900, "activated_on": dev["activated_on"],
                                     "rental_free_until": dev["rental_free_until"], "returned_on": dev["returned_on"],
                                     "return_ref": f"RET-{mid}-{n}" if dev["returned_on"] else None})
        for dev_index, month in sc.rentals:
            first = date.fromisoformat(f"{month}-01")
            debit_on = first if first.weekday() < 5 else banking_add(first, 1)
            by_date.setdefault(debit_on, []).append({
                "txn_id": None, "line_type": "RENTAL", "instrument": None, "captured_at": None,
                "gross_paise": -19_900, "mdr_paise": 0, "gst_paise": 0, "tcs_paise": 0, "tds_paise": 0,
                "net_paise": -19_900, "charge_ref": f"SBX-{mid}-{dev_index + 1}:{month}"})
        for day in sorted(by_date):
            batch_id = f"STL-{mid}-{day:%Y%m%d}"
            lines = by_date[day]
            totals = {k: sum(l[k] for l in lines) for k in ("gross_paise", "mdr_paise", "gst_paise", "tcs_paise", "tds_paise", "net_paise")}
            batch_utr = None
            if totals["net_paise"] > 0:
                utr += 1
                batch_utr = f"UTRRT{utr:09d}"
                files["bank_credits"].append({"credit_id": f"CR-{mid}-{day:%Y%m%d}", "merchant_id": mid,
                                              "value_date": day.isoformat(), "amount_paise": totals["net_paise"],
                                              "utr": batch_utr, "narration": f"PG SETTLEMENT {batch_id}"})
            files["settlement_batches"].append({"batch_id": batch_id, "merchant_id": mid, "settlement_date": day.isoformat(),
                                                "cycle_dates": [day.isoformat()], "line_count": len(lines), **totals,
                                                "utr": batch_utr, "carried_forward": totals["net_paise"] < 0})
            for n, line in enumerate(lines, start=1):
                files["settlement_lines"].append({"line_id": f"{batch_id}-L{n:05d}", "batch_id": batch_id,
                                                  "merchant_id": mid, "ref_batch_id": None,
                                                  "charge_ref": line.get("charge_ref"),
                                                  **{k: v for k, v in line.items() if k != "charge_ref"}})

    manifest = {"dataset": "redteam", "as_of": AS_OF.isoformat(), "window_start": "2026-01-01", "seed": 0,
                "generator_version": "redteam-1", "months": 10, "files": {}}
    for name, rows in files.items():
        path = root / f"{name}.jsonl"
        path.write_text("".join(_row(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
        manifest["files"][f"{name}.jsonl"] = {"rows": len(rows)}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / HIDDEN / EXPECTATIONS).write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    return root
