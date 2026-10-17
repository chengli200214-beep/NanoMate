"""Frozen entry for the isolated personal assistant, without a system Python install."""

from __future__ import annotations

import multiprocessing


def main() -> None:
    multiprocessing.freeze_support()
    from nanomuse.certs import ensure_ca_bundle

    ensure_ca_bundle()
    from assistant_dev import main as assistant_main

    assistant_main()


if __name__ == "__main__":
    main()
