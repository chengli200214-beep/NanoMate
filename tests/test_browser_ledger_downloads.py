"""Unexpected page attachments cannot bypass a ledger download's explicit path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanomuse.assistant.browser_actions import BrowserSpec
from nanomuse.tools.browser_backends import PlaywrightBackend
from tests.test_assistant_browser import fixture


class Download:
    suggested_filename = "offered.bin"

    def __init__(self, *, fail_cancel: bool = False):
        self.cancelled = 0
        self.saved: list[str] = []
        self.fail_cancel = fail_cancel

    async def cancel(self) -> None:
        self.cancelled += 1
        if self.fail_cancel:
            raise RuntimeError("fixture cancellation unavailable")

    async def save_as(self, path: str) -> None:
        self.saved.append(path)
        Path(path).write_bytes(b"offered attachment")


async def test_handler_captures_ledger_owner_before_background_callback_runs(
    tmp_path: Path,
) -> None:
    backend = PlaywrightBackend(tmp_path / "browser-profile")
    offered = Download()
    checkpoint = backend.begin_ledger_action()
    backend._on_download(offered)
    # A user action can happen before the background cancel coroutine starts. The
    # attachment's policy was already captured by the synchronous event callback.
    backend.allow_legacy_downloads()
    assert await backend.finish_ledger_action(checkpoint)
    assert offered.cancelled == 1
    assert offered.saved == []
    assert not backend.downloads_dir.exists()


async def test_delayed_attachment_stays_blocked_after_action_finishes(tmp_path: Path) -> None:
    backend = PlaywrightBackend(tmp_path / "browser-profile")
    checkpoint = backend.begin_ledger_action()
    assert not await backend.finish_ledger_action(checkpoint)
    offered = Download()
    backend._on_download(offered)
    await asyncio.sleep(0)
    assert await backend.finish_ledger_action(backend.begin_ledger_action())
    assert offered.cancelled == 1
    assert offered.saved == []


async def test_cancellation_failure_never_falls_back_to_saving(tmp_path: Path) -> None:
    backend = PlaywrightBackend(tmp_path / "browser-profile")
    checkpoint = backend.begin_ledger_action()
    offered = Download(fail_cancel=True)
    backend._on_download(offered)
    assert await backend.finish_ledger_action(checkpoint)
    assert offered.cancelled == 1
    assert offered.saved == []
    assert not backend.downloads_dir.exists()


async def test_explicit_nonledger_policy_keeps_existing_download_behavior(tmp_path: Path) -> None:
    backend = PlaywrightBackend(tmp_path / "browser-profile")
    backend.begin_ledger_action()
    backend.allow_legacy_downloads()
    offered = Download()
    backend._on_download(offered)
    for _ in range(10):
        if offered.saved:
            break
        await asyncio.sleep(0)
    assert offered.cancelled == 0
    assert offered.saved == [str(backend.downloads_dir / "offered.bin")]
    assert (backend.downloads_dir / "offered.bin").read_bytes() == b"offered attachment"


async def test_ledger_navigation_attachment_is_rejected_and_not_false_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, page, _, _ = fixture(tmp_path)
    backend = PlaywrightBackend(tmp_path / "browser-profile")
    offered = Download()
    original_goto = page.goto

    async def goto(url: str) -> None:
        backend._on_download(offered)
        await original_goto(url)

    monkeypatch.setattr(page, "begin_ledger_action", backend.begin_ledger_action)
    monkeypatch.setattr(page, "finish_ledger_action", backend.finish_ledger_action)
    monkeypatch.setattr(page, "goto", goto)
    with pytest.raises(ValueError, match="unexpected attachment"):
        await driver.execute(BrowserSpec(action="navigate", url="https://example.test/offered"))
    assert offered.cancelled == 1
    assert offered.saved == []
    assert driver._observation is None
    assert not backend.downloads_dir.exists()


async def test_generic_browser_read_keeps_suppression_and_explicit_input_resets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, page, browser, _ = fixture(tmp_path)
    resets: list[bool] = []
    monkeypatch.setattr(page, "allow_legacy_downloads", lambda: resets.append(True))
    assert not (await browser.execute(action="extract")).error
    assert resets == []
    assert not (await browser.execute(action="navigate", url="https://example.test/new")).error
    assert resets == [True]
    await browser.user_action("look", "main")
    assert resets == [True]
    await browser.user_action("navigate", "main", url="https://example.test/user")
    assert resets == [True, True]
