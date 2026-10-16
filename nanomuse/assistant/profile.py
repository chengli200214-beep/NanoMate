"""Isolated local assistant profile and an OS-released single-instance lock."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from nanomuse.config import Settings
from nanomuse.research.model import load_research_settings


@contextmanager
def own_directory(directory: Path) -> Iterator[None]:
    """Reject existing foreign data before creating a service or changing settings."""
    directory = directory.expanduser().resolve()
    marker = directory / "assistant-profile.json"
    lock = directory / ".assistant.lock"
    if directory.exists() and not marker.exists():
        if any(p.name != lock.name for p in directory.iterdir()):
            raise ValueError("Use an empty assistant directory; existing data is preserved.")
    directory.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        stream.seek(0)
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Another assistant owns this data directory.") from exc
        try:
            if marker.exists():
                if json.loads(marker.read_text("utf-8")) != {"profile": "assistant", "version": 1}:
                    raise ValueError("Unsupported assistant data profile.")
            else:
                if any(p.name != lock.name for p in directory.iterdir()):
                    raise ValueError(
                        "Use an empty assistant directory; existing data is preserved."
                    )
                marker.write_text('{"profile": "assistant", "version": 1}\n', encoding="utf-8")
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def assistant_settings(directory: Path, port: int, config: Path | None = None) -> Settings:
    settings = load_research_settings(config=config, data_dir=directory)
    settings.data_dir = directory
    settings.agent.workspace = directory / "workspace"
    settings.agent.extra_roots = []
    settings.agent.task_ledger_only = True
    settings.server.host, settings.server.port = "127.0.0.1", port
    settings.server.auth, settings.server.update_check = True, False
    settings.cloud.required = settings.cloud.models = settings.cloud.sync = False
    settings.hub.enabled = False
    settings.connectors.email.enabled = False
    settings.connectors.calendar.enabled = False
    settings.connectors.contacts.enabled = False
    settings.skills.enabled = False
    settings.mcp.servers = []
    # Retain local browser/hands choices, but do not enable them merely by launching chat.
    settings.sentinel.mode = "ask"
    settings.memory.embeddings = "off"
    return settings
