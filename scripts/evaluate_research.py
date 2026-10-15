"""Run the frozen research matrix. Defaults to a dry run with no model requests."""

from __future__ import annotations

import sys
from pathlib import Path

# Keep an explicit PYTHONPATH ahead of this source checkout for integration checks.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from evals.research.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
