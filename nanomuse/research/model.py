"""Load the local workbench's model slot without persisting resolved credentials."""

from __future__ import annotations

import os
from pathlib import Path

from nanomuse.config import Settings, load_settings
from nanomuse.llm.base import BaseLLM
from nanomuse.vault import CredentialVault
from nanomuse.vault.vault import VaultError


def load_research_settings(config: Path | None = None, data_dir: Path | None = None) -> Settings:
    """CLI entry points select the app-settings overlay before loading configuration."""
    if data_dir is None:
        return load_settings(config)
    previous = os.environ.get("NANOMUSE_DATA_DIR")
    try:
        os.environ["NANOMUSE_DATA_DIR"] = str(data_dir.expanduser().resolve())
        return load_settings(config)
    finally:
        if previous is None:
            os.environ.pop("NANOMUSE_DATA_DIR", None)
        else:
            os.environ["NANOMUSE_DATA_DIR"] = previous


def research_model_ready(settings: Settings) -> bool:
    from nanomuse.llm.factory import llm_ready

    vault = CredentialVault(settings.vault_file, settings.vault_key_file)
    return llm_ready(settings.llm, vault=vault, data_dir=settings.data_dir)


def create_research_llm(settings: Settings) -> BaseLLM:
    from nanomuse.llm.factory import create_llm

    if not research_model_ready(settings):
        raise ValueError("Configure a model in the local workbench before live acceptance.")
    slot = settings.llm.model_copy(deep=True)
    vault = CredentialVault(settings.vault_file, settings.vault_key_file)
    if vault.has_placeholders(slot.api_key):
        try:
            slot.api_key = vault.resolve(slot.api_key)
        except VaultError as exc:
            raise ValueError("The configured model credential could not be loaded.") from exc
    return create_llm(slot, data_dir=settings.data_dir)
