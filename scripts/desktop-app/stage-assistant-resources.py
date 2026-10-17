"""Synchronize the assistant's packaged data after the web build has finished."""

from __future__ import annotations

import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "desktop" / "assistant" / "runtime" / "assistant-runtime"


def main() -> None:
    if not (TARGET / "assistant-runtime.exe").is_file():
        raise SystemExit("Build the Windows assistant runtime first.")
    destination = (TARGET / "_internal").resolve()
    files = collect_data_files("nanomuse")
    for source, relative in files:
        target = (destination / relative / Path(source).name).resolve()
        if not target.is_relative_to(destination):
            raise SystemExit("The resource destination is outside the runtime.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for required in ("research/schema.sql", "llm/providers.json", "server/static/index.html"):
        if not (destination / "nanomuse" / required).is_file():
            raise SystemExit(f"Missing runtime resource: {required}")
    print(f"Staged {len(files)} package resources.")


if __name__ == "__main__":
    main()
