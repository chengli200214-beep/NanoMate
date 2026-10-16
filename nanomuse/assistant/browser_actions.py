"""One browser action per ledger intent, using the host browser and Sentinel.

Observation tokens are short lived, process local and single use. A restart, takeover,
page change or approval wait never makes an old element number safe to replay.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nanomuse.schema import Function, RiskLevel, ToolCall, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.tools.browser import Browser
from nanomuse.tools.browser_backends import BrowserBackend
from nanomuse.tools.web import host_of

_INTERACTIONS = {"fill", "click"}
_OBSERVATION_TTL = 300.0
_SENSITIVE = re.compile(
    r"password|passwd|passcode|one.?time|\botp\b|captcha|credit.?card|card.?number|"
    r"\bcvv\b|\bcvc\b|\bcc-(number|csc|exp|name)\b|security.?code|payment|pay.?now|checkout|purchase|buy.?now|"
    r"\bsubmit\b|\bsend\b|\bdelete\b|\bremove\b|\blogout\b|\blogoff\b|"
    r"密码|验证码|校验码|支付|付款|银行卡|信用卡|提交|发送|购买|删除|注销|退出登录",
    re.IGNORECASE,
)

# Keep the numbering identical to Browser._state. Read values only for ordinary draft
# fields. Passwords and security inputs never enter an observation or its receipt.
_OBSERVE_JS = r"""
() => {
  const selector = 'a[href], button, input, textarea, select, summary, [role=button], [role=link], [role=tab], [role=menuitem], [onclick], [contenteditable=true]';
  const sensitive = /password|passwd|passcode|one.?time|otp|captcha|credit.?card|card.?number|cvv|cvc|cc-(number|csc|exp|name)|security.?code|payment|密码|验证码|校验码|支付|付款|银行卡|信用卡/i;
  const visible = (e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  document.querySelectorAll('[data-om-idx]').forEach(e => e.removeAttribute('data-om-idx'));
  const elements = Array.from(document.querySelectorAll(selector)).filter(visible).slice(0, 150).map((e, i) => {
    e.setAttribute('data-om-idx', String(i));
    const label = (e.innerText || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('title') || e.getAttribute('name') || '').trim().replace(/\s+/g, ' ').slice(0, 200);
    const details = [e.type, e.name, e.id, e.autocomplete, label].join(' ');
    const secret = sensitive.test(details);
    const r = e.getBoundingClientRect();
    return {index: i, tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
      label, href: e.getAttribute('href') || '', name: e.getAttribute('name') || '',
      autocomplete: e.getAttribute('autocomplete') || '', sensitive: secret,
      disabled: !!e.disabled, readonly: !!e.readOnly,
      scripted: !!e.getAttribute('onclick'), editable: !!e.isContentEditable,
      value: secret ? null : ('value' in e ? String(e.value).slice(0, 10001) : null),
      rect: {x:r.x, y:r.y, width:r.width, height:r.height}};
  });
  const active = document.activeElement;
  return {text: (document.body ? document.body.innerText : '').slice(0, 200001), elements,
    focus: active ? {tag: active.tagName.toLowerCase(), index: active.getAttribute('data-om-idx')} : null,
    focused: document.hasFocus(), visibility: document.visibilityState,
    viewport: {width: innerWidth, height: innerHeight, scale: devicePixelRatio},
    scroll: {x:scrollX, y:scrollY}, ready_state:document.readyState};
}
"""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(c.isspace() for c in value)
    ):
        raise ValueError("Use an HTTP(S) URL without embedded credentials")
    return value


class BrowserSpec(BaseModel):
    """An immutable browser step. Draft filling never presses Enter or submits a form."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["navigate", "extract", "fill", "click"]
    url: str | None = Field(default=None, max_length=4096)
    index: int | None = Field(default=None, ge=0, le=149)
    text: str | None = Field(default=None, max_length=10000)
    expected_url: str | None = Field(default=None, max_length=4096)
    observation_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_action(self) -> BrowserSpec:
        if self.url is not None:
            _url(self.url)
        if self.expected_url is not None:
            _url(self.expected_url)
        if self.action == "navigate":
            if self.url is None or any(
                value is not None
                for value in (self.index, self.text, self.expected_url, self.observation_hash)
            ):
                raise ValueError("Navigation needs only a URL")
        elif self.action == "extract":
            if any(value is not None for value in (self.url, self.index, self.text)):
                raise ValueError("Extraction does not accept interaction arguments")
        elif (
            self.url is not None
            or self.index is None
            or self.expected_url is None
            or self.observation_hash is None
            or (self.action == "fill" and self.text is None)
            or (self.action == "click" and self.text is not None)
        ):
            raise ValueError("An interaction needs a fresh observation, URL and element index")
        return self


class _BrowserLedgerTool(BaseTool):
    """Keep the original tool name, targets and risk on every actual browser action."""

    name: str = "browser"
    description: str = "Execute one observed browser action from the task ledger."
    risk: RiskLevel = RiskLevel.MODERATE
    reads_private_data: bool = True
    driver: Any
    spec: BrowserSpec

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        original = self.driver.browser().assess(self.driver.tool_arguments(self.spec))
        values = original.model_dump()
        values["reads_private_data"] = True
        if self.spec.action in _INTERACTIONS:
            values["risk"] = RiskLevel.SENSITIVE
            values["egress"] = True
            values["egress_target"] = self.driver.interaction_host(self.spec)
            values["target"] = values["egress_target"]
            values["warnings"] = [
                "A draft field may save immediately on the website. Form submission, "
                "credentials, payments and destructive controls are not supported here."
            ]
        return CallAssessment.model_validate(values)

    async def execute(self, **_: Any) -> ToolResult:
        try:
            result = await self.driver._execute(self.spec)
            return ToolResult(output=json.dumps(result, ensure_ascii=False))
        except (ValueError, RuntimeError, LookupError) as exc:
            return ToolResult.fail(str(exc))


class _BrowserObservationTool(BaseTool):
    name: str = "assistant_observe_browser"
    description: str = "Read the current browser page without interacting with it."
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    driver: Any

    async def execute(self, **_: Any) -> ToolResult:
        try:
            observation = await self.driver._observe()
            return ToolResult(output=json.dumps(observation, ensure_ascii=False))
        except (ValueError, RuntimeError, LookupError) as exc:
            return ToolResult.fail(str(exc))


class BrowserActions:
    """A stateless action adapter plus a single fresh observation capability.

    The ledger owns durable intent and recovery. This adapter deliberately cannot replay
    a previous interaction after a process restart or certify that a remote write failed.
    """

    def __init__(self, service: Any, workspace: Path):
        self.service = service
        self.workspace = Path(workspace)
        self._observation: dict[str, Any] | None = None
        self._manual = False
        self.can_execute: Callable[[], bool] | None = None

    def browser(self) -> Browser:
        browser = self.service.browser
        if browser is None:
            raise ValueError("Enable the browser tool before creating browser tasks")
        return browser  # type: ignore[no-any-return]

    def available(self) -> bool:
        try:
            return bool(self.browser().backend_kind)
        except ValueError:
            return False

    def invalidate(self) -> None:
        self._observation = None

    def _held(self, browser: Browser) -> bool:
        return bool(
            self._manual
            or (
                browser.holds is not None
                and any(hold.tool == "browser" for hold in browser.holds.active())
            )
        )

    def _check_running(self) -> None:
        if self.can_execute is not None and not self.can_execute():
            self.invalidate()
            raise ValueError("Task paused, cancelled or handed over before the browser action")

    def tool_arguments(self, spec: BrowserSpec) -> dict[str, Any]:
        if spec.action == "fill":
            return {"action": "type", "index": spec.index, "text": spec.text, "submit": False}
        if spec.action == "click":
            # Following an ordinary anchor through navigate avoids page click handlers.
            target = self._pending_element(spec)
            href = urljoin(spec.expected_url or "", str(target.get("href", "")))
            return {"action": "navigate", "url": href}
        return {"action": spec.action, **({"url": spec.url} if spec.url else {})}

    def interaction_host(self, spec: BrowserSpec) -> str | None:
        return host_of(str(self.tool_arguments(spec).get("url") or spec.expected_url or ""))

    def _pending_element(self, spec: BrowserSpec) -> dict[str, Any]:
        if self._observation is None:
            return {}
        return next((e for e in self._observation["elements"] if e.get("index") == spec.index), {})

    async def execute(self, spec: BrowserSpec) -> dict[str, Any]:
        browser = self.browser()
        if self._held(browser):
            self.invalidate()
            raise ValueError("The user controls the browser; hand it back and observe again")
        tool = _BrowserLedgerTool(driver=self, spec=spec)
        result = await self.service.app.sentinel.guard(
            ToolCall(
                function=Function(name="browser", arguments=json.dumps(self.tool_arguments(spec)))
            ),
            tool,
        )
        if result.error:
            self.invalidate()
            raise ValueError(result.error)
        return json.loads(result.output)  # type: ignore[no-any-return]

    async def observe(self) -> dict[str, Any]:
        self.browser()
        tool = _BrowserObservationTool(driver=self)
        result = await self.service.app.sentinel.guard(
            ToolCall(function=Function(name=tool.name, arguments="{}")), tool
        )
        if result.error:
            raise ValueError(result.error)
        return json.loads(result.output)  # type: ignore[no-any-return]

    async def _observe(self) -> dict[str, Any]:
        browser = self.browser()
        async with browser.lock:
            backend = browser.backend
            if backend is None or not backend.open:
                raise ValueError("No browser page is open; create a navigation task first")
            state = await self._capture(browser, backend)
            if self._held(browser):
                self.invalidate()
                return {**state, "control": "user", "interactive": False}
            return {**self._remember(state), "control": "agent", "interactive": True}

    async def takeover(self, reason: str = "", thread: str = "main") -> dict[str, Any]:
        self._manual = True
        self.invalidate()
        browser = self.browser()
        if browser.holds is None:
            return {"control": "user", "thread": thread}
        return browser.holds.open(thread, "browser", by="user", reason=reason).to_event()

    async def release(self, thread: str = "main") -> dict[str, Any]:
        browser = self.browser()
        if browser.holds is not None:
            browser.holds.release(thread, "browser")
        self._manual = False
        self.invalidate()
        return await self.observe()

    async def _capture(self, browser: Browser, backend: BrowserBackend) -> dict[str, Any]:
        data = await backend.evaluate(_OBSERVE_JS)
        if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
            raise ValueError("Browser backend did not return a verifiable page observation")
        text = str(data.pop("text", ""))
        if len(text) > 200000:
            raise ValueError("Page is too large for a complete interaction observation")
        return {
            "url": await backend.url(),
            "title": await backend.title(),
            "body_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "text": text[:20000],
            **data,
            "user_actions": list(browser._user_actions),
            "backend": backend.kind,
        }

    def _remember(self, state: dict[str, Any]) -> dict[str, Any]:
        observed = time.time()
        self._observation = {
            **state,
            "state_hash": _digest(state),
            "observation_hash": _digest({"state": state, "nonce": secrets.token_hex(24)}),
            "observed_at": observed,
            "expires_at": observed + _OBSERVATION_TTL,
        }
        return self._observation

    async def _execute(self, spec: BrowserSpec) -> dict[str, Any]:
        browser = self.browser()
        async with browser.lock:
            self._check_running()
            if self._held(browser):
                self.invalidate()
                raise ValueError("Browser is under human control; a fresh observation is required")
            backend = browser._pick_backend()
            await backend.ensure()
            before: dict[str, Any] | None = None
            args = self.tool_arguments(spec)
            if spec.action in _INTERACTIONS:
                before = self._observation
                self.invalidate()  # consume even a rejected token, never retry by index
                if (
                    before is None
                    or before["observation_hash"] != spec.observation_hash
                    or before["expires_at"] < time.time()
                    or before["url"] != spec.expected_url
                ):
                    raise ValueError("Observation expired or was consumed; observe the page again")
                current = await self._capture(browser, backend)
                if _digest(current) != before["state_hash"]:
                    raise ValueError("Page, focus or viewport changed; observe the page again")
                if (
                    not current.get("focused")
                    or current.get("visibility") != "visible"
                    or current.get("ready_state") != "complete"
                ):
                    raise ValueError("Page is not focused, visible and fully loaded")
                element = next(
                    (e for e in current["elements"] if e.get("index") == spec.index), None
                )
                if element is None:
                    raise ValueError("Element is no longer present")
                self._validate_element(spec, element)
                self._check_running()
                if self._held(browser):
                    raise ValueError("User took over before the browser action")
            else:
                self.invalidate()
            result = await browser._act(
                backend,
                str(args["action"]),
                args.get("url"),
                args.get("index"),
                args.get("text"),
                False,
                None,
                "down",
            )
            if result.error:
                raise ValueError(result.error)
            after = await self._capture(browser, backend)
            if spec.action == "fill":
                field: dict[str, Any] = next(
                    (e for e in after["elements"] if e.get("index") == spec.index), {}
                )
                original: dict[str, Any] = next(
                    (e for e in (before or {}).get("elements", []) if e.get("index") == spec.index),
                    {},
                )
                if (
                    field.get("value") != spec.text
                    or after["url"] != spec.expected_url
                    or any(
                        field.get(key) != original.get(key)
                        for key in ("tag", "type", "name", "label", "autocomplete")
                    )
                ):
                    raise ValueError(
                        "Draft field value could not be verified; do not retry blindly"
                    )
            return {
                "action": spec.action,
                "effective_action": args["action"],
                "before_hash": before["state_hash"] if before else None,
                "observation": self._remember(after),
                "text": after["text"],
                "verification": "browser_fresh_observation",
                "explicit_submit": False,
                "remote_effects": "not_verified"
                if spec.action in _INTERACTIONS
                else "not_requested",
            }

    @staticmethod
    def _validate_element(spec: BrowserSpec, element: dict[str, Any]) -> None:
        description = " ".join(
            str(element.get(key) or "") for key in ("type", "label", "name", "autocomplete", "href")
        )
        if element.get("sensitive") or _SENSITIVE.search(description):
            raise ValueError(
                "This control needs the user: credentials, submission or sensitive action"
            )
        if element.get("disabled") or element.get("readonly"):
            raise ValueError("Control is disabled or read-only")
        if spec.action == "fill":
            if element.get("tag") not in {"input", "textarea"} or element.get("type", "") not in {
                "",
                "text",
                "search",
                "email",
                "tel",
                "url",
                "number",
            }:
                raise ValueError("Only ordinary text draft fields are supported")
        else:
            if element.get("tag") != "a" or element.get("scripted"):
                raise ValueError(
                    "Only ordinary navigation links are supported; buttons need the user"
                )
            target = urljoin(spec.expected_url or "", str(element.get("href") or ""))
            _url(target)
            if not element.get("href") or str(element["href"]).startswith("#"):
                raise ValueError("This link is not an HTTP(S) page navigation")
