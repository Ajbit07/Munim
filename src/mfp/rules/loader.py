"""Loading rule configuration.

Block 0 provides loading and validation only. Temporal resolution -- picking
exactly one rule for a (instrument, mcc, class, amount, date) query, and
raising AmbiguousRuleError or NoApplicableRuleError rather than guessing --
lands with the Rule Engine in Block 2.
"""

from __future__ import annotations

import json
from pathlib import Path

from mfp.schemas.rules import RuleSet

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def load_rule_set(path: Path) -> RuleSet:
    """Load and validate a rule set. Raises pydantic.ValidationError on a bad rule."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return RuleSet.model_validate(raw)


def load_fee_rules(config_dir: Path | None = None) -> RuleSet:
    directory = config_dir or DEFAULT_CONFIG_DIR
    return load_rule_set(directory / "fee_rules.json")


def load_raw(name: str, config_dir: Path | None = None) -> dict:
    """Load a config file that is not a RuleSet (regulatory, settlement)."""
    directory = config_dir or DEFAULT_CONFIG_DIR
    return json.loads((directory / name).read_text(encoding="utf-8"))
