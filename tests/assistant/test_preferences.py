"""Confirmed preference provenance, deletion and interrupted projection recovery."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanomuse.assistant.ledger import Conflict
from nanomuse.assistant.preferences import PreferenceAction, PreferenceStore
from nanomuse.config import LLMSettings, MemorySettings
from nanomuse.memory.embeddings import Embedder, MemoryIndex
from nanomuse.memory.store import MemoryStore
from nanomuse.research.clock import FakeClock


def action(**changes: Any) -> PreferenceAction:
    return PreferenceAction.model_validate(
        {
            "action": "set",
            "key": "reply_style",
            "value": "Use concise answers",
            "purpose": "Routine explanations",
            "expected_revision": 0,
            **changes,
        }
    )


def run(store: PreferenceStore, spec: PreferenceAction, key: str = "request") -> dict[str, Any]:
    return store.execute(spec, key, "task", 1, "step")


@pytest.fixture
def memory(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory.db")


def test_confirmed_preference_retrieval_isolation_and_provenance(
    tmp_path: Path, memory: MemoryStore
) -> None:
    other = memory.add("Use concise answers", category="note", source="user")
    store = PreferenceStore(tmp_path / "preferences.db", memory)
    receipt = run(store, action())
    assert memory.count() == 2
    projected = memory.get(receipt["memory_id"])
    assert projected is not None and projected.category == "preference"
    assert "current request takes priority" in projected.content
    assert json.loads(projected.source.removeprefix("assistant_preference:")) == {
        "key": "reply_style",
        "revision": 1,
        "source_task": "task",
        "source_plan": 1,
        "source_step": "step",
        "confirmed_at": store.list()[0]["confirmed_at"],
        "expires_at": None,
    }
    assert projected.id in {item.id for item in memory.search("concise answers")}
    assert projected.id in {item.id for item in memory.relevant("explanations")}
    assert memory.get(other.id) == other
    assert "Use concise answers" not in json.dumps(receipt)


def test_idempotent_restart_update_and_old_replay_do_not_resurrect(
    tmp_path: Path, memory: MemoryStore
) -> None:
    path = tmp_path / "preferences.db"
    store = PreferenceStore(path, memory)
    first = run(store, action())
    assert run(store, action()) == first
    reopened = PreferenceStore(path, memory)
    replacement = action(value="Give detailed reasoning", expected_revision=1)
    second = run(reopened, replacement, "replacement")
    assert second["memory_id"] == first["memory_id"]
    assert second["revision"] == 2
    assert run(reopened, action()) == first
    assert "detailed reasoning" in memory.get(first["memory_id"]).content  # type: ignore[union-attr]
    deleted = PreferenceAction(action="delete", key="reply_style", expected_revision=2)
    result = run(reopened, deleted, "delete")
    assert result["revision"] == 3
    assert run(reopened, action()) == first
    assert run(reopened, replacement, "replacement") == second
    assert memory.get(first["memory_id"]) is None
    assert reopened.list() == []
    tombstone = reopened.list(include_deleted=True)[0]
    assert tombstone["status"] == "deleted" and tombstone["value"] == tombstone["purpose"] == ""
    with sqlite3.connect(path) as db:
        receipts = db.execute("SELECT payload_hash,result FROM preference_receipt").fetchall()
    assert "detailed reasoning" not in str(receipts)
    assert "concise answers" not in str(receipts)
    assert memory.history() == []


def test_changed_inputs_and_stale_revisions_require_review(
    tmp_path: Path, memory: MemoryStore
) -> None:
    store = PreferenceStore(tmp_path / "preferences.db", memory)
    run(store, action())
    with pytest.raises(Conflict, match="different inputs"):
        run(store, action(value="Long answers"))
    with pytest.raises(Conflict, match="current revision"):
        run(store, action(value="Long answers"), "second")
    with pytest.raises(Conflict, match="does not exist"):
        run(
            store,
            PreferenceAction(action="delete", key="unknown", expected_revision=0),
            "unknown",
        )
    assert len(store.list()) == 1 and store.list()[0]["revision"] == 1


def test_expiry_scrubs_memory_and_blocks_old_replay(tmp_path: Path, memory: MemoryStore) -> None:
    clock = FakeClock()
    store = PreferenceStore(tmp_path / "preferences.db", memory, clock.now)
    spec = action(expires_at=clock.now() + 60)
    first = run(store, spec)
    clock.advance(61)
    assert store.active_context() == []
    assert memory.get(first["memory_id"]) is None
    assert run(store, spec) == first
    assert store.list(include_deleted=True)[0]["status"] == "expired"
    assert store.list(include_deleted=True)[0]["value"] == ""
    with pytest.raises(Conflict):
        run(store, action(expected_revision=1), "stale")
    revived = run(store, action(expected_revision=2), "fresh")
    assert revived["revision"] == 3


def test_interrupted_write_recovers_latest_committed_intent(
    tmp_path: Path, memory: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "preferences.db"
    store = PreferenceStore(path, memory)
    original = memory.set_owned_projection

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise OSError("Interrupted memory projection")

    monkeypatch.setattr(memory, "set_owned_projection", unavailable)
    with pytest.raises(OSError, match="Interrupted"):
        run(store, action())
    assert memory.count() == 0
    monkeypatch.setattr(memory, "set_owned_projection", original)
    recovered = PreferenceStore(path, memory)
    assert recovered.list()[0]["revision"] == 1
    assert run(recovered, action())["revision"] == 1
    assert memory.count() == 1
    remove = memory.remove_owned_projection
    monkeypatch.setattr(memory, "remove_owned_projection", unavailable)
    with pytest.raises(OSError):
        run(
            recovered,
            PreferenceAction(action="delete", key="reply_style", expected_revision=1),
            "delete",
        )
    monkeypatch.setattr(memory, "remove_owned_projection", remove)
    final = PreferenceStore(path, memory)
    assert final.list() == [] and memory.count() == 0
    run(final, action())
    assert memory.count() == 0


def test_concurrent_revision_writers_publish_only_the_winning_value(
    tmp_path: Path, memory: MemoryStore
) -> None:
    path = tmp_path / "preferences.db"
    store = PreferenceStore(path, memory)
    run(store, action())

    def update(label: str) -> dict[str, Any] | None:
        try:
            return run(store, action(expected_revision=1, value=label), label)
        except Conflict:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, ["Short replies", "Detailed replies"]))
    assert sum(result is not None for result in results) == 1
    current = store.list()[0]
    assert current["revision"] == 2
    projected = memory.get(current["memory_id"])
    assert projected is not None and current["value"] in projected.content


def test_external_forget_is_observed_before_checking_a_new_revision(
    tmp_path: Path, memory: MemoryStore
) -> None:
    store = PreferenceStore(tmp_path / "preferences.db", memory)
    first = run(store, action())
    memory.forget(first["memory_id"])
    with pytest.raises(Conflict, match="current revision"):
        run(store, action(expected_revision=1, value="A new preference"), "new")
    assert store.list() == []
    result = run(store, action(expected_revision=2, value="A new preference"), "new-confirmed")
    assert result["revision"] == 3


@pytest.mark.parametrize("surface", ["forget", "forget_matching", "clear"])
def test_existing_memory_deletion_revokes_without_restore(
    tmp_path: Path, memory: MemoryStore, surface: str
) -> None:
    path = tmp_path / "preferences.db"
    store = PreferenceStore(path, memory)
    receipt = run(store, action())
    other = memory.add("Another unrelated memory", source="user")
    if surface == "forget":
        memory.forget(receipt["memory_id"])
    elif surface == "forget_matching":
        memory.forget_matching("concise answers")
    else:
        memory.clear()
    recovered = PreferenceStore(path, memory)
    assert recovered.list() == []
    assert run(recovered, action()) == receipt
    assert memory.get(receipt["memory_id"]) is None
    assert memory.history() == []
    tombstone = recovered.list(include_deleted=True)[0]
    assert tombstone["revision"] == 2 and tombstone["value"] == ""
    if surface != "clear":
        assert memory.get(other.id) == other


def test_owned_preferences_cannot_be_copied_by_tidy_or_updated_outside_ledger(
    tmp_path: Path, memory: MemoryStore
) -> None:
    store = PreferenceStore(tmp_path / "preferences.db", memory)
    receipt = run(store, action())
    other = memory.add("Another memory")
    assert memory.replace([receipt["memory_id"], other.id], "Use concise answers") is None
    assert memory.drop(receipt["memory_id"]) is None
    with pytest.raises(ValueError, match="task ledger"):
        memory.update(receipt["memory_id"], "Unconfirmed changed value")
    assert memory.history() == []
    assert len(store.list()) == 1 and memory.count() == 2


def test_preference_changes_remove_persisted_and_cached_vectors(
    tmp_path: Path, memory: MemoryStore
) -> None:
    store = PreferenceStore(tmp_path / "preferences.db", memory)
    receipt = run(store, action())
    memory.put_vectors("example", {receipt["memory_id"]: ("old", [1.0, 0.0])})
    embedder = Embedder(MemorySettings(embeddings="off"), LLMSettings())
    memory.index = MemoryIndex(memory, embedder)
    memory.index._vectors = {receipt["memory_id"]: ("old", [1.0, 0.0])}
    run(store, action(expected_revision=1, value="Use short bullet points"), "change")
    assert memory.vectors("example") == {}
    assert memory.index._vectors is None
    run(store, PreferenceAction(action="delete", key="reply_style", expected_revision=2), "delete")
    assert memory.vectors("example") == {} and memory.index._vectors is None


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_revision": True},
        {"expected_revision": "1"},
        {"key": "Invalid Key"},
        {"key": "a" * 65},
        {"value": " "},
        {"value": "x" * 2001},
        {"action": "infer"},
        {"provenance": "document"},
        {"expires_at": float("inf")},
        {"action": "delete"},
    ],
)
def test_preference_actions_are_strict(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        action(**changes)


def test_disabled_memory_and_elapsed_expiry_do_not_store_preference(tmp_path: Path) -> None:
    clock = FakeClock()
    disabled = PreferenceStore(tmp_path / "disabled.db", None, clock.now)
    with pytest.raises(ValueError, match="enabled"):
        run(disabled, action())
    assert disabled.list() == []
    memory = MemoryStore(tmp_path / "memory.db")
    store = PreferenceStore(tmp_path / "preferences.db", memory, clock.now)
    with pytest.raises(ValueError, match="future"):
        run(store, action(expires_at=clock.now()))
    assert store.list() == [] and memory.count() == 0
