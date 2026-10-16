from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from nanomuse.config import Settings
from nanomuse.llm.mock import MockLLM
from nanomuse.reminders import Reminder
from nanomuse.server.service import MuseService


def test_due_snapshot_cannot_dispatch_after_edit_or_cancel(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    send = Mock(return_value={})
    monkeypatch.setattr(service, "send", send)
    store = service.app.reminders
    item = store.create("Read notes", at="2999-01-01 10:00")
    monkeypatch.setattr(store, "due", lambda: [item])
    edited = store.update(item.id, expected_revision=1, at="2999-01-02 10:00")
    service._run_due_reminders()
    send.assert_not_called()
    assert store.get(item.id).revision == 2
    assert store.get(item.id).next_at == edited.next_at

    store.cancel(item.id, expected_revision=2)
    with pytest.raises(ValueError, match="cancelled"):
        service.fire_reminder(
            item.id, expected_revision=item.revision, expected_next_at=item.next_at
        )
    service._run_due_reminders()
    send.assert_not_called()


def test_edit_between_read_and_claim_is_not_dispatched(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    send = Mock(return_value={})
    monkeypatch.setattr(service, "send", send)
    store = service.app.reminders
    item = store.create("Read notes", at="2999-01-01 10:00")
    mark_fired = store.mark_fired

    def raced_claim(reminder_id: str, **kwargs: int | str) -> Reminder | None:
        store.update(reminder_id, expected_revision=1, at="2999-01-02 10:00")
        return mark_fired(reminder_id, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store, "mark_fired", raced_claim)
    with pytest.raises(ValueError, match="changed before"):
        service.fire_reminder(item.id)
    send.assert_not_called()
    assert store.get(item.id).status == "active"
    assert store.get(item.id).fired == 0


def test_due_dispatch_claims_once_and_manual_fire_remains_available(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nanomuse.reminders.store as reminder_store

    now = datetime(2026, 1, 1, 8, tzinfo=UTC)
    monkeypatch.setattr(reminder_store, "_now", lambda: now)
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    send = Mock(return_value={})
    monkeypatch.setattr(service, "send", send)
    store = service.app.reminders
    item = store.create("Read notes", at=(now + timedelta(minutes=1)).isoformat())
    now += timedelta(minutes=2)
    service._run_due_reminders()
    send.assert_called_once()
    assert send.call_args.kwargs["source"] == "reminder"
    fired = store.get(item.id)
    assert fired is not None and fired.status == "done" and fired.fired == 1
    service._run_due_reminders()
    send.assert_called_once()

    future = store.create("Review notes", at=(now + timedelta(days=1)).isoformat())
    manual = service.fire_reminder(future.id)
    assert manual.status == "done" and manual.fired == 1
    assert send.call_count == 2 and not llm.calls


def test_missing_target_chat_is_rejected_before_claim(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    send = Mock(return_value={})
    monkeypatch.setattr(service, "send", send)
    item = service.app.reminders.create("Read notes", at="2999-01-01 10:00")
    service.threads.clear()
    with pytest.raises(KeyError):
        service.fire_reminder(item.id)
    assert service.app.reminders.get(item.id).status == "active"
    send.assert_not_called()
