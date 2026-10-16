"""Whole classification plans can preview new folders without changing files."""

from __future__ import annotations

import hashlib

import pytest

from nanomuse.assistant.ledger import Plan
from tests.test_assistant_execution import approve_and_bind, execution_host, waiting

__all__ = ["execution_host"]


@pytest.mark.asyncio
async def test_classification_previews_all_targets_before_mkdir_and_moves(execution_host) -> None:
    _, _, coordinator, llm = execution_host
    runtime = coordinator.personal
    originals = {f"note-{index}.md": f"source {index}".encode() for index in range(6)}
    for name, content in originals.items():
        (runtime.workspace / name).write_bytes(content)
    steps = [
        {
            "adapter": "file",
            "title": "Create notes folder",
            "file": {"action": "mkdir", "path": "notes"},
        }
    ]
    steps.extend(
        {
            "adapter": "file",
            "title": f"Classify {name}",
            "file": {"action": "move", "source_path": name, "path": f"notes/{name}"},
        }
        for name in originals
    )
    value = Plan(
        title="Classify six notes",
        success_criteria="Same bytes in notes, sources absent",
        steps=steps,
    )
    task = waiting(coordinator, value)
    assert not (runtime.workspace / "notes").exists()
    assert all((runtime.workspace / name).read_bytes() == data for name, data in originals.items())
    # Creation and observation never authorize an effect. Each reviewed action still
    # goes through the user's exact preview and the real host Sentinel.
    assert all(not coordinator.ledger.preference_authorized(step["id"]) for step in task["steps"])
    for _ in range(7):
        current = coordinator.ledger.get(task["id"])
        step = approve_and_bind(coordinator, current)
        await runtime._run(step)
        coordinator.tick()
    final = coordinator.ledger.get(task["id"])
    assert final["status"] == "completed"
    for name, data in originals.items():
        assert not (runtime.workspace / name).exists()
        assert (runtime.workspace / "notes" / name).read_bytes() == data
    receipts = final["steps"][1:]
    assert {s["result"]["verification"]["sha256"] for s in receipts} == {
        hashlib.sha256(data).hexdigest() for data in originals.values()
    }
    assert not llm.calls


def test_classification_requires_prior_mkdir_and_rejects_duplicate_targets(execution_host) -> None:
    _, _, coordinator, _ = execution_host
    runtime = coordinator.personal
    (runtime.workspace / "source.md").write_text("source", encoding="utf-8")
    move = {
        "adapter": "file",
        "title": "Move",
        "file": {"action": "move", "source_path": "source.md", "path": "notes/a.md"},
    }
    mkdir = {"adapter": "file", "title": "Folder", "file": {"action": "mkdir", "path": "notes"}}
    with pytest.raises(ValueError, match="earlier mkdir"):
        runtime.validate(
            Plan(title="Wrong order", success_criteria="No effects", steps=[move, mkdir])
        )
    with pytest.raises(ValueError, match="same file target"):
        runtime.validate(
            Plan(title="Collision", success_criteria="No effects", steps=[mkdir, move, move])
        )
    assert not (runtime.workspace / "notes").exists()


def test_new_parent_replaced_after_preview_blocks_publication(execution_host) -> None:
    _, _, coordinator, _ = execution_host
    runtime = coordinator.personal
    (runtime.workspace / "source.md").write_bytes(b"source")
    plan = Plan.model_validate(
        {
            "title": "Classify",
            "success_criteria": "No conflict",
            "steps": [
                {
                    "adapter": "file",
                    "title": "Folder",
                    "file": {"action": "mkdir", "path": "notes"},
                },
                {
                    "adapter": "file",
                    "title": "Move",
                    "file": {"action": "move", "source_path": "source.md", "path": "notes/a.md"},
                },
            ],
        }
    )
    runtime.validate(plan)
    # The preview cannot authorize a file or link masquerading as a future directory.
    (runtime.workspace / "notes").write_bytes(b"user data")
    with pytest.raises(ValueError, match="parent"):
        runtime.files.execute(plan.steps[1].file, "conflict", "task", 1, "step")
    assert (runtime.workspace / "source.md").read_bytes() == b"source"
    assert (runtime.workspace / "notes").read_bytes() == b"user data"
