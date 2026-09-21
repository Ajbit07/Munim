"""The merchant master: who an uploaded report belongs to, and that merchant's terms.

In production this is a lookup by MID into Paytm's merchant records (KYC
category, pricing plan and its amendments, previous-year turnover, e-commerce
status, rented devices). Here the stand-in is the merchant records of the
generated datasets. A report is matched by its MID column when it has one,
otherwise by recognising its transaction IDs. Nothing is typed by ops.

The agreed rates come from the merchant's agreement, never from the report:
reading the "agreed" rate off the charges being audited would make a year of
overcharging look normal.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from mfp.data.store import MerchantIndex, ObservedDataset
from mfp.schemas.ledger import Device, Merchant, MerchantAgreement, ProcessorConfigSnapshot


@dataclass
class MasterRecord:
    merchant: Merchant
    agreements: list[MerchantAgreement]
    processor_config: list[ProcessorConfigSnapshot]
    devices: list[Device] = field(default_factory=list)
    source: str = ""          # which records it came from
    matched_by: str = ""      # "merchant ID" | "N of M transaction IDs"

    def summary(self) -> dict:
        latest = max(self.agreements, key=lambda a: a.signed_on) if self.agreements else None
        return {
            "merchant_id": self.merchant.merchant_id, "legal_name": self.merchant.legal_name,
            "city": self.merchant.city, "mcc": self.merchant.registered_mcc,
            "annual_turnover_paise": self.merchant.annual_turnover_paise,
            "is_ecommerce_participant": self.merchant.is_ecommerce_participant,
            "agreements": len(self.agreements), "devices": len(self.devices),
            "card_credit_rate_percent": str(latest.card_credit_rate_percent) if latest else None,
            "card_debit_rate_percent": str(latest.card_debit_rate_percent) if latest else None,
            "netbanking_rate_percent": str(latest.netbanking_rate_percent) if latest else None,
            "settlement_sla_days": latest.settlement_sla_days if latest else None,
            "source": self.source, "matched_by": self.matched_by,
        }


class MerchantMaster:
    def __init__(self, roots: list[Path]) -> None:
        self.roots = [r for r in roots if (r / "manifest.json").exists()]

    def find(self, mid: str | None, txn_ids: list[str]) -> MasterRecord | None:
        """The merchant a report belongs to, or None when no record matches."""
        for root in self.roots:
            dataset = ObservedDataset(root)
            known = {m.merchant_id for m in dataset.merchants if m.fidelity == "FULL"}
            if mid and mid in known:
                return self._record(dataset, mid, root.name, "merchant ID")
        sample = set(txn_ids[:200])
        if not sample:
            return None
        for root in self.roots:
            hits: Counter = Counter()
            with (root / "transactions.jsonl").open(encoding="utf-8") as handle:
                for raw in handle:
                    txn = re.search(r'"txn_id":"([^"]+)"', raw)
                    if txn and txn.group(1) in sample:
                        hits[re.search(r'"merchant_id":"([^"]+)"', raw).group(1)] += 1
            if hits:
                merchant_id, n = hits.most_common(1)[0]
                if n >= max(3, len(sample) // 2):   # most of the report, not a stray collision
                    return self._record(ObservedDataset(root), merchant_id, root.name,
                                        f"{n} of {len(sample)} transaction IDs")
        return None

    @staticmethod
    def _record(dataset: ObservedDataset, merchant_id: str, source: str, matched_by: str) -> MasterRecord:
        view = MerchantIndex(dataset).view(merchant_id)
        return MasterRecord(view.merchant, list(view.agreements), list(view.processor_config), list(view.devices),
                            source, matched_by)


def default_master(repo: Path) -> MerchantMaster:
    generated = repo / "data" / "generated"
    roots = sorted((p for p in generated.glob("seed-*") if not p.name.endswith("-baseline")),
                   key=lambda p: (p.name != "seed-42", p.name))
    return MerchantMaster(roots)
