"""Firewall tests.

Two halves, and both matter:

  1. The real source tree is clean.
  2. CANARY tests -- we synthesize a module that deliberately violates each
     firewall and assert the checker catches it. Without these, a checker that
     silently stopped working would look identical to a clean codebase.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_firewall import scan  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src"


# -- the real tree ------------------------------------------------------


def test_source_tree_has_no_firewall_violations():
    violations = scan(SRC)
    assert violations == [], "\n" + "\n".join(str(v) for v in violations)


# -- canaries -----------------------------------------------------------


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_canary_proof_importing_generator_is_caught(tmp_path):
    _write(
        tmp_path,
        "mfp/proof/engine.py",
        "from mfp.data.generator.fees import compute_mdr\n",
    )
    violations = scan(tmp_path)
    assert any(v.firewall == "PROOF_INDEPENDENCE" for v in violations), (
        "the Proof Engine imported the generator's fee logic and the checker "
        "did not notice; every proof result would be circular"
    )


def test_canary_proof_importing_generator_via_plain_import_is_caught(tmp_path):
    _write(tmp_path, "mfp/proof/other.py", "import mfp.data.generator.fees\n")
    assert any(v.firewall == "PROOF_INDEPENDENCE" for v in scan(tmp_path))


def test_canary_non_proof_module_may_import_generator(tmp_path):
    """The firewall is targeted, not blanket. Evaluation and tooling may."""
    _write(tmp_path, "mfp/evaluation/harness.py", "import mfp.data.generator.fees\n")
    assert not any(v.firewall == "PROOF_INDEPENDENCE" for v in scan(tmp_path))


def test_canary_production_reading_ground_truth_is_caught(tmp_path):
    _write(
        tmp_path,
        "mfp/agents/investigation.py",
        'PLANTED = "data/ground_truth.json"\n',
    )
    violations = scan(tmp_path)
    assert any(v.firewall == "GROUND_TRUTH_ISOLATION" for v in violations), (
        "a production module referenced ground truth and the checker did not "
        "notice; every detection metric would be meaningless"
    )


def test_canary_production_importing_ground_truth_module_is_caught(tmp_path):
    _write(tmp_path, "mfp/proof/engine.py", "from mfp.data.ground_truth import planted\n")
    assert any(v.firewall == "GROUND_TRUTH_ISOLATION" for v in scan(tmp_path))


def test_canary_evaluation_may_read_ground_truth(tmp_path):
    _write(
        tmp_path,
        "mfp/evaluation/miss_report.py",
        'TRUTH = "data/ground_truth.json"\n',
    )
    assert not any(v.firewall == "GROUND_TRUTH_ISOLATION" for v in scan(tmp_path))


def test_canary_wall_clock_is_caught(tmp_path):
    _write(
        tmp_path,
        "mfp/agents/monitor.py",
        "from datetime import datetime\n\n\ndef tick():\n    return datetime.now()\n",
    )
    violations = scan(tmp_path)
    assert any(v.firewall == "NO_WALL_CLOCK" for v in violations), (
        "a module called datetime.now() and the checker did not notice; the "
        "backfill and the follow-up wait would stop being demonstrable"
    )


def test_canary_date_today_is_caught(tmp_path):
    _write(tmp_path, "mfp/rules/engine.py", "from datetime import date\nD = date.today()\n")
    assert any(v.firewall == "NO_WALL_CLOCK" for v in scan(tmp_path))


def test_canary_clock_module_is_exempt(tmp_path):
    _write(
        tmp_path,
        "mfp/core/clock.py",
        "from datetime import datetime\n\n\ndef now():\n    return datetime.now()\n",
    )
    assert not any(v.firewall == "NO_WALL_CLOCK" for v in scan(tmp_path))


def test_checker_reports_useful_location(tmp_path):
    _write(
        tmp_path,
        "mfp/proof/engine.py",
        "\n\n\nfrom mfp.data.generator.fees import compute_mdr\n",
    )
    violation = next(v for v in scan(tmp_path) if v.firewall == "PROOF_INDEPENDENCE")
    assert violation.module == "mfp.proof.engine"
    assert violation.line == 4
