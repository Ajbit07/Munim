"""Static firewall checker.

Three boundaries hold this system's credibility together. All three are
enforced here by AST analysis, and all three have canary tests in
tests/test_firewalls.py that synthesize a violating module and assert that
this checker catches it. A firewall nobody has seen fail is not a firewall.

FIREWALL 1 -- PROOF INDEPENDENCE (both directions)
    Production packages (proof, rules, fees, reconciliation, agents, workflow,
    memory, network, api, data.store) may not import mfp.data.generator.* or
    mfp.evaluation.*. And the generator may not import the production engines.

    If the Proof Engine could reach the generator's fee logic, it would be
    re-running the arithmetic that produced the data rather than independently
    recomputing from published rules, and "0 false claims" would be circular.
    The reverse direction matters too: if the generator borrowed the Fee
    Engine, fixing a bug in one would silently fix the other, and agreement
    between them would stop meaning anything.

FIREWALL 2 -- GROUND TRUTH ISOLATION
    Only mfp.evaluation.* (which reads it) and mfp.data.generator.* (which
    writes it) may touch ground_truth.

    The production audit pipeline must never know what was planted. Any other
    module naming ground_truth -- by import, by string literal, by filename --
    is a violation.

FIREWALL 3 -- NO WALL CLOCK
    Only mfp.core.clock (and process entrypoints) may call datetime.now(),
    date.today() or time.time().

    Everything else takes an injected Clock, which is what makes the 12-month
    backfill, the follow-up wait and reproducible demo runs possible.

Usage:
    python tools/check_firewall.py [src_root]
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# -- configuration ------------------------------------------------------

PROOF_PACKAGES = ("mfp.proof",)
PRODUCTION_PACKAGES = (
    "mfp.proof",
    "mfp.cases",
    "mfp.prevention",
    "mfp.notify",
    "mfp.llm",
    "mfp.runtime",
    "mfp.rules",
    "mfp.fees",
    "mfp.reconciliation",
    "mfp.agents",
    "mfp.workflow",
    "mfp.memory",
    "mfp.network",
    "mfp.api",
    "mfp.data.store",
)
GENERATOR_PACKAGES = ("mfp.data.generator",)
EVALUATION_PACKAGES = ("mfp.evaluation",)
ENGINE_PACKAGES = ("mfp.proof", "mfp.rules", "mfp.fees", "mfp.reconciliation", "mfp.agents")
TRUTH_PERMITTED_PACKAGES = EVALUATION_PACKAGES + GENERATOR_PACKAGES

GROUND_TRUTH_TOKENS = ("ground_truth", "groundtruth")

CLOCK_EXEMPT_MODULES = ("mfp.core.clock",)
WALL_CLOCK_CALLS = {
    ("datetime", "now"),
    ("date", "today"),
    ("datetime", "utcnow"),
    ("time", "time"),
}


@dataclass(frozen=True)
class Violation:
    firewall: str
    module: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"[{self.firewall}] {self.module}:{self.line} -- {self.detail}"


# -- helpers ------------------------------------------------------------


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _starts_with_any(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                found.append((node.module, node.lineno))
    return found


# -- the three checks ---------------------------------------------------


def check_proof_independence(mod: str, tree: ast.AST) -> list[Violation]:
    out = []
    if _starts_with_any(mod, PRODUCTION_PACKAGES):
        label = "PROOF_INDEPENDENCE" if _starts_with_any(mod, PROOF_PACKAGES) else "PRODUCTION_ISOLATION"
        for imported, line in _imported_modules(tree):
            if _starts_with_any(imported, GENERATOR_PACKAGES + EVALUATION_PACKAGES):
                out.append(
                    Violation(
                        label,
                        mod,
                        line,
                        f"imports {imported}. Production code works from config rules "
                        "and observed artifacts only, never from the generator that "
                        "produced the data or the harness that scores it.",
                    )
                )
    if _starts_with_any(mod, GENERATOR_PACKAGES):
        for imported, line in _imported_modules(tree):
            if _starts_with_any(imported, ENGINE_PACKAGES):
                out.append(
                    Violation(
                        "GENERATOR_INDEPENDENCE",
                        mod,
                        line,
                        f"imports {imported}. The generator's processor must be an "
                        "independent implementation; sharing engine code would turn "
                        "agreement between them into an echo.",
                    )
                )
    return out


def check_ground_truth_isolation(mod: str, tree: ast.AST, source: str) -> list[Violation]:
    if _starts_with_any(mod, TRUTH_PERMITTED_PACKAGES):
        return []
    out = []
    for imported, line in _imported_modules(tree):
        lowered = imported.lower()
        if any(token in lowered for token in GROUND_TRUTH_TOKENS):
            out.append(
                Violation(
                    "GROUND_TRUTH_ISOLATION",
                    mod,
                    line,
                    f"imports {imported}. Only mfp.evaluation.* may read ground truth.",
                )
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            if any(token in lowered for token in GROUND_TRUTH_TOKENS):
                out.append(
                    Violation(
                        "GROUND_TRUTH_ISOLATION",
                        mod,
                        node.lineno,
                        f"references ground truth in a string literal "
                        f"({node.value!r}). Only mfp.evaluation.* may do this.",
                    )
                )
    return out


def check_no_wall_clock(mod: str, tree: ast.AST) -> list[Violation]:
    if _starts_with_any(mod, CLOCK_EXEMPT_MODULES):
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        pair = (func.value.id, func.attr)
        if pair in WALL_CLOCK_CALLS:
            out.append(
                Violation(
                    "NO_WALL_CLOCK",
                    mod,
                    node.lineno,
                    f"calls {pair[0]}.{pair[1]}(). Take an injected Clock instead "
                    "-- virtual time is what makes the backfill and the "
                    "follow-up wait demonstrable.",
                )
            )
    return out


# -- driver -------------------------------------------------------------


def scan(root: Path) -> list[Violation]:
    """Scan every .py under root and return all firewall violations."""
    violations: list[Violation] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - surfaced to the caller
            violations.append(
                Violation("PARSE_ERROR", str(path), exc.lineno or 0, str(exc))
            )
            continue
        mod = module_name(path, root)
        violations.extend(check_proof_independence(mod, tree))
        violations.extend(check_ground_truth_isolation(mod, tree, source))
        violations.extend(check_no_wall_clock(mod, tree))
    return violations


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).parent.parent / "src"
    violations = scan(root)
    if not violations:
        print(f"firewalls intact: no violations under {root}")
        return 0
    print(f"{len(violations)} firewall violation(s):\n")
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
