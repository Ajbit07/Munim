from __future__ import annotations

from dataclasses import replace

import pytest

from mfp.data.generator.cli import generate
from mfp.data.generator.params import GenerationParams

LOOP = GenerationParams(seed=5, merchants=12, months=4, hero_txn_per_day=40, cohort_size=5, cohort_txn_per_day=(12, 18))


@pytest.fixture(scope="session")
def loop_datasets(tmp_path_factory):
    base = tmp_path_factory.mktemp("loop")
    leaky = generate(replace(LOOP, out_dir=base))
    clean = generate(replace(LOOP, out_dir=base, leakage=False))
    return leaky, clean


@pytest.fixture(scope="session")
def redteam_root(tmp_path_factory):
    from mfp.redteam.generator import write_dataset
    return write_dataset(tmp_path_factory.mktemp("redteam"))
