"""Block 1: the synthetic merchant environment.

The baseline assertions below are written directly from docs/FEE_RULES.md,
not by calling the generator's tariff. If the processor simulation drifted
away from the published rules, these would catch it.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from mfp.data.generator.cli import OBSERVED_FILES, generate
from mfp.data.generator.params import GenerationParams
from mfp.data.generator.truth import HIDDEN_DIR, TRUTH_FILE
from mfp.data.store import ObservedDataset, RestrictedPathError

SMALL = GenerationParams(
    seed=11, merchants=8, months=3, hero_txn_per_day=30, cohort_size=5, cohort_txn_per_day=(10, 15)
)
RS_2000 = 200_000
UPI_MDR_START = date(2026, 10, 15)
RUPAY_CC_START = date(2026, 6, 1)
CARDS = {"CARD_DEBIT", "CARD_CREDIT", "RUPAY_DEBIT"}
RUPAY_CC_TABLE = {"5411": "1.10", "5499": "1.10", "5541": "0.75", "5542": "0.75",
                  "8220": "0.70", "4812": "0.70", "4814": "0.70"}


def half_up(paise: int, percent: str | Decimal) -> int:
    return int((Decimal(paise) * Decimal(percent) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def digest(root: Path) -> dict[str, str]:
    files = [root / name for name in OBSERVED_FILES] + [root / "manifest.json", root / HIDDEN_DIR / TRUTH_FILE]
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    base = tmp_path_factory.mktemp("gen")
    leaky = generate(replace(SMALL, out_dir=base / "a"))
    leaky_again = generate(replace(SMALL, out_dir=base / "b"))
    clean = generate(replace(SMALL, out_dir=base / "a", leakage=False))
    other_seed = generate(replace(SMALL, out_dir=base / "c", seed=12))
    return {"leaky": leaky, "again": leaky_again, "clean": clean, "other": other_seed}


@pytest.fixture(scope="module")
def leaky(runs):
    return ObservedDataset(runs["leaky"])


@pytest.fixture(scope="module")
def clean(runs):
    return ObservedDataset(runs["clean"])


def truth(root: Path) -> dict:
    return json.loads((root / HIDDEN_DIR / TRUTH_FILE).read_text(encoding="utf-8"))


# -- reproducibility ----------------------------------------------------


def test_same_seed_reproduces_every_file_byte_for_byte(runs):
    assert digest(runs["leaky"]) == digest(runs["again"])


def test_different_seed_produces_a_different_ledger(runs):
    a = hashlib.sha256((runs["leaky"] / "transactions.jsonl").read_bytes()).hexdigest()
    b = hashlib.sha256((runs["other"] / "transactions.jsonl").read_bytes()).hexdigest()
    assert a != b


def test_baseline_and_leaky_share_the_identical_ledger(runs):
    """Leakage changes only what was charged, never what was transacted."""
    for name in ("merchants.jsonl", "agreements.jsonl", "transactions.jsonl"):
        assert (runs["leaky"] / name).read_bytes() == (runs["clean"] / name).read_bytes(), name
    assert (runs["leaky"] / "settlement_lines.jsonl").read_bytes() != (
        runs["clean"] / "settlement_lines.jsonl"
    ).read_bytes()


# -- the hidden boundary ------------------------------------------------


def test_manifest_reveals_nothing_about_leakage(runs):
    for key in ("leaky", "clean"):
        text = (runs[key] / "manifest.json").read_text(encoding="utf-8").lower()
        for forbidden in ("leak", "plant", "fault", "truth", "_hidden", "lookalike"):
            assert forbidden not in text, f"manifest mentions {forbidden!r}"


def test_store_refuses_to_open_hidden_material(runs):
    with pytest.raises(RestrictedPathError):
        ObservedDataset(runs["leaky"] / HIDDEN_DIR)


def test_hidden_truth_is_not_among_observed_files(runs):
    observed = {p.name for p in runs["leaky"].iterdir() if p.is_file()}
    assert TRUTH_FILE not in observed


# -- structure and batching ---------------------------------------------


def test_every_artifact_validates_against_its_schema(leaky):
    assert len(leaky.merchants) == SMALL.merchants
    assert leaky.transactions and leaky.settlement_lines and leaky.bank_credits
    # Loading is validation: each row passed its Pydantic model, including
    # the net == gross - deductions check on every settlement line.


def test_background_merchants_have_no_ledger(leaky):
    full = {m.merchant_id for m in leaky.merchants if m.fidelity == "FULL"}
    assert len(full) == SMALL.full_fidelity_count
    assert {t.merchant_id for t in leaky.transactions} <= full


def test_batch_totals_equal_the_sum_of_their_lines(leaky):
    for batch in leaky.settlement_batches:
        lines = leaky.lines_by_batch[batch.batch_id]
        assert batch.line_count == len(lines)
        for field in ("gross_paise", "mdr_paise", "gst_paise", "tcs_paise", "tds_paise", "net_paise"):
            assert getattr(batch, field) == sum(getattr(line, field) for line in lines), (batch.batch_id, field)


def test_settlements_land_only_on_banking_days_within_the_window(leaky):
    for batch in leaky.settlement_batches:
        assert batch.settlement_date.weekday() < 5
        assert batch.settlement_date <= SMALL.as_of


def test_one_bank_credit_per_positive_batch_matched_by_utr(leaky):
    credits_by_utr = {c.utr: c for c in leaky.bank_credits}
    for batch in leaky.settlement_batches:
        if batch.net_paise > 0:
            credit = credits_by_utr[batch.utr]
            assert credit.amount_paise == batch.net_paise
            assert credit.value_date == batch.settlement_date
        else:
            assert batch.utr is None


def test_bank_statement_contains_credits_that_are_not_settlements(leaky):
    batch_utrs = {b.utr for b in leaky.settlement_batches if b.utr}
    assert any(c.utr not in batch_utrs for c in leaky.bank_credits)


def test_batches_aggregate_multiple_capture_cycles(leaky):
    """Weekend captures collapse into Monday's batch."""
    assert any(len(b.cycle_dates) > 1 for b in leaky.settlement_batches)


