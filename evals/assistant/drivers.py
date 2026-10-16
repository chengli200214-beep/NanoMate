"""Freeze explicit local drivers without loading model credentials or dispatching GUI input."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import sys
import tomllib
from pathlib import Path
from typing import Any


def disabled_profile() -> dict[str, Any]:
    return {"browser": {"enabled": False}, "hands": {"enabled": False}}


def load_profile(path: Path, browsers: Path | None = None) -> dict[str, Any]:
    """Only accept the existing deterministic driver switches, never a model config."""
    from evals.assistant.freeze import file_hash

    raw = tomllib.loads(path.read_text("utf-8"))
    allowed = {"browser": {"enabled", "headless", "backend"}, "hands": {"enabled", "backend"}}
    if set(raw) - allowed.keys() or any(
        not isinstance(value, dict) or set(value) - allowed[key] for key, value in raw.items()
    ):
        raise ValueError("Driver profile may contain only browser and hands switches")
    profile = disabled_profile()
    for name, values in raw.items():
        if "enabled" in values and type(values["enabled"]) is not bool:
            raise ValueError("Driver enable switches must be booleans")
        profile[name].update(values)
    browser, hands = profile["browser"], profile["hands"]
    if browser.get("enabled"):
        if (
            browser.get("backend") != "playwright"
            or type(browser.get("headless", True)) is not bool
        ):
            raise ValueError("Use the explicit local Playwright browser driver")
        if browsers is None:
            raise ValueError("Record an explicit Playwright browser installation directory")
        directory = browsers.resolve(strict=True)
        executables = sorted(
            [*directory.glob("chromium-*/chrome-win*/chrome.exe")]
            + [*directory.glob("chromium_headless_shell-*/*/chrome-headless-shell.exe")]
        )
        if not executables:
            raise ValueError("The supplied browser directory has no installed Chromium executable")
        profile["browser_runtime"] = {
            "path": str(directory),
            "executables": {
                str(item.relative_to(directory)): file_hash(item) for item in executables
            },
        }
    if hands.get("enabled") and hands.get("backend") != "pyautogui":
        raise ValueError("Use the explicit bounded Windows pyautogui hands driver")
    profile["source_sha256"] = file_hash(path)
    return profile


def readiness(profile: dict[str, Any]) -> dict[str, Any]:
    """Read installation metadata only. No browser, app, screenshot or input is started."""
    from evals.assistant.freeze import file_hash

    missing: list[str] = []
    versions = {}
    browser, hands = profile.get("browser", {}), profile.get("hands", {})
    packages = ["playwright"] if browser.get("enabled") else []
    if hands.get("enabled"):
        packages += ["pyautogui", "mss", "pillow"]
        if sys.platform != "win32":
            missing.append("Windows interactive desktop required for the native adapter")
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(f"Missing installed dependency: {package}")
    if browser.get("enabled"):
        runtime = profile.get("browser_runtime", {})
        directory = Path(runtime.get("path", "."))
        if not runtime.get("executables"):
            missing.append("No frozen browser executable")
        for relative, expected in runtime.get("executables", {}).items():
            target = directory / relative
            if not target.is_file() or file_hash(target) != expected:
                missing.append("Frozen browser executable missing or changed")
    if hands.get("enabled") and importlib.util.find_spec("pyautogui") is None:
        missing.append("pyautogui module unavailable")
    return {
        "ready": not missing,
        "problems": missing,
        "versions": versions,
        "dispatches": 0,
        "interactive_session_verified": False,
    }


def activate(profile: dict[str, Any]) -> None:
    status = readiness(profile)
    if not status["ready"]:
        raise ValueError(
            "Frozen deterministic drivers are unavailable: " + "; ".join(status["problems"])
        )
    if profile.get("browser", {}).get("enabled"):
        # This affects only the isolated runner process, not another service's environment.
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = profile["browser_runtime"]["path"]
