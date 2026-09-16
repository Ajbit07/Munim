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
