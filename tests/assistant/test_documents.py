"""Source snapshots, crash recovery, path boundaries and verified artifact reads."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanomuse.assistant.documents import (
    MAX_SOURCE_BYTES,
    DocumentArtifacts,
    DocumentSpec,
    prepare,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nEvidence from a local source.\n", encoding="utf-8")
    return root


def execute(store: DocumentArtifacts, spec: DocumentSpec, key: str = "request") -> dict[str, Any]:
    return store.execute(spec, key, "task", 1, "step")


def test_extract_and_verify_replay(workspace: Path, tmp_path: Path) -> None:
    original = (workspace / "notes.md").read_bytes()
    spec = prepare(
        {"source_paths": ["notes.md"], "notes": "Review this passage.\n```\nUser notes."}, workspace
    )
    assert spec.source_hashes == {"notes.md": hashlib.sha256(original).hexdigest()}
    store = DocumentArtifacts(tmp_path / "data", workspace)
    artifact = execute(store, spec)
    read = store.read(artifact["id"])
    assert artifact["verified"] and artifact["kind"] == "extractive_digest"
    assert "Evidence from a local source." in read["content"]
    assert "User notes." in read["content"] and "not a semantic summary" in read["content"]
    assert hashlib.sha256((workspace / artifact["path"]).read_bytes()).hexdigest() == read["sha256"]
    assert (workspace / "notes.md").read_bytes() == original
    assert execute(DocumentArtifacts(tmp_path / "data", workspace), spec) == artifact
    # Completed receipts refer to their source snapshot, not later source changes.
    (workspace / "notes.md").unlink()
    assert execute(store, spec) == artifact


def test_changed_sources_require_new_plan(workspace: Path, tmp_path: Path) -> None:
    spec = prepare({"source_paths": ["notes.md"]}, workspace)
    (workspace / "notes.md").write_text("Changed material", encoding="utf-8")
    store = DocumentArtifacts(tmp_path / "data", workspace)
    with pytest.raises(ValueError, match="changed"):
        execute(store, spec)
    with pytest.raises(ValueError, match="changed"):
        prepare(spec, workspace)
    with pytest.raises(ValueError, match="prepared"):
        execute(store, DocumentSpec(source_paths=["notes.md"]))
    assert not (workspace / "assistant-artifacts").exists()


@pytest.mark.parametrize(
    "source",
    ["../notes.md", "/notes.md", "C:/notes.md", "folder/../notes.md", "a.txt:stream", "NUL.md"],
)
def test_source_paths_are_explicit_and_scoped(source: str) -> None:
    with pytest.raises(ValidationError):
        DocumentSpec(source_paths=[source])


@pytest.mark.parametrize("name", ["../digest.md", "nested/digest.md", "digest.txt", "CON.md"])
def test_output_names_cannot_escape(name: str) -> None:
    with pytest.raises(ValidationError):
        DocumentSpec(source_paths=["notes.md"], output_name=name)


def test_invalid_text_and_size_limits(workspace: Path) -> None:
    source = workspace / "invalid.txt"
    for content in (b"\xff", b"binary\x00data", b"a" * (MAX_SOURCE_BYTES + 1)):
        source.write_bytes(content)
        with pytest.raises(ValueError):
            prepare({"source_paths": [source.name]}, workspace)
    with pytest.raises(ValidationError):
        DocumentSpec(source_paths=["notes.md"], unexpected="value")
    with pytest.raises(ValidationError):
        DocumentSpec(source_paths=["notes.md", "NOTES.md"])


def test_symlink_source_and_output_are_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("Private", encoding="utf-8")
    link = workspace / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(ValueError, match="links|junctions"):
        prepare({"source_paths": ["linked/private.md"]}, workspace)
    link.unlink()
    spec = prepare({"source_paths": ["notes.md"]}, workspace)
    (workspace / "assistant-artifacts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="links|junctions"):
        execute(DocumentArtifacts(tmp_path / "data", workspace), spec)
    assert list(outside.iterdir()) == [outside / "private.md"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_junction_source_is_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text("Private", encoding="utf-8")
    link = workspace / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    try:
        with pytest.raises(ValueError, match="links|junctions"):
            prepare({"source_paths": ["junction/private.md"]}, workspace)
    finally:
        # Remove only this junction itself, never recursively traverse its target.
        link.rmdir()
    assert (outside / "private.md").read_text(encoding="utf-8") == "Private"


@pytest.mark.parametrize("remove", [False, True])
def test_receipt_does_not_hide_missing_or_tampered_artifact(
    workspace: Path, tmp_path: Path, remove: bool
) -> None:
    spec = prepare({"source_paths": ["notes.md"]}, workspace)
    store = DocumentArtifacts(tmp_path / "data", workspace)
    artifact = execute(store, spec)
    target = workspace / artifact["path"]
    if remove:
        target.unlink()
    else:
        target.write_text("Tampered", encoding="utf-8")
    with pytest.raises(ValueError):
        execute(store, spec)
    with pytest.raises(ValueError):
        store.read(artifact["id"])
    assert not target.exists() if remove else target.read_text(encoding="utf-8") == "Tampered"


def test_foreign_output_is_never_overwritten(workspace: Path, tmp_path: Path) -> None:
    artifact_id = hashlib.sha256(b"request").hexdigest()[:32]
    target = workspace / "assistant-artifacts" / artifact_id / "digest.md"
    target.parent.mkdir(parents=True)
    target.write_text("Existing file", encoding="utf-8")
    store = DocumentArtifacts(tmp_path / "data", workspace)
    spec = prepare({"source_paths": ["notes.md"]}, workspace)
    with pytest.raises(ValueError, match="already exists"):
        execute(store, spec)
    assert target.read_text(encoding="utf-8") == "Existing file"


def test_crash_after_publish_recovers_in_another_process(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    store = DocumentArtifacts(data_dir, workspace)
    spec = prepare({"source_paths": ["notes.md"]}, workspace)

    def crash(db: sqlite3.Connection, artifact_id: str) -> None:
        raise RuntimeError("Simulated process loss after publication")

    monkeypatch.setattr(store, "_mark_published", crash)
    with pytest.raises(RuntimeError, match="process loss"):
        execute(store, spec)
    artifact_id = hashlib.sha256(b"request").hexdigest()[:32]
    target = workspace / "assistant-artifacts" / artifact_id / "digest.md"
    before = target.stat().st_mtime_ns
    # Recovery uses the persisted source snapshot, including if sources moved.
    (workspace / "notes.md").unlink()
    payload = tmp_path / "replay.json"
    payload.write_text(
        json.dumps({"spec": spec.model_dump(), "workspace": str(workspace), "data": str(data_dir)}),
        encoding="utf-8",
    )
    script = """
import json, sys
from pathlib import Path
from nanomuse.assistant.documents import DocumentArtifacts
p = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
s = DocumentArtifacts(Path(p['data']), Path(p['workspace']))
artifact = s.execute(p['spec'], 'request', 'task', 1, 'step')
assert s.read(artifact['id'])['verified']
print(artifact['id'])
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(payload)], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == artifact_id
    assert target.stat().st_mtime_ns == before
    assert store.read(artifact_id)["verified"]


def test_concurrent_dispatch_publishes_once_and_request_conflicts(
    workspace: Path, tmp_path: Path
) -> None:
    spec = prepare({"source_paths": ["notes.md"]}, workspace)
    store = DocumentArtifacts(tmp_path / "data", workspace)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: execute(store, spec), range(4)))
    assert all(result == results[0] for result in results)
    assert list((workspace / "assistant-artifacts").glob("*/*.md")) == [
        workspace / results[0]["path"]
    ]
    with pytest.raises(ValueError, match="different inputs"):
        store.execute(spec, "request", "task", 2, "step")
    with pytest.raises(ValueError):
        store.read("../notes.md")
