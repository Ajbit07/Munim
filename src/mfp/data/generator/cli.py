"""Generate a seeded synthetic merchant environment.

    python generate.py --seed 42
    python generate.py --seed 42 --no-leakage
    python generate.py --seed 42 --merchants 250 --months 12

Observed artifacts are written to <out>/<dataset>/. Hidden ground truth is
written to <out>/<dataset>/_hidden/, which the observed-data store refuses to
open. The observed manifest deliberately omits the leakage flag and every
plant count.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from mfp.data.generator.network import background_signatures
from mfp.data.generator.params import GENERATOR_VERSION, GenerationParams
from mfp.data.generator.processor import charge_ledger
from mfp.data.generator.settlement import settle
from mfp.data.generator.tariff import ProcessorTariff
from mfp.data.generator.truth import HIDDEN_DIR, TRUTH_FILE, GroundTruth
from mfp.data.generator.world import build_ledger, build_worlds

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

OBSERVED_FILES = (
    "merchants.jsonl",
    "agreements.jsonl",
    "processor_config.jsonl",
    "transactions.jsonl",
    "settlement_batches.jsonl",
    "settlement_lines.jsonl",
    "bank_credits.jsonl",
    "network_signatures.jsonl",
    "devices.jsonl",
)


def _json_default(value):
    if isinstance(value, (date, Decimal)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def _dump(row) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=_json_default)


def _write_jsonl(path: Path, rows) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_dump(row) + "\n")
            count += 1
    return count


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dataset_name(params: GenerationParams) -> str:
    return f"seed-{params.seed}" + ("" if params.leakage else "-baseline")


def generate(params: GenerationParams, config_dir: Path = DEFAULT_CONFIG_DIR) -> Path:
    fee_rules = json.loads((config_dir / "fee_rules.json").read_text(encoding="utf-8"))
    settlement_rules = json.loads((config_dir / "settlement_rules.json").read_text(encoding="utf-8"))
    network = json.loads((config_dir / "network.json").read_text(encoding="utf-8"))
    tariff = ProcessorTariff(fee_rules)

    out = params.out_dir / dataset_name(params)
    hidden = out / HIDDEN_DIR
    hidden.mkdir(parents=True, exist_ok=True)

    worlds = build_worlds(params, tariff)
    truth = GroundTruth(params.seed, GENERATOR_VERSION, params.leakage, params.as_of, params.window_start)

    rows: dict[str, list] = {name: [] for name in OBSERVED_FILES}
    for world in worlds:
        rows["merchants.jsonl"].append(world.merchant)
        rows["agreements.jsonl"].append(world.agreement)
        rows["processor_config.jsonl"].extend(world.snapshots)
        rows["devices.jsonl"].extend(world.devices)
        truth.fault_profiles.extend(world.faults)
        if not world.is_full:
            continue
        ledger = build_ledger(params, world)
        charged = charge_ledger(params, world, ledger, tariff)
        settled = settle(params, world, ledger, charged.charges, settlement_rules, charged.correct)
        rows["transactions.jsonl"].extend(ledger)
        rows["settlement_batches.jsonl"].extend(settled.batches)
        rows["settlement_lines.jsonl"].extend(settled.lines)
        rows["bank_credits.jsonl"].extend(settled.credits)
        truth.plants.extend(charged.plants)
        truth.plants.extend(settled.plants)
        truth.lookalikes.extend(charged.lookalikes)
        truth.lookalikes.extend(settled.lookalikes)

    rows["network_signatures.jsonl"] = background_signatures(params, worlds, network["emitter_salt"])
    truth.plants.sort(key=lambda p: p.plant_id)

    manifest = {
        "dataset": out.name,
        "generator_version": GENERATOR_VERSION,
        "seed": params.seed,
        "as_of": params.as_of.isoformat(),
        "window_start": params.window_start.isoformat(),
        "months": params.months,
        "files": {},
    }
    for name in OBSERVED_FILES:
        path = out / name
        manifest["files"][name] = {"rows": _write_jsonl(path, rows[name]), "sha256": _sha256(path)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8", newline="\n")

    truth_path = hidden / TRUTH_FILE
    truth_path.write_text(json.dumps(truth.to_dict(), indent=1, sort_keys=True, default=_json_default) + "\n",
                          encoding="utf-8", newline="\n")
    return out


def parse_args(argv: list[str] | None = None) -> GenerationParams:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--merchants", type=int, default=250)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--as-of", type=date.fromisoformat, default=GenerationParams.as_of)
    parser.add_argument("--no-leakage", action="store_true")
    parser.add_argument("--hero-txn-per-day", type=int, default=GenerationParams.hero_txn_per_day)
    parser.add_argument("--cohort-size", type=int, default=GenerationParams.cohort_size)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "generated")
    args = parser.parse_args(argv)
    return replace(
        GenerationParams(),
        seed=args.seed, merchants=args.merchants, months=args.months, as_of=args.as_of,
        leakage=not args.no_leakage, hero_txn_per_day=args.hero_txn_per_day,
        cohort_size=args.cohort_size, out_dir=args.out,
    )


def main(argv: list[str] | None = None) -> int:
    params = parse_args(argv)
    out = generate(params)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print(f"dataset   {out}")
    print(f"window    {manifest['window_start']} -> {manifest['as_of']}")
    for name, info in manifest["files"].items():
        print(f"  {name:<28}{info['rows']:>10,}")
    summary = json.loads((out / HIDDEN_DIR / TRUTH_FILE).read_text(encoding="utf-8"))["summary"]
    print("\nhidden ground truth (operator view only; never read by production)")
    print(f"  plants        {summary['plants']:,}  {summary['plants_by_subtype']}")
    print(f"  claimable     Rs {Decimal(summary['claimable_paise']) / 100:,.2f}")
    print(f"  escalate-only Rs {Decimal(summary['escalate_paise']) / 100:,.2f}")
    print(f"  lookalikes    {summary['lookalikes']:,}")
    return 0
