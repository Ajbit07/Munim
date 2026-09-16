"""Read-only access to observed artifacts.

This is the only door production code uses to reach generated data. It knows
a fixed whitelist of observed files and refuses any path with a component
beginning with an underscore, which is where hidden evaluation material lives.
It never imports the generator.
"""

from __future__ import annotations

import json
from collections import defaultdict
from functools import cached_property
from pathlib import Path

from mfp.schemas.network import NetworkSignature
from mfp.schemas.ledger import (
    BankCredit,
    Merchant,
    MerchantAgreement,
    ProcessorConfigSnapshot,
    SettlementBatch,
    SettlementLine,
    Transaction,
)

OBSERVED = {
    "merchants": ("merchants.jsonl", Merchant),
    "agreements": ("agreements.jsonl", MerchantAgreement),
    "processor_config": ("processor_config.jsonl", ProcessorConfigSnapshot),
    "transactions": ("transactions.jsonl", Transaction),
    "settlement_batches": ("settlement_batches.jsonl", SettlementBatch),
    "settlement_lines": ("settlement_lines.jsonl", SettlementLine),
    "bank_credits": ("bank_credits.jsonl", BankCredit),
    "network_signatures": ("network_signatures.jsonl", NetworkSignature),
}


class RestrictedPathError(PermissionError):
    """Raised when production code tries to open hidden material."""


def _guard(path: Path) -> Path:
    # Checks the path and its parent rather than every ancestor, so a dataset
    # stored under an unrelated underscore directory elsewhere is still usable.
    resolved = Path(path).resolve()
    if resolved.name.startswith("_") or resolved.parent.name.startswith("_"):
        raise RestrictedPathError(
            f"{resolved} is inside a restricted directory; observed data never lives under '_'"
        )
    return resolved


class ObservedDataset:
    def __init__(self, root: Path) -> None:
        self.root = _guard(Path(root))
        if not (self.root / "manifest.json").exists():
            raise FileNotFoundError(f"no manifest.json in {self.root}")

    @cached_property
    def manifest(self) -> dict:
        return json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))

    def _load(self, key: str) -> tuple:
        filename, model = OBSERVED[key]
        path = _guard(self.root / filename)
        with path.open("r", encoding="utf-8") as handle:
            return tuple(model.model_validate_json(line) for line in handle if line.strip())

    @cached_property
    def merchants(self) -> tuple[Merchant, ...]:
        return self._load("merchants")

    @cached_property
    def agreements(self) -> tuple[MerchantAgreement, ...]:
        return self._load("agreements")

    @cached_property
    def processor_config(self) -> tuple[ProcessorConfigSnapshot, ...]:
        return self._load("processor_config")

    @cached_property
    def transactions(self) -> tuple[Transaction, ...]:
        return self._load("transactions")

    @cached_property
    def settlement_batches(self) -> tuple[SettlementBatch, ...]:
        return self._load("settlement_batches")

    @cached_property
    def settlement_lines(self) -> tuple[SettlementLine, ...]:
        return self._load("settlement_lines")

    @cached_property
    def bank_credits(self) -> tuple[BankCredit, ...]:
        return self._load("bank_credits")

    @cached_property
    def network_signatures(self) -> tuple[NetworkSignature, ...]:
        return self._load("network_signatures")

    # -- convenience indexes ---------------------------------------------

    @cached_property
    def lines_by_batch(self) -> dict[str, tuple[SettlementLine, ...]]:
        grouped: dict[str, list[SettlementLine]] = defaultdict(list)
        for line in self.settlement_lines:
            grouped[line.batch_id].append(line)
        return {k: tuple(v) for k, v in grouped.items()}

    @cached_property
    def transactions_by_id(self) -> dict[str, Transaction]:
        return {t.txn_id: t for t in self.transactions}

    def merchant(self, merchant_id: str) -> Merchant:
        for m in self.merchants:
            if m.merchant_id == merchant_id:
                return m
        raise KeyError(merchant_id)


# -- per-merchant views ---------------------------------------------------

_MERCHANT_KEY = '"merchant_id":"'


class MerchantView:
    """One merchant's observed records, loaded without validating anyone else's.

    Loading 385k settlement lines through Pydantic for every merchant would
    dominate runtime. Rows are grouped by merchant once from raw text, and
    validated only for the merchant being examined.
    """

    def __init__(self, merchant: Merchant, agreements, processor_config, transactions,
                 settlement_batches, settlement_lines, bank_credits) -> None:
        self.merchant = merchant
        self.merchant_id = merchant.merchant_id
        self.agreements = list(agreements)
        self.processor_config = sorted(processor_config, key=lambda s: s.effective_from)
        self.transactions = list(transactions)
        self.settlement_batches = sorted(settlement_batches, key=lambda b: b.settlement_date)
        self.settlement_lines = list(settlement_lines)
        self.bank_credits = list(bank_credits)
        self.transactions_by_id = {t.txn_id: t for t in self.transactions}
        self.batches_by_id = {b.batch_id: b for b in self.settlement_batches}
        self.credits_by_utr = {c.utr: c for c in self.bank_credits}
        self.lines_by_txn: dict[str, list[SettlementLine]] = defaultdict(list)
        self.lines_by_batch: dict[str, list[SettlementLine]] = defaultdict(list)
        for line in self.settlement_lines:
            self.lines_by_batch[line.batch_id].append(line)
            if line.txn_id:
                self.lines_by_txn[line.txn_id].append(line)

    def snapshot_on(self, on):
        current = None
        for snap in self.processor_config:
            if snap.effective_from <= on:
                current = snap
        return current


class MerchantIndex:
    """Groups a dataset's raw rows by merchant in a single pass per file."""

    GROUPED = ("agreements", "processor_config", "transactions", "settlement_batches",
               "settlement_lines", "bank_credits")

    def __init__(self, dataset: ObservedDataset) -> None:
        self.dataset = dataset
        self._raw: dict[str, dict[str, list[str]]] = {}

    def _group(self, key: str) -> dict[str, list[str]]:
        if key not in self._raw:
            filename, _ = OBSERVED[key]
            grouped: dict[str, list[str]] = defaultdict(list)
            with _guard(self.dataset.root / filename).open("r", encoding="utf-8") as handle:
                for line in handle:
                    start = line.find(_MERCHANT_KEY)
                    if start < 0:
                        continue
                    start += len(_MERCHANT_KEY)
                    grouped[line[start:line.index('"', start)]].append(line)
            self._raw[key] = grouped
        return self._raw[key]

    def warm(self) -> None:
        for key in self.GROUPED:
            self._group(key)

    def merchant_ids(self, fidelity: str | None = "FULL") -> list[str]:
        return [m.merchant_id for m in self.dataset.merchants if fidelity is None or m.fidelity == fidelity]

    def view(self, merchant_id: str) -> MerchantView:
        def rows(key):
            _, model = OBSERVED[key]
            return [model.model_validate_json(raw) for raw in self._group(key).get(merchant_id, [])]
        return MerchantView(
            self.dataset.merchant(merchant_id), rows("agreements"), rows("processor_config"),
            rows("transactions"), rows("settlement_batches"), rows("settlement_lines"), rows("bank_credits"),
        )