def test_capture_after_cutoff_belongs_to_the_next_cycle(leaky):
    cycles = {b.batch_id: set(b.cycle_dates) for b in leaky.settlement_batches}
    checked = 0
    for line in leaky.settlement_lines:
        if line.captured_at and line.captured_at.hour >= 23:
            assert line.captured_at.date() + timedelta(days=1) in cycles[line.batch_id]
            checked += 1
    assert checked > 0


def test_failed_payments_never_settle_and_successful_ones_settle_once(clean):
    counts: dict[str, int] = defaultdict(int)
    for line in clean.settlement_lines:
        if line.txn_id:
            counts[line.txn_id] += 1
    assert all(n == 1 for n in counts.values())
    horizon = SMALL.as_of - timedelta(days=7)
    for txn in clean.transactions:
        if txn.status == "FAILED":
            assert txn.txn_id not in counts
        elif txn.kind == "PAYMENT" and txn.captured_at.date() < horizon:
            assert counts[txn.txn_id] == 1, txn.txn_id


# -- the clean baseline obeys the published rules -----------------------


def test_clean_baseline_has_no_plants_and_no_faults(runs):
    t = truth(runs["clean"])
    assert t["plants"] == []
    assert t["fault_profiles"] == []
    assert t["summary"]["claimable_paise"] == 0


def test_baseline_never_charges_mdr_on_bank_upi_before_15_october(clean):
    for line in clean.settlement_lines:
        if line.instrument in ("UPI_P2M_BANK", "UPI_LITE") and line.captured_at.date() < UPI_MDR_START:
            assert line.mdr_paise == 0 and line.gst_paise == 0, line.line_id


def test_baseline_never_charges_mdr_on_rupay_debit(clean):
    for line in clean.settlement_lines:
        if line.instrument == "RUPAY_DEBIT":
            assert line.mdr_paise == 0, line.line_id


def test_baseline_applies_the_pa_gst_exemption_on_small_card_payments(clean):
    for line in clean.settlement_lines:
        if line.line_type == "PAYMENT" and line.instrument in CARDS and line.gross_paise <= RS_2000:
            assert line.gst_paise == 0, line.line_id


def test_baseline_gst_is_18_percent_of_mdr_wherever_it_applies(clean):
    checked = 0
    for line in clean.settlement_lines:
        exempt = line.instrument in CARDS and line.gross_paise <= RS_2000
        if line.mdr_paise > 0 and not exempt:
            assert line.gst_paise == half_up(line.mdr_paise, "18"), line.line_id
            checked += 1
    assert checked > 0


def test_baseline_rupay_cc_on_upi_matches_the_mcc_table(clean):
    mcc = {m.merchant_id: m.registered_mcc for m in clean.merchants}
    checked = 0
    for line in clean.settlement_lines:
        if line.instrument != "RUPAY_CC_ON_UPI" or line.line_type != "PAYMENT":
            continue
        if line.gross_paise <= RS_2000:
            assert line.mdr_paise == 0, line.line_id
        elif line.captured_at.date() >= RUPAY_CC_START:
            rate = RUPAY_CC_TABLE.get(mcc[line.merchant_id], "1.75")
            assert line.mdr_paise == half_up(line.gross_paise, rate), line.line_id
            checked += 1
    assert checked > 0


def test_baseline_card_mdr_matches_the_signed_agreement(clean):
    agreements = {a.merchant_id: a for a in clean.agreements}
    for line in clean.settlement_lines:
        if line.line_type == "PAYMENT" and line.instrument == "CARD_CREDIT":
            rate = agreements[line.merchant_id].card_credit_rate_percent
            assert line.mdr_paise == half_up(line.gross_paise, rate), line.line_id


def test_baseline_deducts_no_tcs_or_tds_from_non_ecommerce_merchants(clean):
    eco = {m.merchant_id: m.is_ecommerce_participant for m in clean.merchants}
    for line in clean.settlement_lines:
        if not eco[line.merchant_id]:
            assert line.tcs_paise == 0 and line.tds_paise == 0, line.line_id


