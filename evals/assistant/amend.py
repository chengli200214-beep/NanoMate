"""Apply a newly approved pilot continuation without resetting spending history."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.assistant.freeze import PILOTS, approval, digest
from evals.research.budget import BatchBudget


def _publish(path: Path, receipt: dict[str, Any]) -> None:
    temporary = path.with_name(f".approval-{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(receipt, sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def amend(directory: Path, new_receipt: dict[str, Any]) -> dict[str, Any]:
    """Require four finished pilots and a new exact full-phase approval.

    Counts, execution IDs and settled costs remain in the original SQLite file.
    A crash after the transaction but before replacing the receipt fails closed;
    retrying this exact approved amendment repairs only the receipt publication.
    """
    frozen = json.loads((directory / "freeze.json").read_text("utf-8"))
    new_policy, _ = approval(frozen, new_receipt)
    if new_receipt["phase"] != "complete":
        raise ValueError("An amendment must explicitly approve the remaining full-phase windows")
    path = directory / "budget.sqlite3"
    if not path.is_file():
        raise ValueError("Preserve the pilot ledger; a continuation cannot create a replacement")
    identifier = digest(new_receipt)
    # Validate numeric policy using the existing policy validator in a disposable,
    # in-memory ledger. No provider or persistent replacement ledger is involved.
    validator = BatchBudget(Path(":memory:"), new_policy)
    validator.close()
    db = sqlite3.connect(path, isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_approval_amendment (id TEXT PRIMARY KEY, old_receipt TEXT NOT NULL,new_receipt TEXT NOT NULL)"
        )
        existing = db.execute(
            "SELECT * FROM assistant_approval_amendment WHERE id=?", (identifier,)
        ).fetchone()
        if existing is not None:
            if json.loads(existing["new_receipt"]) != new_receipt:
                raise ValueError("Amendment identity conflict")
            if (
                json.loads(db.execute("SELECT value FROM policy WHERE id=1").fetchone()[0])
                != new_policy
            ):
                raise ValueError("The committed amendment policy changed")
            db.execute("COMMIT")
            _publish(directory / "approval.json", new_receipt)
            return {"amendment_sha256": identifier, "recovered_receipt_publication": True}
        old_receipt = json.loads((directory / "approval.json").read_text("utf-8"))
        old_policy, _ = approval(frozen, old_receipt)
        if old_receipt["phase"] != "pilots":
            raise ValueError("Only a completed pilot phase can be continued")
        actual = json.loads(db.execute("SELECT value FROM policy WHERE id=1").fetchone()[0])
        if actual != old_policy:
            raise ValueError("The persisted pilot policy differs from its original approval")
        variable = {"limit_cny", "stop_reserve_cny", "assistant_approval_sha256"}
        if {key: value for key, value in old_policy.items() if key not in variable} != {
            key: value for key, value in new_policy.items() if key not in variable
        }:
            raise ValueError("A continuation cannot change model, vendor rates, context or freeze")
        if Decimal(str(new_policy["limit_cny"])) < Decimal(str(old_policy["limit_cny"])):
            raise ValueError("A continuation cannot lower the already approved batch allowance")
        rows = db.execute("SELECT id,status FROM assistant_execution").fetchall()
        if {row["id"] for row in rows} != {f"{case}-r1" for case in PILOTS} or any(
            row["status"] != "finished" for row in rows
        ):
            raise ValueError("Finish exactly the four pilot windows before approving continuation")
        if db.execute("SELECT 1 FROM request WHERE status!='settled'").fetchone():
            raise ValueError("Unknown or outstanding provider usage must be reconciled first")
        # Do not erase a safety stop or reinterpret a failed provider attempt.
        if db.execute("SELECT 1 FROM stop").fetchone():
            raise ValueError(
                "A stopped pilot batch needs explicit reconciliation, not an amendment"
            )
        spent = db.execute("SELECT COALESCE(SUM(charged_upper),0) FROM request").fetchone()[0]
        maximum = (
            Decimal(str(new_policy["limit_cny"])) - Decimal(str(new_policy["stop_reserve_cny"]))
        ) * 1_000_000
        if spent > maximum:
            raise ValueError("The new allowance cannot cover already settled charges")
        db.execute(
            "UPDATE policy SET value=? WHERE id=1", (json.dumps(new_policy, sort_keys=True),)
        )
        db.execute(
            "INSERT INTO assistant_approval_amendment VALUES(?,?,?)",
            (
                identifier,
                json.dumps(old_receipt, sort_keys=True),
                json.dumps(new_receipt, sort_keys=True),
            ),
        )
        db.execute("COMMIT")
        _publish(directory / "approval.json", new_receipt)
        return {"amendment_sha256": identifier, "recovered_receipt_publication": False}
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
