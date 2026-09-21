"""Import a Paytm merchant settlement report and audit it with the same engines.

Column names follow Paytm's published formats:

  Dashboard settlement report (paytmpayments.com/docs/settlement-reports-on-dashboard):
    Transaction ID, Order ID, Transaction Date, Updated Date, Transaction Type,
    Status, Amount, Commission, GST, Settled Amount, Settled Date, UTR No.,
    Split Flag, Payment Mode
  Settlement Transaction Detail API (camelCase): transactionId, orderId,
    transactionDate, transactionType, status, amount, commission, gst,
    settledAmount, settledDate, utrNo, paymentMode, payoutId, mid

Optional extra columns, used when present: Card Network (tells RuPay debit from
other debit cards), TCS, TDS.

What the report cannot say (the merchant's category, agreed card rates,
previous-year turnover, e-commerce status, settlement timeline) comes from the
ops person uploading it. The report's UTRs stand in for the bank statement:
the chain is payment -> settlement line -> payout (UTR) as Paytm reported it.

Conservative by design. Rows the engines cannot judge are counted and set
aside, never guessed: pending payments, unsupported payment modes, other
deduction types (rental, recovery), refunds whose original payment is not in
the report, and rows whose settled amount does not equal amount - commission -
GST (- TCS - TDS) by more than a paisa.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from mfp.core.enums import Instrument
from mfp.schemas.ledger import (
    BankCredit,
    LineType,
    Merchant,
    MerchantAgreement,
    ProcessorConfigSnapshot,
    SettlementBatch,
    SettlementLine,
    Transaction,
    TxnKind,
)

IST = timezone(timedelta(hours=5, minutes=30))
MERCHANT_ID = "UPL-0001"

COLUMNS = {  # canonical -> accepted spellings, compared lower-case with non-alphanumerics removed
    "txn_id": ("transactionid", "txnid", "paytmtransactionid"),
    "order_id": ("orderid",),
    "txn_date": ("transactiondate", "txndate", "createddate", "transactiontime"),
    "updated_date": ("updateddate",),
    "txn_type": ("transactiontype", "txntype", "type"),
    "status": ("status", "transactionstatus"),
    "amount": ("amount", "txnamount", "transactionamount"),
    "commission": ("commission", "mdr", "commissionamount"),
    "gst": ("gst", "servicetax"),
    "settled_amount": ("settledamount", "netamount", "netsettledamount"),
    "settled_date": ("settleddate", "settlementdate", "payoutdate"),
    "utr": ("utrno", "utr", "utrnumber"),
    "payment_mode": ("paymentmode", "paymode", "mode"),
    "card_network": ("cardnetwork", "network", "cardscheme", "issuingnetwork"),
    "tcs": ("tcs",),
    "tds": ("tds",),
    "payout_id": ("payoutid",),
}
REQUIRED = ("txn_id", "txn_date", "amount", "payment_mode")

DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y %H:%M:%S",
                "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d %b %Y %H:%M:%S", "%d %b %Y", "%d-%b-%y", "%Y/%m/%d")


class ReportError(ValueError):
    """The file cannot be read as a Paytm settlement report; the message says why."""


@dataclass
class MerchantProfile:
    legal_name: str = "Uploaded merchant"
    city: str = "—"
    mcc: str = "5411"
    card_credit_rate_percent: str = "1.80"
    card_debit_rate_percent: str = "0.40"
    netbanking_rate_percent: str = "1.50"
    annual_turnover_paise: int = 1_00_00_000_00          # Rs 1 crore unless told otherwise
    is_ecommerce_participant: bool = False
    settlement_sla_days: int = 1
    upi_class: str = "P2M"


@dataclass
class ImportSummary:
    rows_read: int = 0
    payments: int = 0
    refunds: int = 0
    chargebacks: int = 0
    failed: int = 0
    settled_lines: int = 0
    payouts: int = 0
    set_aside: Counter = field(default_factory=Counter)
    first_date: str | None = None
    last_date: str | None = None
    columns_found: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["set_aside"] = dict(self.set_aside)
        return d


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _amount(raw: str) -> int | None:
    """'₹2,113.47', '(129.00)', '-129', '' -> paise, or None when blank."""
    text = (raw or "").strip().replace("₹", "").replace("Rs.", "").replace("Rs", "").replace(",", "").strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        value = int((Decimal(text) * 100).to_integral_value())
    except InvalidOperation:
        raise ReportError(f"'{raw}' is not an amount") from None
    return -value if negative else value


def _when(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    text = re.sub(r"\.\d+", "", text).replace("Z", "")
    text = re.sub(r"([+-]\d{2}:\d{2})$", "", text).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    raise ReportError(f"'{raw}' is not a date this importer understands")


def _instrument(mode: str, network: str) -> Instrument | None:
    m, n = _key(mode), _key(network)
    if m in ("upicreditcard", "upicc", "ccupi", "rupayccupi", "rupaycreditcardonupi", "creditcardonupi"):
        return Instrument.RUPAY_CC_ON_UPI
    if m in ("upilite",):
        return Instrument.UPI_LITE
    if m in ("upi", "upiintent", "upicollect", "upiqr", "bhimupi"):
        return Instrument.UPI_P2M_BANK
    if m in ("ppi", "wallet", "balance", "paytmwallet", "upippi", "ppionupi"):
        return Instrument.PPI_ON_UPI
    if m in ("dc", "debitcard", "debit"):
        return Instrument.RUPAY_DEBIT if "rupay" in n else Instrument.CARD_DEBIT
    if m in ("cc", "creditcard", "credit"):
        return Instrument.CARD_CREDIT
    if m in ("nb", "netbanking", "net banking"):
        return Instrument.NETBANKING
    return None


def _kind(raw: str) -> TxnKind | None:
    k = _key(raw)
    if k in ("", "acquiring", "sale", "payment", "capture", "paymentacquiring"):
        return TxnKind.PAYMENT
    if k.startswith("refund"):
        return TxnKind.REFUND
    if k.startswith("chargeback"):
        return TxnKind.CHARGEBACK
    if k.startswith("reversal"):
        return TxnKind.REVERSAL
    return None


def read_rows(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Rows keyed by canonical column name. Accepts comma, semicolon or tab separated text."""
    text = text.lstrip("﻿")
    if not text.strip():
        raise ReportError("The file is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, [])
    mapping: dict[int, str] = {}
    for i, name in enumerate(header):
        for canon, names in COLUMNS.items():
            if _key(name) in names and canon not in mapping.values():
                mapping[i] = canon
    missing = [c for c in REQUIRED if c not in mapping.values()]
    if missing:
        raise ReportError("Missing column(s): " + ", ".join(missing) + ". Found: " + ", ".join(header[:20]))
    rows = [{mapping[i]: v.strip() for i, v in enumerate(r) if i in mapping} for r in reader if any(x.strip() for x in r)]
    return rows, sorted(set(mapping.values()))


def import_report(text: str, profile: MerchantProfile, out_dir: Path) -> ImportSummary:
    """Write an observed dataset for one merchant from a Paytm report. Returns what was read and set aside."""
    rows, columns = read_rows(text)
    summary = ImportSummary(rows_read=len(rows), columns_found=columns)
    mid = MERCHANT_ID
    transactions: dict[str, Transaction] = {}
    pending_lines: list[tuple[date, str | None, dict]] = []
    payment_by_order: dict[str, str] = {}
    later: list[dict[str, str]] = []

    for n, row in enumerate(rows, start=1):
        kind = _kind(row.get("txn_type", ""))
        if kind is None:
            summary.set_aside[f"other deduction type ({row.get('txn_type') or 'blank'})"] += 1
            continue
        if kind is TxnKind.PAYMENT:
            _take(row, n, kind, None, profile, mid, transactions, pending_lines, payment_by_order, summary)
        else:
            later.append(row | {"_n": str(n)})
    for row in later:  # refunds and chargebacks need their original payment first
        kind = _kind(row.get("txn_type", ""))
        parent = payment_by_order.get(row.get("order_id", ""))
        if parent is None:
            summary.set_aside[f"{kind.lower()} whose original payment is not in the report"] += 1
            continue
        _take(row, int(row["_n"]), kind, parent, profile, mid, transactions, pending_lines, payment_by_order, summary)

    if not transactions:
        raise ReportError("No payments could be read from the file")
    captures = [t.captured_at.date() for t in transactions.values()]
    settled_days = [d for d, _, _ in pending_lines]
    first, as_of = min(captures), max(captures + settled_days)
    summary.first_date, summary.last_date = first.isoformat(), as_of.isoformat()

    by_batch: dict[tuple[date, str | None], list[dict]] = defaultdict(list)
    for settled_on, utr, line in pending_lines:
        by_batch[(settled_on, utr)].append(line)
    batches, lines, credits = [], [], []
    for (settled_on, utr), members in sorted(by_batch.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        batch_id = f"PTM-{settled_on:%Y%m%d}-{(utr or 'NOUTR')[-8:]}"
        totals = {k: sum(m[k] for m in members) for k in ("gross_paise", "mdr_paise", "gst_paise", "tcs_paise",
                                                          "tds_paise", "net_paise")}
        for i, m in enumerate(members, start=1):
            lines.append(SettlementLine(line_id=f"{batch_id}-L{i:05d}", batch_id=batch_id, merchant_id=mid, **m))
        batches.append(SettlementBatch(
            batch_id=batch_id, merchant_id=mid, settlement_date=settled_on,
            cycle_dates=tuple(sorted({m["captured_at"].date() for m in members})), line_count=len(members),
            utr=utr if totals["net_paise"] > 0 else None, carried_forward=totals["net_paise"] <= 0, **totals))
        if utr and totals["net_paise"] > 0:  # the report's payout stands in for the bank statement line
            credits.append(BankCredit(credit_id=f"CR-{batch_id}", merchant_id=mid, value_date=settled_on,
                                      amount_paise=totals["net_paise"], utr=utr,
                                      narration=f"PAYTM SETTLEMENT {utr} (from settlement report)"))
    summary.settled_lines, summary.payouts = len(lines), len(credits)

    merchant = Merchant(merchant_id=mid, legal_name=profile.legal_name, registered_mcc=profile.mcc, city=profile.city,
                        acquirer_id="PAYTM", fidelity="FULL", onboarded_on=first - timedelta(days=1),
                        is_ecommerce_participant=profile.is_ecommerce_participant, upi_class=profile.upi_class,
                        annual_turnover_paise=profile.annual_turnover_paise)
    rates = dict(card_credit_rate_percent=Decimal(profile.card_credit_rate_percent),
                 card_debit_rate_percent=Decimal(profile.card_debit_rate_percent),
                 netbanking_rate_percent=Decimal(profile.netbanking_rate_percent))
    agreement = MerchantAgreement(agreement_id=f"AGR-{mid}", merchant_id=mid, signed_on=first - timedelta(days=1),
                                  settlement_sla_days=profile.settlement_sla_days, **rates)
    config = ProcessorConfigSnapshot(snapshot_id=f"CFG-{mid}", merchant_id=mid, effective_from=first - timedelta(days=1),
                                     pricing_mcc=profile.mcc, **rates)
    files = {"merchants.jsonl": [merchant], "agreements.jsonl": [agreement], "processor_config.jsonl": [config],
             "transactions.jsonl": sorted(transactions.values(), key=lambda t: t.captured_at),
             "settlement_batches.jsonl": batches, "settlement_lines.jsonl": lines, "bank_credits.jsonl": credits,
             "network_signatures.jsonl": [], "devices.jsonl": []}
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, records in files.items():
        (out_dir / name).write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")
    manifest = {"as_of": as_of.isoformat(), "dataset": out_dir.name, "source": "Paytm settlement report upload",
                "files": {name: {"rows": len(records)} for name, records in files.items()},
                "import": summary.as_dict()}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return summary


def _take(row, n, kind, parent, profile, mid, transactions, pending_lines, payment_by_order, summary) -> None:
    status = _key(row.get("status", "success"))
    if status in ("pending", "initiated", "processing"):
        summary.set_aside["pending (not final yet)"] += 1
        return
    instrument = _instrument(row.get("payment_mode", ""), row.get("card_network", ""))
    if instrument is None:
        summary.set_aside[f"unsupported payment mode ({row.get('payment_mode') or 'blank'})"] += 1
        return
    captured = _when(row.get("txn_date", ""))
    amount = _amount(row.get("amount", ""))
    if captured is None or not amount:
        summary.set_aside["missing date or amount"] += 1
        return
    gross = abs(amount)
    txn_id = row["txn_id"] or f"ROW-{n}"
    failed = status in ("failure", "failed", "txnfailure", "txnfailed")
    known = transactions.get(txn_id)
    if known is not None and known.kind is kind and known.amount_paise == gross and not failed:
        # The same transaction settled again: a second debit or credit of one refund or payment.
        # Kept as a second settlement line, which is exactly what the reconciliation looks for.
        _settle(row, kind, txn_id, instrument, captured, gross, pending_lines, summary, transactions)
        return
    if known is not None:
        txn_id = f"{txn_id}-{n}"
    transactions[txn_id] = Transaction(
        txn_id=txn_id, merchant_id=mid, kind=kind, status="FAILED" if failed else "SUCCESS", instrument=instrument,
        amount_paise=gross, captured_at=captured, parent_txn_id=parent, order_ref=row.get("order_id") or txn_id)
    if failed:
        summary.failed += 1
        return
    if kind is TxnKind.PAYMENT:
        summary.payments += 1
        if row.get("order_id"):
            payment_by_order.setdefault(row["order_id"], txn_id)
    elif kind is TxnKind.REFUND:
        summary.refunds += 1
    else:
        summary.chargebacks += 1

    _settle(row, kind, txn_id, instrument, captured, gross, pending_lines, summary, transactions)


def _settle(row, kind, txn_id, instrument, captured, gross, pending_lines, summary, transactions) -> None:
    settled_on = _when(row.get("settled_date", ""))
    settled = _amount(row.get("settled_amount", ""))
    if settled_on is None or settled is None:
        return  # not settled yet: the reconciliation decides whether it is due or missing
    mdr, gst = abs(_amount(row.get("commission", "")) or 0), abs(_amount(row.get("gst", "")) or 0)
    tcs, tds = abs(_amount(row.get("tcs", "")) or 0), abs(_amount(row.get("tds", "")) or 0)
    sign = 1 if kind is TxnKind.PAYMENT else -1
    gross_signed = sign * gross
    net = gross_signed - mdr - gst - tcs - tds if sign > 0 else gross_signed
    if sign < 0:
        mdr = gst = tcs = tds = 0  # a refund or chargeback debits the amount; fees are not reversed
    if abs(net - (settled if sign > 0 else -abs(settled))) > 1:
        if not any(line["txn_id"] == txn_id for _, _, line in pending_lines):
            del transactions[txn_id]
        summary.set_aside["settled amount does not add up (other deductions)"] += 1
        return
    pending_lines.append((settled_on.date(), (row.get("utr") or "").strip() or None, {
        "line_type": LineType(kind.value), "txn_id": txn_id, "instrument": instrument, "captured_at": captured,
        "gross_paise": gross_signed, "mdr_paise": mdr, "gst_paise": gst, "tcs_paise": tcs, "tds_paise": tds,
        "net_paise": net}))
