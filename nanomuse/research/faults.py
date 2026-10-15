"""Named process exits for opt-in recovery tests."""

from __future__ import annotations

import os


def crash_point(name: str) -> None:
    if os.environ.get("NANOMUSE_RESEARCH_CRASH") == name:
        os._exit(86)
