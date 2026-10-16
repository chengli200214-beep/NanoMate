"""Driver readiness reads installation metadata and never launches a GUI or model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.assistant.drivers import disabled_profile, load_profile, readiness
from evals.assistant.freeze import (
    approval_template,
    configure_drivers,
    digest,
    file_hash,
    prepare,
    verify,
)
from evals.assistant.runner import _settings, retire_unapproved_case
from nanomuse.config import LLMSettings


def slot() -> LLMSettings:
    return LLMSettings(max_retries=0, tool_mode="native", vision="off", stream=False, timeout=120)


@pytest.fixture
def driver_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "nanomuse").mkdir(parents=True)
    directory = tmp_path / ".local" / "review"
    prepare(root, directory, slot(), "synthetic_materials_disclosed")
    return directory


@pytest.mark.parametrize(
    "content",
    [
        '[llm]\nmodel="unreviewed"',
        '[browser]\nenabled=true\nbackend="device"',
        '[hands]\nenabled=true\nbackend="desktop"',
        '[browser]\nenabled="true"',
        '[browser]\napi_key="forbidden-field"',
    ],
)
def test_profile_rejects_credential_or_unreviewed_driver_fields(
    tmp_path: Path, content: str
) -> None:
    config = tmp_path / "driver.toml"
    config.write_text(content, "utf-8")
    with pytest.raises(ValueError):
        load_profile(config)


def test_disabled_readiness_needs_no_package_import_or_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any) -> Any:
        raise AssertionError("Disabled readiness must not inspect a GUI dependency")

    monkeypatch.setattr("importlib.metadata.version", forbidden)
    monkeypatch.setattr("importlib.util.find_spec", forbidden)
    assert readiness(disabled_profile()) == {
        "ready": True,
        "problems": [],
        "versions": {},
        "dispatches": 0,
        "interactive_session_verified": False,
    }


def test_explicit_browser_profile_binds_executable_and_detects_change(
    driver_bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "drivers.toml"
    config.write_text('[browser]\nenabled=true\nbackend="playwright"\nheadless=true', "utf-8")
    executable = tmp_path / "browsers" / "chromium-test" / "chrome-win64" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"Synthetic executable, never launched")
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test-version")
    # Package versions are frozen by prepare too, so refresh this synthetic test
    # bundle under the same deterministic metadata patch before configuring.
    from evals.assistant.freeze import refresh_sources

    refresh_sources(driver_bundle)
    status = configure_drivers(driver_bundle, config, tmp_path / "browsers")
    assert status["ready"] and status["dispatches"] == 0
    frozen = json.loads((driver_bundle / "freeze.json").read_text("utf-8"))
    verify(frozen)
    settings = _settings(tmp_path / "runtime", frozen, 8792)
    assert settings.browser.enabled and settings.browser.backend == "playwright"
    assert not settings.hands.enabled
    executable.write_bytes(b"Changed fixture executable")
    with pytest.raises(ValueError, match="installation changed"):
        verify(frozen)


@pytest.mark.parametrize("barrier", ["approval.json", "budget.sqlite3"])
def test_profile_cannot_change_an_approved_or_started_batch(
    driver_bundle: Path, tmp_path: Path, barrier: str
) -> None:
    (driver_bundle / barrier).touch()
    with pytest.raises(ValueError, match="cannot change drivers"):
        configure_drivers(driver_bundle, tmp_path / "unused.toml", None)


def test_reprepare_retains_original_data_and_requires_a_new_review(driver_bundle: Path) -> None:
    directory = driver_bundle
    case = directory / "cases" / "D6-r1"
    case.mkdir(parents=True)
    receipt = case / "fixture.json"
    receipt.write_text('{"provider_requests":0}', "utf-8")
    original = receipt.read_bytes()
    frozen = json.loads((directory / "freeze.json").read_text("utf-8"))
    frozen["case_fixtures"] = {"D6-r1": {"path": str(receipt), "sha256": file_hash(receipt)}}
    frozen.pop("freeze_sha256")
    frozen["freeze_sha256"] = digest(frozen)
    (directory / "freeze.json").write_text(json.dumps(frozen), "utf-8")
    record = retire_unapproved_case(directory, "D6-r1")
    assert not case.exists()
    assert (Path(record["path"]) / "fixture.json").read_bytes() == original
    refreshed = json.loads((directory / "freeze.json").read_text("utf-8"))
    verify(refreshed)
    assert refreshed["case_fixtures"] == {}
    assert refreshed["retired_case_preparations"] == [record]
    assert approval_template(refreshed)["status"] == "pending"


@pytest.mark.parametrize("barrier", ["approval.json", "budget.sqlite3"])
def test_reprepare_never_retires_started_or_approved_data(
    driver_bundle: Path, barrier: str
) -> None:
    (driver_bundle / barrier).touch()
    with pytest.raises(ValueError, match="cannot be replaced"):
        retire_unapproved_case(driver_bundle, "D6-r1")
