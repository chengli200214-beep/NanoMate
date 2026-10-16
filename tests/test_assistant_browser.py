"""Browser ledger intents use current observations and the existing guarded browser."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from nanomuse.agent.holds import Holds
from nanomuse.assistant.browser_actions import _OBSERVE_JS, BrowserActions, BrowserSpec
from nanomuse.schema import RiskLevel, ToolCall, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.browser import _ANNOTATE_JS, Browser
from nanomuse.tools.browser_backends import BrowserBackend, BrowserProfile


class Page(BrowserBackend):
    kind = "test"

    def __init__(self) -> None:
        self.opened = True
        self.current_url = "https://example.test/"
        self.title_text = "Example"
        self.actions: list[tuple[str, Any]] = []
        self.state: dict[str, Any] = {
            "text": "Research draft",
            "elements": [
                {"index": 0, "tag": "a", "href": "/article", "label": "Read article"},
                {"index": 1, "tag": "input", "type": "text", "label": "Draft", "value": ""},
                {"index": 2, "tag": "button", "type": "submit", "label": "Submit"},
            ],
            "focus": {"tag": "body", "index": None},
            "focused": True,
            "visibility": "visible",
            "viewport": {"width": 1280, "height": 900, "scale": 1},
            "scroll": {"x": 0, "y": 0},
            "ready_state": "complete",
        }

    @property
    def open(self) -> bool:
        return self.opened

    async def ensure(self) -> None:
        self.opened = True

    async def goto(self, url: str) -> None:
        self.actions.append(("navigate", url))
        self.current_url = url

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if js == _OBSERVE_JS:
            return copy.deepcopy(self.state)
        if js == _ANNOTATE_JS:
            return [
                {
                    "i": e["index"],
                    "tag": e["tag"],
                    "type": e.get("type", ""),
                    "text": e.get("label", ""),
                    "href": e.get("href", ""),
                }
                for e in self.state["elements"]
            ]
        if "setter.call" in js:
            self.actions.append(("fill", (arg["index"], arg["text"])))
            self.state["elements"][arg["index"]]["value"] = arg["text"]
            self.state["focus"] = {"tag": "input", "index": str(arg["index"])}
            return True
        if "document.body" in js:
            return self.state["text"]
        return "Draft"

    async def click_at(self, x: float, y: float) -> None:
        self.actions.append(("click", (x, y)))

    async def type_text(self, text: str) -> None:
        self.actions.append(("type", text))

    async def press(self, key: str) -> None:
        self.actions.append(("key", key))

    async def scroll(self, dy: float) -> None:
        self.actions.append(("scroll", dy))

    async def back(self) -> None:
        self.actions.append(("back", None))

    async def screenshot(self, quality: int = 55) -> bytes:
        return b""

    async def url(self) -> str:
        return self.current_url

    async def title(self) -> str:
        return self.title_text

    async def settle(self, timeout_ms: int) -> None:
        pass

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("The ledger does not bypass page controls with fetch")

    async def set_profile(self, profile: BrowserProfile) -> None:
        self.profile = profile

    async def close(self) -> None:
        self.opened = False


class Guard:
    def __init__(self) -> None:
        self.calls: list[tuple[ToolCall, CallAssessment]] = []
        self.before: Any = None
        self.deny = False

    async def guard(self, call: ToolCall, tool: BaseTool) -> ToolResult:
        self.calls.append((call, tool.assess(call.arguments)))
        if self.before is not None:
            self.before()
        if self.deny:
            return ToolResult.fail("Denied by Sentinel")
        return await tool.execute(**call.arguments)


def fixture(tmp_path: Path) -> tuple[BrowserActions, Page, Browser, Guard]:
    page = Page()
    browser = Browser(workspace=tmp_path, holds=Holds())
    browser._backend = page
    guard = Guard()
    service = SimpleNamespace(browser=browser, app=SimpleNamespace(sentinel=guard))
    return BrowserActions(service, tmp_path), page, browser, guard


def interaction(observation: dict[str, Any], action: str = "fill", index: int = 1) -> BrowserSpec:
    return BrowserSpec.model_validate(
        {
            "action": action,
            "index": index,
            "expected_url": observation["url"],
            "observation_hash": observation["observation_hash"],
            **({"text": "My draft"} if action == "fill" else {}),
        }
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "press", "key": "Enter"},
        {"action": "fill", "index": 1, "text": "Draft", "submit": True},
        {"action": "navigate", "url": "file:///etc/passwd"},
        {"action": "navigate", "url": "https://user:secret@example.test/"},
        {"action": "navigate", "url": "https://example.test/", "index": 1},
        {"action": "fill", "index": True, "text": "draft"},
        {"action": "click", "index": 0},
        {"action": "download", "url": "https://example.test/file"},
    ],
)
def test_specs_do_not_admit_unobserved_or_unsupported_effects(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BrowserSpec.model_validate(payload)


async def test_navigate_extract_and_draft_use_browser_sentinel(tmp_path: Path) -> None:
    driver, page, _, guard = fixture(tmp_path)
    assert driver.available()
    navigation = await driver.execute(BrowserSpec(action="navigate", url="https://example.test/"))
    assert navigation["observation"]["url"] == "https://example.test/"
    observation = await driver.observe()
    assert len(observation["body_sha256"]) == 64
    result = await driver.execute(interaction(observation))
    assert result["observation"]["elements"][1]["value"] == "My draft"
    assert page.actions == [("navigate", "https://example.test/"), ("fill", (1, "My draft"))]
    assert len(guard.calls) == 3
    assert [call.name for call, _ in guard.calls] == [
        "browser",
        "assistant_observe_browser",
        "browser",
    ]
    assert guard.calls[-1][1].risk == RiskLevel.SENSITIVE
    assert guard.calls[-1][1].egress_target == "example.test"
    assert guard.calls[-1][1].reads_private_data
    assert json.loads(guard.calls[-1][0].function.arguments)["submit"] is False


async def test_one_observation_cannot_authorize_two_actions(tmp_path: Path) -> None:
    driver, page, _, _ = fixture(tmp_path)
    old = interaction(await driver.observe())
    await driver.execute(old)
    with pytest.raises(ValueError, match="expired or was consumed"):
        await driver.execute(old)
    assert page.actions == [("fill", (1, "My draft"))]


@pytest.mark.parametrize("change", ["body", "url", "focus", "viewport", "elements", "user"])
async def test_changes_during_sentinel_approval_stop_before_input(
    tmp_path: Path, change: str
) -> None:
    driver, page, browser, guard = fixture(tmp_path)
    spec = interaction(await driver.observe())

    def mutate() -> None:
        if change == "body":
            page.state["text"] += " changed"
        elif change == "url":
            page.current_url = "https://example.test/other"
        elif change == "focus":
            page.state["focus"] = {"tag": "input", "index": "2"}
        elif change == "viewport":
            page.state["viewport"]["scale"] = 1.25
        elif change == "elements":
            page.state["elements"][1]["label"] = "A different draft"
        else:
            browser._user_actions.append("typed")

    guard.before = mutate
    with pytest.raises(ValueError, match="changed"):
        await driver.execute(spec)
    assert not page.actions


async def test_expiry_and_restart_require_new_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, page, _, _ = fixture(tmp_path)
    obs = await driver.observe()
    monkeypatch.setattr(
        "nanomuse.assistant.browser_actions.time.time", lambda: obs["expires_at"] + 1
    )
    with pytest.raises(ValueError, match="expired"):
        await driver.execute(interaction(obs))
    restarted = BrowserActions(driver.service, tmp_path)
    with pytest.raises(ValueError, match="expired"):
        await restarted.execute(interaction(obs))
    assert not page.actions


@pytest.mark.parametrize(
    "element",
    [
        {"tag": "input", "type": "password", "label": "Password"},
        {"tag": "input", "type": "text", "name": "one_time_code"},
        {"tag": "input", "type": "text", "autocomplete": "cc-number"},
        {"tag": "input", "type": "text", "label": "付款卡号"},
        {"tag": "input", "type": "file", "label": "File"},
        {"tag": "input", "type": "text", "readonly": True},
        {"tag": "input", "type": "text", "disabled": True},
    ],
)
async def test_credential_payment_and_non_draft_fields_need_the_user(
    tmp_path: Path, element: dict[str, Any]
) -> None:
    driver, page, _, _ = fixture(tmp_path)
    page.state["elements"][1] = {"index": 1, **element}
    with pytest.raises(ValueError):
        await driver.execute(interaction(await driver.observe()))
    assert not page.actions


async def test_click_follows_anchor_without_running_page_click_handlers(tmp_path: Path) -> None:
    driver, page, _, guard = fixture(tmp_path)
    result = await driver.execute(interaction(await driver.observe(), "click", 0))
    assert page.actions == [("navigate", "https://example.test/article")]
    assert result["effective_action"] == "navigate"
    assert guard.calls[-1][1].risk == RiskLevel.SENSITIVE
    assert guard.calls[-1][1].target == "example.test"


@pytest.mark.parametrize(
    "element",
    [
        {"tag": "button", "type": "submit", "label": "Continue"},
        {"tag": "a", "href": "/pay", "label": "Pay now"},
        {"tag": "a", "href": "/delete", "label": "Remove"},
        {"tag": "a", "href": "javascript:doSomething()", "label": "Continue"},
        {"tag": "a", "href": "#", "label": "Continue"},
        {"tag": "a", "href": "/article", "label": "Continue", "scripted": True},
    ],
)
async def test_submission_and_scripted_controls_never_receive_automatic_clicks(
    tmp_path: Path, element: dict[str, Any]
) -> None:
    driver, page, _, _ = fixture(tmp_path)
    page.state["elements"][0] = {"index": 0, **element}
    with pytest.raises(ValueError):
        await driver.execute(interaction(await driver.observe(), "click", 0))
    assert not page.actions


async def test_takeover_invalidates_old_token_and_release_reobserves(tmp_path: Path) -> None:
    driver, page, browser, _ = fixture(tmp_path)
    old = interaction(await driver.observe())
    hold = await driver.takeover("Complete sign in")
    assert browser.holds.get(hold["id"]).open
    with pytest.raises(ValueError, match="user controls"):
        await driver.execute(old)
    page.current_url = "https://example.test/signed-in"
    fresh = await driver.release()
    assert fresh["url"] == page.current_url
    assert fresh["observation_hash"] != old.observation_hash
    with pytest.raises(ValueError, match="expired"):
        await driver.execute(old)
    assert not page.actions


async def test_takeover_in_different_chat_also_stops_ledger_input(tmp_path: Path) -> None:
    driver, page, browser, guard = fixture(tmp_path)
    spec = interaction(await driver.observe())
    guard.before = lambda: browser.holds.open("a-side-chat", "browser")
    with pytest.raises(ValueError, match="human control"):
        await driver.execute(spec)
    assert not page.actions


async def test_manual_observation_reads_without_issuing_an_input_token(tmp_path: Path) -> None:
    driver, page, _, guard = fixture(tmp_path)
    await driver.takeover("Finish sign in")
    observation = await driver.observe()
    assert observation["control"] == "user"
    assert observation["interactive"] is False
    assert "observation_hash" not in observation
    assert guard.calls[-1][0].name == "assistant_observe_browser"
    assert guard.calls[-1][1].risk == RiskLevel.SAFE
    assert not page.actions


async def test_read_observation_never_opens_a_browser_page(tmp_path: Path) -> None:
    driver, page, _, _ = fixture(tmp_path)
    page.opened = False
    with pytest.raises(ValueError, match="No browser page is open"):
        await driver.observe()
    assert not page.opened


async def test_paused_ledger_intent_after_approval_cannot_type(tmp_path: Path) -> None:
    driver, page, _, guard = fixture(tmp_path)
    spec = interaction(await driver.observe())
    running = True
    driver.can_execute = lambda: running

    def pause() -> None:
        nonlocal running
        running = False

    guard.before = pause
    with pytest.raises(ValueError, match="paused"):
        await driver.execute(spec)
    assert not page.actions


async def test_denial_does_not_touch_page(tmp_path: Path) -> None:
    driver, page, _, guard = fixture(tmp_path)
    spec = interaction(await driver.observe())
    guard.deny = True
    with pytest.raises(ValueError, match="Denied"):
        await driver.execute(spec)
    assert not page.actions


async def test_availability_does_not_claim_missing_browser(tmp_path: Path) -> None:
    driver = BrowserActions(SimpleNamespace(browser=None), tmp_path)
    assert not driver.available()
    with pytest.raises(ValueError, match="Enable the browser"):
        await driver.observe()
