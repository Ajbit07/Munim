"""Entry point: python generate.py --seed 42 [--no-leakage] [--merchants N] [--months N]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from mfp.data.generator.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
