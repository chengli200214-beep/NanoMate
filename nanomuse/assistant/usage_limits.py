"""Persistent admission for the isolated assistant's model requests and GUI actions.

The context reservation is the vendor's entire documented context, not a tokenizer
estimate. Unknown outcomes remain charged at that upper bound until reconciled.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nanomuse.config import AssistantUsageSettings, LLMSettings
from nanomuse.llm.base import BaseLLM, DeltaCallback
from nanomuse.llm.mock import MockLLM
from nanomuse.llm.openai_chat import OpenAIChatLLM
from nanomuse.schema import LLMResponse, Message

PRICE_POLICY = "deepseek-2026-10-16"
PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
CONTEXT_TOKENS = 1_048_576
_RATES = {"deepseek-flash": (2, 8), "deepseek-v4-pro": (9, 27)}
_owner: ContextVar[UsageOwner | None] = ContextVar("assistant_usage_owner", default=None)


class UsageStopped(RuntimeError):
    """A local refusal. Callers must show it without another model request."""

    code = "assistant_usage_stopped"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Assistant stopped: {reason}. / 助手已停止：{reason}。")


@dataclass(frozen=True)
class UsageOwner:
    turn_id: str
    task_id: str = ""
    run_id: str = ""
    background: bool = False
    root_id: str = ""

    def __post_init__(self) -> None:
        if not self.turn_id.strip() or any(
            len(value) > 200 for value in (self.turn_id, self.task_id, self.run_id, self.root_id)
        ):
            raise ValueError("A stable bounded usage owner is required")

    @property
    def budget_key(self) -> str:
        return self.root_id or self.turn_id


@contextmanager
def usage_owner(owner: UsageOwner) -> Iterator[None]:
    token = _owner.set(owner)
    try:
        yield
    finally:
        _owner.reset(token)


def current_usage_owner() -> UsageOwner | None:
    return _owner.get()


def strict_llm_settings(slot: LLMSettings, policy: AssistantUsageSettings) -> LLMSettings:
    """Run before client construction, including settings-driven replacements."""
    return slot.model_copy(
        deep=True,
        update={
            "max_retries": 0,
            "tool_mode": "native",
            "vision": "off",
            "stream": False,
            "max_tokens": min(max(1, slot.max_tokens), policy.request_output_tokens),
            "timeout": min(slot.timeout, policy.request_seconds),
        },
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _money(value: float) -> int:
    return int(Decimal(str(value)) * 1_000_000)


def _cost(input_tokens: int, output_tokens: int, rates: tuple[int, int]) -> int:
    # Integer micro-CNY. Rates are CNY per million tokens, so the factors cancel.
    return input_tokens * rates[0] + output_tokens * rates[1]


def _pricing(slot: LLMSettings, policy: AssistantUsageSettings) -> tuple[int, int]:
    if policy.price_policy != PRICE_POLICY:
        raise UsageStopped("configure the reviewed assistant_usage.price_policy")
    if slot.provider not in {"openai", "deepseek"} or (slot.base_url or "").rstrip("/") not in {
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    }:
        raise UsageStopped("the configured provider has no reviewed price/context policy")
    if slot.model not in _RATES:
        raise UsageStopped("the configured model has no reviewed price/context policy")
    return _RATES[slot.model]


class UsageLimits:
    """One shared database for main chat, research and background requests."""

    def __init__(
        self,
        path: Path,
        policy: AssistantUsageSettings,
        clock: Callable[[], float] = time.time,
    ):
        self.path, self.clock = path, clock
        self.policy = policy.model_copy(deep=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage_request ("
                "id TEXT PRIMARY KEY,owner TEXT NOT NULL,budget_key TEXT NOT NULL,"
                "day TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,"
                "policy TEXT NOT NULL,model TEXT NOT NULL,reserved_tokens INTEGER NOT NULL,"
                "reserved_cost INTEGER NOT NULL,charged_tokens INTEGER,charged_cost INTEGER,"
                "usage TEXT,error TEXT NOT NULL DEFAULT '')"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS gui_budget ("
                "root_id TEXT PRIMARY KEY,started REAL NOT NULL,deadline REAL NOT NULL,"
                "action_limit INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS gui_action ("
                "root_id TEXT NOT NULL,id TEXT NOT NULL,at REAL NOT NULL,"
                "PRIMARY KEY(root_id,id))"
            )
            # A new runtime cannot assume a previous process never reached the vendor.
            db.execute(
                "UPDATE usage_request SET status='unknown',error='interrupted_request' "
                "WHERE status='pending'"
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def update_policy(self, policy: AssistantUsageSettings) -> None:
        """New limits never erase spend, reservations, unknowns or GUI deadlines."""
        self.policy = policy.model_copy(deep=True)

    def validate_slot(self, slot: LLMSettings) -> None:
        """Check configuration before a caller constructs a provider client."""
        if self.policy.daily_cny <= 0 or self.policy.task_cny <= 0:
            raise UsageStopped("configure assistant_usage daily_cny and task_cny before paid use")
        _pricing(slot, self.policy)
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM usage_request WHERE status='unknown' LIMIT 1").fetchone():
                raise UsageStopped("reconcile unknown model usage before continuing")

    def _admit(
        self,
        db: sqlite3.Connection,
        owner: UsageOwner,
        slot: LLMSettings,
        output: int,
    ) -> tuple[AssistantUsageSettings, tuple[int, int], str, int, int]:
        policy = self.policy.model_copy(deep=True)
        if db.execute("SELECT 1 FROM usage_request WHERE status='unknown' LIMIT 1").fetchone():
            raise UsageStopped("reconcile unknown model usage before continuing")
        if policy.daily_cny <= 0 or policy.task_cny <= 0:
            raise UsageStopped("configure assistant_usage daily_cny and task_cny before paid use")
        if type(output) is not int or not 1 <= output <= policy.request_output_tokens:
            raise UsageStopped("the single request output token limit was exceeded")
        rates = _pricing(slot, policy)
        day = datetime.fromtimestamp(self.clock(), UTC).date().isoformat()
        tokens = CONTEXT_TOKENS + output
        cost = _cost(CONTEXT_TOKENS, output, rates)
        scopes = [
            ("day", day, policy.daily_requests, policy.daily_tokens, _money(policy.daily_cny)),
            (
                "budget_key",
                owner.budget_key,
                policy.task_requests,
                policy.task_tokens,
                _money(policy.task_cny),
            ),
        ]
        # A later user turn cannot give an existing task/run a fresh allowance.
        for kind, value in (("task_id", owner.task_id), ("run_id", owner.run_id)):
            if value:
                scopes.append(
                    (
                        f"json_extract(owner,'$.{kind}')",
                        value,
                        policy.task_requests,
                        policy.task_tokens,
                        _money(policy.task_cny),
                    )
                )
        for column, key, count_limit, token_limit, cost_limit in scopes:
            row = db.execute(
                "SELECT count(*) AS requests,"
                "coalesce(sum(coalesce(charged_tokens,reserved_tokens)),0) AS tokens,"
                "coalesce(sum(coalesce(charged_cost,reserved_cost)),0) AS cost "
                f"FROM usage_request WHERE {column}=?",
                (key,),
            ).fetchone()
            if row["requests"] + 1 > count_limit:
                raise UsageStopped(f"the {column} model request allowance was exhausted")
            if row["tokens"] + tokens > token_limit:
                raise UsageStopped(f"the {column} token allowance cannot cover this request")
            if row["cost"] + cost > cost_limit:
                raise UsageStopped(f"the {column} currency allowance cannot cover this request")
        return policy, rates, day, tokens, cost

    def reserve(self, owner: UsageOwner, slot: LLMSettings, output: int) -> str:
        with self.transaction() as db:
            policy, rates, day, tokens, cost = self._admit(db, owner, slot, output)
            request_id = uuid.uuid4().hex
            model: dict[str, Any] = {
                "provider": slot.provider,
                "base_url": slot.base_url,
                "model": slot.model,
            }
            model.update(context_tokens=CONTEXT_TOKENS, output_tokens=output, rates=rates)
            db.execute(
                "INSERT INTO usage_request "
                "(id,owner,budget_key,day,status,created,policy,model,reserved_tokens,reserved_cost) "
                "VALUES(?,?,?,?,'pending',?,?,?,?,?)",
                (
                    request_id,
                    json.dumps(asdict(owner), sort_keys=True),
                    owner.budget_key,
                    day,
                    self.clock(),
                    json.dumps({"digest": _digest(policy.model_dump()), **policy.model_dump()}),
                    json.dumps({"digest": _digest(model), **model}),
                    tokens,
                    cost,
                ),
            )
        return request_id

    def can_continue(self, budget_key: str, slot: LLMSettings) -> bool:
        try:
            with self.transaction() as db:
                self._admit(
                    db,
                    UsageOwner(turn_id=budget_key),
                    slot,
                    min(slot.max_tokens, self.policy.request_output_tokens),
                )
            return True
        except UsageStopped:
            return False

    def unknown(self, request_id: str, reason: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE usage_request SET status='unknown',error=? "
                "WHERE id=? AND status!='settled'",
                (reason[:150], request_id),
            )

    def settle(self, request_id: str, usage: dict[str, Any]) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM usage_request WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            if row["status"] == "settled":
                if json.loads(row["usage"]) != usage:
                    raise UsageStopped("conflicting settlement for an existing model request")
                return
            model = json.loads(row["model"])
            raw_prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
            raw_completion = usage.get("completion_tokens", usage.get("output_tokens"))
            prompt = raw_prompt if type(raw_prompt) is int else -1
            completion = raw_completion if type(raw_completion) is int else -1
            total = usage.get("total_tokens")
            valid = prompt >= 0 and completion >= 0
            valid = (
                valid and prompt <= model["context_tokens"] and completion <= model["output_tokens"]
            )
            valid = valid and (total is None or type(total) is int and total == prompt + completion)
            if not valid:
                db.execute(
                    "UPDATE usage_request SET status='unknown',error='incomplete_or_invalid_usage' "
                    "WHERE id=?",
                    (request_id,),
                )
            else:
                db.execute(
                    "UPDATE usage_request SET status='settled',charged_tokens=?,charged_cost=?,"
                    "usage=?,error='' WHERE id=?",
                    (
                        prompt + completion,
                        _cost(prompt, completion, tuple(model["rates"])),
                        json.dumps(usage, sort_keys=True),
                        request_id,
                    ),
                )
        if not valid:
            raise UsageStopped(
                "the provider returned incomplete or invalid usage; reconciliation is required"
            )

    def status(self) -> dict[str, Any]:
        with self.transaction() as db:
            rows = [
                dict(row) for row in db.execute("SELECT * FROM usage_request ORDER BY created,id")
            ]
        return {
            "enabled": self.policy.enabled,
            "configuration_required": self.policy.daily_cny <= 0
            or self.policy.task_cny <= 0
            or self.policy.price_policy != PRICE_POLICY,
            "price_policy": PRICE_POLICY,
            "price_source": PRICE_SOURCE,
            "context_reservation_tokens": CONTEXT_TOKENS,
            "day_timezone": "UTC",
            "limits": self.policy.model_dump(),
            "requests": len(rows),
            "unknown_requests": sum(row["status"] == "unknown" for row in rows),
            "pending_requests": sum(row["status"] == "pending" for row in rows),
            "charged_upper_cny": sum(row["charged_cost"] or 0 for row in rows) / 1_000_000,
            "unsettled_reserved_cny": sum(
                row["reserved_cost"] for row in rows if row["status"] != "settled"
            )
            / 1_000_000,
        }

    def requests(self) -> list[dict[str, Any]]:
        with self.transaction() as db:
            rows = [
                dict(row) for row in db.execute("SELECT * FROM usage_request ORDER BY created,id")
            ]
        for row in rows:
            for key in ("owner", "policy", "model", "usage"):
                if row[key] is not None:
                    row[key] = json.loads(row[key])
        return rows

    def reserve_gui(self, root_task_id: str, action_id: str) -> dict[str, Any]:
        if not root_task_id.strip() or not action_id.strip():
            raise ValueError("Stable GUI root and action IDs are required")
        now = self.clock()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM gui_budget WHERE root_id=?", (root_task_id,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO gui_budget VALUES(?,?,?,?)",
                    (root_task_id, now, now + self.policy.gui_seconds, self.policy.gui_actions),
                )
                row = db.execute(
                    "SELECT * FROM gui_budget WHERE root_id=?", (root_task_id,)
                ).fetchone()
            count = db.execute(
                "SELECT count(*) FROM gui_action WHERE root_id=?", (root_task_id,)
            ).fetchone()[0]
            if db.execute(
                "SELECT 1 FROM gui_action WHERE root_id=? AND id=?", (root_task_id, action_id)
            ).fetchone():
                return {**dict(row), "actions": count, "action_id": action_id, "replayed": True}
            if now >= row["deadline"] or now < row["started"]:
                raise UsageStopped("the cumulative GUI execution deadline was reached")
            if count >= min(row["action_limit"], self.policy.gui_actions):
                raise UsageStopped("the cumulative GUI action allowance was exhausted")
            db.execute("INSERT INTO gui_action VALUES(?,?,?)", (root_task_id, action_id, now))
            return {**dict(row), "actions": count + 1, "action_id": action_id, "replayed": False}

    def check_gui(self, root_task_id: str) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM gui_budget WHERE root_id=?", (root_task_id,)).fetchone()
        if row and (self.clock() >= row["deadline"] or self.clock() < row["started"]):
            raise UsageStopped("the cumulative GUI execution deadline was reached")


class UsageGuardLLM(BaseLLM):
    """An audited one-attempt client, charged under its immutable call owner."""

    name = "assistant-usage"

    def __init__(
        self,
        delegate: BaseLLM,
        settings: LLMSettings,
        limits: UsageLimits,
        owner: UsageOwner | None = None,
    ):
        self.delegate, self.limits, self.owner = delegate, limits, owner
        self.settings = settings.model_copy(deep=True)
        self.vision_available = False

    def with_owner(self, owner: UsageOwner) -> UsageGuardLLM:
        return UsageGuardLLM(self.delegate, self.settings, self.limits, owner)

    def continuation_owner(self) -> str | None:
        owner = self.owner or current_usage_owner()
        return owner.budget_key if owner is not None else None

    def continuation_allowed(self, owner: str) -> bool:
        if isinstance(self.delegate, MockLLM):
            return True
        return self.limits.can_continue(owner, self.settings)

    def _check_client(self) -> None:
        if type(self.delegate) is not OpenAIChatLLM:
            raise UsageStopped("only the audited native single-attempt chat adapter is supported")
        actual = self.delegate.settings
        if (
            actual.tool_mode != "native"
            or actual.max_retries != 0
            or actual.vision != "off"
            or actual.stream
            or actual.extra_body
            or self.delegate.client.max_retries != 0
            or actual.model_dump(exclude={"api_key"})
            != self.settings.model_dump(exclude={"api_key"})
        ):
            raise UsageStopped("strict native/no-retry/no-vision model settings are required")

    async def ask(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        on_delta: DeltaCallback | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if isinstance(self.delegate, MockLLM):
            return await self.delegate.ask(messages, tools, tool_choice, on_delta, max_tokens)
        self._check_client()
        if any(message.images for message in messages):
            raise UsageStopped("visual model requests require a separate reviewed policy")
        owner = self.owner or current_usage_owner()
        if owner is None:
            raise UsageStopped("the model request has no immutable task/turn owner")
        output = max_tokens if max_tokens is not None else self.settings.max_tokens
        request_id = self.limits.reserve(owner, self.settings, output)
        try:
            response = await asyncio.wait_for(
                self.delegate.ask(messages, tools, tool_choice, on_delta, output),
                timeout=self.limits.policy.request_seconds,
            )
        except asyncio.CancelledError:
            self.limits.unknown(request_id, "CancelledError")
            raise
        except Exception as exc:
            self.limits.unknown(request_id, type(exc).__name__)
            raise UsageStopped(
                "the model request outcome is unknown; reconciliation is required"
            ) from exc
        self.limits.settle(request_id, response.usage)
        return response

    async def close(self) -> None:
        await self.delegate.close()


def guard_llm(
    delegate: BaseLLM,
    settings: LLMSettings,
    limits: UsageLimits,
    owner: UsageOwner | None = None,
) -> BaseLLM:
    if isinstance(delegate, UsageGuardLLM):
        if delegate.limits is not limits:
            raise UsageStopped("model clients cannot switch usage databases")
        return delegate.with_owner(owner) if owner is not None else delegate
    return UsageGuardLLM(delegate, settings, limits, owner)


class BlockedUsageLLM(BaseLLM):
    """Keep settings/UI available while refusing unconfigured paid operations."""

    name = "assistant-usage-blocked"

    def __init__(self, settings: LLMSettings, reason: str):
        self.settings, self.reason = settings.model_copy(deep=True), reason

    def continuation_owner(self) -> str | None:
        owner = current_usage_owner()
        return owner.budget_key if owner is not None else None

    def continuation_allowed(self, owner: str) -> bool:
        return False

    async def ask(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        on_delta: DeltaCallback | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        raise UsageStopped(self.reason)


def make_guarded_llm(
    slot: LLMSettings,
    limits: UsageLimits,
    factory: Callable[[LLMSettings], BaseLLM],
    owner: UsageOwner | None = None,
) -> BaseLLM:
    """A refusal never invokes the supplied provider factory, Vault or network."""
    strict = strict_llm_settings(slot, limits.policy)
    try:
        limits.validate_slot(strict)
    except UsageStopped as exc:
        return BlockedUsageLLM(strict, exc.reason)
    return guard_llm(factory(strict), strict, limits, owner)
