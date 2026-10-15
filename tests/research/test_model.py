"""A CLI uses the selected workbench overlay and resolves its encrypted key in memory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from nanomuse.config import Settings, save_app_settings
from nanomuse.research.model import (
    create_research_llm,
    load_research_settings,
    research_model_ready,
)
from nanomuse.vault import CredentialVault


def test_selected_data_directory_loads_its_own_overlay_and_restores_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, selected = tmp_path / "other", tmp_path / "selected"
    save_app_settings(original, {"llm": {"model": "other-model"}})
    save_app_settings(selected, {"llm": {"model": "selected-model"}})
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(original))
    settings = load_research_settings(data_dir=selected)
    assert settings.data_dir == selected
    assert settings.llm.model == "selected-model"
    assert os.environ["NANOMUSE_DATA_DIR"] == str(original)


def test_missing_vault_reference_is_not_ready_and_makes_no_model_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanomuse.llm.factory as factory

    settings = Settings(data_dir=tmp_path)
    settings.llm.api_key = "{{vault:LLM_API_KEY}}"
    monkeypatch.setattr(factory, "create_llm", lambda *a, **k: pytest.fail("created a client"))
    assert research_model_ready(settings) is False
    with pytest.raises(ValueError, match="Configure a model"):
        create_research_llm(settings)
    assert not settings.vault_file.exists()
    assert not settings.vault_key_file.exists()


def test_vault_reference_is_resolved_only_in_model_client_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanomuse.llm.factory as factory
    from nanomuse.llm.mock import MockLLM

    settings = Settings(data_dir=tmp_path)
    settings.llm.api_key = "{{vault:LLM_API_KEY}}"
    vault = CredentialVault(settings.vault_file, settings.vault_key_file)
    vault.set("LLM_API_KEY", "synthetic-test-value")
    received = []

    def build(slot: Any, data_dir: Any) -> MockLLM:
        received.append((slot, data_dir))
        return MockLLM([])

    monkeypatch.setattr(factory, "create_llm", build)
    assert research_model_ready(settings) is True
    create_research_llm(settings)
    assert received[0][0].api_key == "synthetic-test-value"
    assert received[0][1] == tmp_path
    assert settings.llm.api_key == "{{vault:LLM_API_KEY}}"
