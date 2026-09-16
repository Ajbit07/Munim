"""Entry point: python evaluate.py --seed 42 [--merchants all|MER-0001,...]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mfp.evaluation.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
