"""Durable input intents. An interrupted GUI action is never automatically replayed."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from nanomuse.assistant.errors import Conflict


class OutcomeUnknown(ValueError):
    """An input may have reached its target. Observe and revise before continuing."""


class ExecutionJournal:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS action (step_id TEXT PRIMARY KEY,task_id TEXT,"
                "revision INTEGER,adapter TEXT,intent TEXT,status TEXT,result TEXT,at REAL)"
            )

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def get(self, step_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM action WHERE step_id=?", (step_id,)).fetchone()
        return (
            {**dict(row), "intent": json.loads(row["intent"]), "result": json.loads(row["result"])}
            if row
            else None
        )

    def start(self, step: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any] | None:
        encoded = json.dumps(intent, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM action WHERE step_id=?", (step["id"],)).fetchone()
            if old:
                if old["intent"] != encoded:
                    raise Conflict("Action intent changed")
                if old["status"] == "completed":
                    return dict(json.loads(old["result"]))
                raise OutcomeUnknown("A previous action intent exists; do not repeat its input")
            db.execute(
                "INSERT INTO action VALUES(?,?,?,?,?,'started','{}',?)",
                (
                    step["id"],
                    step["task_id"],
                    step["revision"],
                    step["spec"]["adapter"],
                    encoded,
                    time.time(),
                ),
            )
        return None

    def finish(self, step_id: str, result: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE action SET status='completed',result=? WHERE step_id=?",
                (
                    json.dumps(result, ensure_ascii=False),
                    step_id,
                ),
            )

    def records(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT step_id FROM action WHERE task_id=? ORDER BY at", (task_id,)
                )
            ]
        return [row for step_id in ids if (row := self.get(step_id)) is not None]
