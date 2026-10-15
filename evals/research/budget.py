"""Reserve a conservative monetary bound before each evaluation provider request."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nanomuse.llm.base import BaseLLM, DeltaCallback
from nanomuse.schema import LLMResponse, Message


class BatchBudgetStopped(RuntimeError):
    """No further paid request is permitted in this evaluation batch."""


class BatchBudget:
    """A durable, shared ledger in integer micro-CNY, including in-flight reservations.

    The policy supplies vendor-verified peak cache-miss and output rates. Settlement
    remains a conservative upper estimate, never a claim about the provider's bill.
    A process crash leaves its reservation outstanding and prevents new requests.
    """

    def __init__(self, path: Path, policy: dict[str, Any]) -> None:
        self.path = path
        self.policy = policy
        limit = Decimal(str(policy["limit_cny"]))
        reserve = Decimal(str(policy["stop_reserve_cny"]))
        if (
            not limit.is_finite()
            or not reserve.is_finite()
            or not 0 <= reserve < limit
            or any(
                type(policy[key]) is not int or policy[key] <= 0
                for key in (
                    "context_token_ceiling",
                    "input_peak_micro_cny",
                    "output_peak_micro_cny",
                )
            )
        ):
            raise ValueError("A budget policy requires positive bounds and a finite spending limit")
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, value TEXT);"
            "CREATE TABLE IF NOT EXISTS request (id TEXT PRIMARY KEY, execution_id TEXT, "
            "status TEXT, reserved INTEGER, charged_upper INTEGER, input_tokens INTEGER, "
            "output_tokens INTEGER, error_type TEXT);"
            "CREATE TABLE IF NOT EXISTS stop (reason TEXT);"
        )
        with self.transaction():
            encoded = json.dumps(policy, sort_keys=True)
            old = self.db.execute("SELECT value FROM policy WHERE id=1").fetchone()
            if old and old["value"] != encoded:
                raise ValueError("An existing batch ledger cannot change its approved policy")
            self.db.execute("INSERT OR IGNORE INTO policy VALUES (1, ?)", (encoded,))
            if self.db.execute("SELECT 1 FROM request WHERE status='reserved'").fetchone():
                self.db.execute("INSERT INTO stop VALUES ('unsettled_request_after_restart')")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @property
    def stopped(self) -> bool:
        return self.db.execute("SELECT 1 FROM stop LIMIT 1").fetchone() is not None

    def reserve(self, execution_id: str, max_tokens: int) -> str:
        # Reserve the vendor's entire context window, rather than guessing tokenization.
        bound = (
            self.policy["context_token_ceiling"] * self.policy["input_peak_micro_cny"]
            + max_tokens * self.policy["output_peak_micro_cny"]
        )
        limit = int(Decimal(str(self.policy["limit_cny"])) * 1_000_000)
        reserve = int(Decimal(str(self.policy["stop_reserve_cny"])) * 1_000_000)
        identifier = uuid.uuid4().hex
        blocked = False
        with self.transaction():
            if self.stopped:
                raise BatchBudgetStopped("The batch budget is stopped; inspect the local ledger")
            committed = self.db.execute(
                "SELECT COALESCE(SUM(CASE WHEN status='settled' THEN charged_upper "
                "ELSE reserved END), 0) FROM request"
            ).fetchone()[0]
            if committed + bound > limit - reserve:
                self.db.execute("INSERT INTO stop VALUES ('currency_limit_reservation')")
                blocked = True
            else:
                self.db.execute(
                    "INSERT INTO request VALUES (?, ?, 'reserved', ?, NULL, NULL, NULL, NULL)",
                    (identifier, execution_id, bound),
                )
        if blocked:
            raise BatchBudgetStopped("The next request would exceed the batch stopping threshold")
        return identifier

    def settle(self, identifier: str, usage: dict[str, Any]) -> None:
        inputs = usage.get("prompt_tokens", usage.get("input_tokens"))
        outputs = usage.get("completion_tokens", usage.get("output_tokens"))
        total = usage.get("total_tokens")
        valid = all(type(value) is int and value >= 0 for value in (inputs, outputs, total))
        if (
            not valid
            or not isinstance(inputs, int)
            or not isinstance(outputs, int)
            or total != inputs + outputs
        ):
            self.unknown(identifier, "missing_or_inconsistent_usage")
            raise BatchBudgetStopped("Provider usage is unknown; reconciliation is required")
        assert isinstance(inputs, int) and isinstance(outputs, int)
        cost = inputs * self.policy["input_peak_micro_cny"]
        cost += outputs * self.policy["output_peak_micro_cny"]
        exceeded = False
        with self.transaction():
            row = self.db.execute("SELECT * FROM request WHERE id=?", (identifier,)).fetchone()
            if row is None or row["status"] != "reserved":
                raise ValueError("Only an outstanding request reservation can be settled")
            self.db.execute(
                "UPDATE request SET status='settled', charged_upper=?, input_tokens=?, "
                "output_tokens=? WHERE id=?",
                (cost, inputs, outputs, identifier),
            )
            if cost > row["reserved"]:
                self.db.execute("INSERT INTO stop VALUES ('vendor_usage_exceeded_reservation')")
                exceeded = True
        if exceeded:
            raise BatchBudgetStopped("Reported usage exceeded the reserved vendor bound")

    def unknown(self, identifier: str, error_type: str) -> None:
        with self.transaction():
            self.db.execute(
                "UPDATE request SET status='unknown', error_type=? "
                "WHERE id=? AND status='reserved'",
                (error_type, identifier),
            )
            self.db.execute("INSERT INTO stop VALUES ('unknown_provider_usage')")

    def summary(self) -> dict[str, Any]:
        rows = [dict(row) for row in self.db.execute("SELECT * FROM request")]
        return {
            "requests": len(rows),
            "settled_requests": sum(row["status"] == "settled" for row in rows),
            "unsettled_requests": sum(row["status"] != "settled" for row in rows),
            "known_peak_cost_upper_cny": sum(row["charged_upper"] or 0 for row in rows) / 1_000_000,
            "unsettled_reservations_cny": sum(
                row["reserved"] for row in rows if row["status"] != "settled"
            )
            / 1_000_000,
            "stopped": self.stopped,
            "stop_reasons": [row[0] for row in self.db.execute("SELECT reason FROM stop")],
            "actual_provider_bill_verified": False,
        }

    def close(self) -> None:
        self.db.close()


class BudgetedLLM(BaseLLM):
    """One ledger entry per HTTP attempt, with transport and tool fallbacks disabled."""

    def __init__(
        self,
        delegate: BaseLLM,
        budget: BatchBudget,
        execution_id: str,
        before_request: Callable[[], None] | None = None,
    ) -> None:
        self.delegate = delegate
        self.budget = budget
        self.execution_id = execution_id
        self.before_request = before_request
        self.name = delegate.name
        self.settings: Any = getattr(delegate, "settings", None)
        slot = self.settings
        if (
            slot is None
            or slot.model != budget.policy["model"]
            or urlparse(slot.base_url or "").hostname != "api.deepseek.com"
            or urlparse(slot.base_url or "").scheme != "https"
            or slot.tool_mode != "native"
            or slot.max_retries != 0
            or slot.vision != "off"
            or not 0 < slot.max_tokens <= 384_000
            or set(slot.extra_body) - {"thinking", "reasoning_effort"}
        ):
            raise ValueError("Budgeted evaluation requires the frozen official model configuration")

    async def ask(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        on_delta: DeltaCallback | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        maximum = max_tokens or self.settings.max_tokens
        if not 0 < maximum <= self.settings.max_tokens or any(item.images for item in messages):
            raise ValueError("Request exceeds the frozen text-only output allowance")
        if self.before_request:
            self.before_request()
        identifier = self.budget.reserve(self.execution_id, maximum)
        try:
            response = await self.delegate.ask(
                messages, tools, tool_choice, on_delta, max_tokens=maximum
            )
        except BaseException as exc:
            self.budget.unknown(identifier, type(exc).__name__)
            raise
        self.budget.settle(identifier, response.usage)
        return response

    async def close(self) -> None:
        await self.delegate.close()
