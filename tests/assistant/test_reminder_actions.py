from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from nanomuse.assistant.reminders import ReminderAction, ReminderActions
from nanomuse.reminders import ReminderStore
from nanomuse.schema import RiskLevel
from nanomuse.tools import Reminders


def test_retry_receipt_survives_reopen_and_preserves_same_reminder(tmp_path: Path) -> None:
    path = tmp_path / "reminders.db"
    store = ReminderStore(path)
    actions = ReminderActions(store)
    create = ReminderAction(action="create", text="Read notes", at="2999-01-01 10:00")
    original = actions.execute(create, "task-one-create")
    reminder_id = original["reminder"]["id"]
    assert actions.execute(create, "task-one-create") == original
    store.close()

    store = ReminderStore(path)
    actions = ReminderActions(store)
    assert actions.execute(create, "task-one-create") == original
    assert len(store.list()) == 1
    update = ReminderAction(
        action="update",
        reminder_id=reminder_id,
        expected_revision=1,
        text="Read revised notes",
        at="2999-01-02 11:00",
        kind="task",
    )
    edited = actions.execute(update, "task-one-update")
    item = store.get(reminder_id)
    assert item is not None and item.id == reminder_id
    assert item.revision == 2 and item.kind == "task" and item.text == "Read revised notes"
    assert item.next_at != original["reminder"]["next_at"]
    # The domain write has committed even if the task ledger has not committed its result.
    store.close()
    store = ReminderStore(path)
    actions = ReminderActions(store)
    assert actions.execute(update, "task-one-update") == edited
    with pytest.raises(ValueError, match="different reminder inputs"):
        actions.execute(create.model_copy(update={"text": "Other notes"}), "task-one-create")
    with pytest.raises(ValueError, match="changed"):
        actions.execute(update, "another-edit-from-stale-view")
    assert store.get(reminder_id).revision == 2
    cancelled = actions.execute(
        ReminderAction(action="cancel", reminder_id=reminder_id, expected_revision=2),
        "task-one-cancel",
    )
    assert cancelled["reminder"]["status"] == "cancelled"
    assert cancelled["reminder"]["revision"] == 3
    assert actions.execute(update, "task-one-update") == edited
    assert store.get(reminder_id).status == "cancelled"
    assert store.get(reminder_id).revision == 3
    assert store.due(datetime.fromisoformat("2999-01-03T00:00:00+00:00")) == []
    store.close()


def test_edit_cancel_and_firing_reject_stale_occurrences(tmp_path: Path) -> None:
    store = ReminderStore(tmp_path / "reminders.db")
    once = store.create("Read notes", at="2999-01-01 10:00")
    old_due = store.due(datetime.fromisoformat("2999-01-01T23:00:00+00:00"))[0]
    edited = store.update(once.id, expected_revision=1, at="2999-01-02 10:00")
    assert (
        store.mark_fired(
            old_due.id, expected_revision=old_due.revision, expected_next_at=old_due.next_at
        )
        is None
    )
    assert store.due(datetime.fromisoformat("2999-01-01T23:00:00+00:00")) == []
    fired = store.mark_fired(
        edited.id, expected_revision=edited.revision, expected_next_at=edited.next_at
    )
    assert fired is not None and fired.status == "done" and fired.fired == 1
    with pytest.raises(ValueError, match="changed"):
        store.update(once.id, expected_revision=edited.revision, text="Too late")
    with pytest.raises(ValueError, match="done"):
        store.cancel(once.id, expected_revision=fired.revision)
    assert store.mark_fired(once.id, expected_revision=edited.revision) is None

    repeating = store.create("Review notes", repeat="daily 08:00", thread="notes")
    next_once = store.update(repeating.id, expected_revision=1, at="2999-01-03 12:00")
    assert next_once.repeat == "" and next_once.thread == "notes"
    cadence = store.update(next_once.id, expected_revision=2, repeat="weekly mon 09:00")
    assert cadence.repeat == "weekly mon 09:00" and cadence.id == repeating.id
    cancelled = store.cancel(cadence.id, expected_revision=3)
    assert cancelled is not None
    assert store.mark_fired(cadence.id, expected_revision=3) is None
    store.close()


def test_same_request_from_two_connections_commits_once(tmp_path: Path) -> None:
    path = tmp_path / "reminders.db"
    first = ReminderStore(path)
    second = ReminderStore(path)
    action = ReminderAction(action="create", text="Read notes", repeat="daily 08:00")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(ReminderActions(store).execute, action, "same-request")
            for store in (first, second)
        ]
        results = [future.result() for future in futures]
    assert results[0] == results[1]
    assert len(first.list()) == 1
    first.close()
    second.close()


def test_existing_database_gains_revision_without_losing_reminders(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE reminders(id TEXT PRIMARY KEY,text TEXT,kind TEXT,thread TEXT,"
        "status TEXT,next_at TEXT,repeat TEXT,created_at TEXT,last_fired_at TEXT,fired INTEGER)"
    )
    db.execute(
        "INSERT INTO reminders VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "r_old",
            "Read notes",
            "remind",
            "main",
            "active",
            "2999-01-01T10:00:00+00:00",
            "",
            "2026-01-01T00:00:00+00:00",
            "",
            0,
        ),
    )
    db.commit()
    db.close()
    store = ReminderStore(path)
    original = store.get("r_old")
    assert original is not None and original.revision == 1 and original.text == "Read notes"
    edited = store.update(original.id, expected_revision=1, text="Read changed notes")
    assert edited.revision == 2 and edited.created_at == original.created_at
    store.close()


def test_action_validation_and_agent_update_keep_current_values(tmp_path: Path) -> None:
    for payload in (
        {"action": "update", "reminder_id": "r_old", "text": "New"},
        {"action": "update", "reminder_id": "r_old", "expected_revision": "1", "text": "New"},
        {"action": "cancel", "reminder_id": "r_old", "expected_revision": 1, "text": "New"},
        {"action": "create", "text": "Read", "at": "2999-01-01 10:00", "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            ReminderAction.model_validate(payload)
    store = ReminderStore(tmp_path / "reminders.db")
    item = store.create("Read notes", at="2999-01-01 10:00", thread="notes")
    tool = Reminders(store=store)
    assert tool.assess({"action": "update", "reminder_id": item.id}).risk == RiskLevel.MODERATE
    result = asyncio.run(
        tool.execute(
            action="update", reminder_id=item.id, expected_revision=1, text="Read revised notes"
        )
    )
    assert result.ok
    edited = store.get(item.id)
    assert edited is not None and edited.next_at == item.next_at and edited.thread == "notes"
    result = asyncio.run(
        tool.execute(action="update", reminder_id=item.id, expected_revision=1, text="Stale edit")
    )
    assert not result.ok and "changed" in (result.error or "")
    assert store.get(item.id).text == "Read revised notes"
    store.close()
