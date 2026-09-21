"""Write one merchant's observed records as a Paytm-format settlement report (CSV).

    python tools/export_paytm_report.py [--seed 42] [--merchant MER-0001] [--out samples/]

Columns are Paytm's dashboard settlement report columns, plus the optional
Card Network, TCS and TDS columns the importer understands. Unsettled
payments are included with blank settlement fields, as in a transaction
report. Rental debits are written as their own rows (the importer sets them
aside). The result is a realistic file to upload into the ops console.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mfp.data.store import MerchantIndex, ObservedDataset  # noqa: E402

HEADER = ["Transaction ID", "Order ID", "Transaction Date", "Updated Date", "Transaction Type", "Status", "Amount",
          "Commission", "GST", "Settled Amount", "Settled Date", "UTR No.", "Split Flag", "Payment Mode",
          "Card Network", "TCS", "TDS"]
MODE = {"UPI_P2M_BANK": ("UPI", ""), "UPI_LITE": ("UPI_LITE", ""), "RUPAY_CC_ON_UPI": ("UPI_CREDIT_CARD", "RUPAY"),
        "PPI_ON_UPI": ("PPI", ""), "RUPAY_DEBIT": ("DC", "RUPAY"), "CARD_DEBIT": ("DC", "VISA"),
        "CARD_CREDIT": ("CC", "VISA"), "NETBANKING": ("NB", "")}
TYPE = {"PAYMENT": "ACQUIRING", "REFUND": "REFUND", "CHARGEBACK": "CHARGEBACK", "REVERSAL": "REVERSAL"}


def rupees(paise: int) -> str:
    return f"{paise / 100:.2f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--merchant", default="MER-0001")
    parser.add_argument("--out", type=Path, default=ROOT / "samples")
    args = parser.parse_args()
    dataset = ObservedDataset(ROOT / "data" / "generated" / f"seed-{args.seed}")
    view = MerchantIndex(dataset).view(args.merchant)
    through = dataset.manifest["as_of"]
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"paytm_settlement_report_{args.merchant}_seed{args.seed}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for t in view.transactions:
            mode, network = MODE[str(t.instrument)]
            when = t.captured_at.strftime("%Y-%m-%d %H:%M:%S")
            own = [ln for ln in view.lines_by_txn.get(t.txn_id, []) if str(ln.line_type) == str(t.kind)]
            # One row per settlement line: a refund debited twice appears twice, as it would in the report.
            for line in own or [None]:
                batch = view.batches_by_id.get(line.batch_id) if line else None
                settled = batch is not None and batch.settlement_date.isoformat() <= through
                writer.writerow([
                    t.txn_id, t.order_ref, when, when, TYPE[str(t.kind)],
                    "SUCCESS" if str(t.status) == "SUCCESS" else "FAILURE", rupees(t.amount_paise),
                    rupees(line.mdr_paise) if settled else "", rupees(line.gst_paise) if settled else "",
                    rupees(line.net_paise) if settled else "", batch.settlement_date.isoformat() if settled else "",
                    batch.utr or "" if settled else "", "N", mode, network,
                    rupees(line.tcs_paise) if settled else "", rupees(line.tds_paise) if settled else ""])
        for line in view.settlement_lines:
            if str(line.line_type) == "RENTAL":
                batch = view.batches_by_id[line.batch_id]
                writer.writerow([line.charge_ref, "", f"{batch.settlement_date} 00:00:00", "", "RENTAL", "SUCCESS",
                                 rupees(-line.gross_paise), "0.00", "0.00", rupees(line.net_paise),
                                 batch.settlement_date.isoformat(), batch.utr or "", "N", "", "", "", ""])
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