def test_baseline_never_passes_ppi_interchange_through(clean):
    for line in clean.settlement_lines:
        if line.instrument == "PPI_ON_UPI":
            assert line.mdr_paise == 0, line.line_id


def test_baseline_still_contains_legitimate_lookalikes(runs):
    """The baseline must test false positives, not merely be empty."""
    assert truth(runs["clean"])["summary"]["lookalikes"] > 0


# -- planted leakage ----------------------------------------------------


def test_hero_carries_its_documented_fault_profile(runs, leaky):
    t = truth(runs["leaky"])
    hero_subtypes = {p["subtype"] for p in t["plants"] if p["merchant_id"] == "MER-0001"}
    assert {"L1a", "L1b", "L2b", "L2e"} <= hero_subtypes
    snaps = [s for s in leaky.processor_config if s.merchant_id == "MER-0001"]
    assert any(s.pricing_mcc == "5999" and s.effective_from == RUPAY_CC_START for s in snaps)


def test_every_plant_points_at_observed_evidence(runs, leaky):
    lines_by_txn: dict[str, list] = defaultdict(list)
    for line in leaky.settlement_lines:
        if line.txn_id:
            lines_by_txn[line.txn_id].append(line)
    plants = truth(runs["leaky"])["plants"]
    components_by_txn: dict[str, set] = defaultdict(set)
    for plant in plants:
        components_by_txn[plant["txn_id"]].add(plant["component"])
    txns = leaky.transactions_by_id
    for plant in plants:
        assert plant["txn_id"] in txns
        lines = lines_by_txn.get(plant["txn_id"], [])
        component = plant["component"]
        if component == "SETTLEMENT":
            assert lines == [], "a dropped payment must be absent from every batch"
            continue
        if component == "REFUND_DEBIT":
            assert len(lines) == 2 and lines[0].batch_id != lines[1].batch_id
            continue
        if not lines:
            continue  # captured near as_of; not settled yet
        (line,) = lines
        if component == "TAX":
            assert plant["charged_paise"] == line.tcs_paise + line.tds_paise
        elif component == "GST":
            assert plant["charged_paise"] == line.gst_paise
        elif component == "MDR" and components_by_txn[plant["txn_id"]] == {"MDR"}:
            assert plant["charged_paise"] == line.mdr_paise + line.gst_paise


def test_early_upi_mdr_plants_are_fully_recoverable_and_correctly_dated(runs, leaky):
    lines_by_txn = {line.txn_id: line for line in leaky.settlement_lines if line.txn_id}
    plants = [p for p in truth(runs["leaky"])["plants"] if p["subtype"] == "L2b"]
    assert plants
    for plant in plants:
        assert date.fromisoformat(plant["captured_on"]) >= date(2026, 8, 20)
        assert plant["correct_paise"] == 0
        assert plant["amount_paise"] == plant["charged_paise"]
        line = lines_by_txn.get(plant["txn_id"])
        if line:
            assert line.gross_paise > RS_2000
            # Independent check against the published NPCI formula.
            assert line.mdr_paise == min(half_up(line.gross_paise, "0.4"), 30_000)


def test_ppi_plants_escalate_and_never_claim_a_known_amount(runs):
    plants = [p for p in truth(runs["leaky"])["plants"] if p["subtype"] == "L2e"]
    assert plants
    assert all(p["expected_action"] == "ESCALATE" and p["correct_paise"] is None for p in plants)


def test_claim_plants_are_positive_and_consistent(runs):
    for plant in truth(runs["leaky"])["plants"]:
        assert plant["amount_paise"] > 0
        if plant["component"] == "SETTLEMENT":
            assert plant["amount_paise"] == plant["correct_paise"] - plant["charged_paise"]
        elif plant["expected_action"] == "CLAIM":
            assert plant["amount_paise"] == plant["charged_paise"] - plant["correct_paise"]


def test_all_six_discrepancy_classes_are_planted_at_scale():
    """Uses the committed seed-42 dataset when present; skipped otherwise."""
    path = Path(__file__).resolve().parents[1] / "data" / "generated" / "seed-42" / HIDDEN_DIR / TRUTH_FILE
    if not path.exists():
        pytest.skip("seed-42 dataset not generated")
    kinds = {p["discrepancy_type"] for p in json.loads(path.read_text(encoding="utf-8"))["plants"]}
    assert kinds == {"L1_WRONG_MDR_BAND", "L2_NIL_MDR_VIOLATION", "L3_GST_BASE_ERROR",
                     "L4_TAX_MISAPPLICATION", "L5_ORPHAN_REFUND", "L6_UNSETTLED_TRANSACTION"}


def test_network_signatures_carry_no_merchant_identity(leaky):
    ids = {m.merchant_id for m in leaky.merchants}
    raw = (Path(leaky.root) / "network_signatures.jsonl").read_text(encoding="utf-8")
    assert raw, "background merchants should have published signatures"
    assert not any(mid in raw for mid in ids)
    assert "TXN-" not in raw
