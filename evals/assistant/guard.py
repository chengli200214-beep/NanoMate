"""Shared model admission, deadlines and conservative durable fee reservations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from evals.assistant.freeze import approval, digest, model_config, verify
from evals.research.budget import BatchBudget, BatchBudgetStopped, BudgetedLLM
from nanomuse.llm.base import BaseLLM, DeltaCallback
from nanomuse.llm.mock import MockLLM
from nanomuse.llm.openai_chat import OpenAIChatLLM
from nanomuse.schema import LLMResponse, Message

MAX_EXECUTIONS = 32
MAX_REQUESTS = 384
REQUESTS_PER_EXECUTION = 12
MAX_OUTPUT_TOKENS = 4096
MAX_INPUT_TOKENS = 32_000
MAX_REQUEST_BYTES = 256 * 1024
MIN_VENDOR_CONTEXT_RESERVATION = 1_048_576
EXECUTION_SECONDS = 900
REQUEST_SECONDS = 120


class _SharedBudget(BatchBudget):
    """Perform admission checks inside the currency reservation's write lock."""

    def __init__(self, path: Path, policy: dict[str, Any]) -> None:
        self.check: Callable[[str], None] | None = None
        self._admitting: str | None = None
        super().__init__(path, policy)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with super().transaction():
            if self._admitting is not None and self.check is not None:
                self.check(self._admitting)
            yield

    def reserve(self, execution_id: str, max_tokens: int) -> str:
        self._admitting = execution_id
        try:
            return super().reserve(execution_id, max_tokens)
        except BatchBudgetStopped:
            self._admitting = None
            with self.transaction():
                self.db.execute("INSERT INTO stop VALUES ('assistant_admission_stopped')")
            raise
        finally:
            self._admitting = None


class EvaluationGuard:
    """Serial technical execution windows, not a scenario runner or semantic scorer.

    The approved full vendor context is reserved before every provider request.
    The smaller payload/observed-token caps are additional checks, not a claim
    about tokenization or justification for reserving less money.
    """

    def __init__(self, directory: Path, *, allow_mock: bool = False) -> None:
        self.directory = directory.resolve()
        self.frozen = json.loads((self.directory / "freeze.json").read_text("utf-8"))
        receipt = json.loads((self.directory / "approval.json").read_text("utf-8"))
        policy, self.allowed = approval(self.frozen, receipt)
        self._receipt_sha256 = digest(receipt)
        if not allow_mock and set(self.frozen.get("case_fixtures", {})) != {
            row["execution_id"] for row in self.frozen["matrix"]
        }:
            raise ValueError("Freeze all 32 prepared case fixtures before live acceptance")
        if (
            type(policy["context_token_ceiling"]) is not int
            or policy["context_token_ceiling"] < MIN_VENDOR_CONTEXT_RESERVATION
        ):
            raise ValueError("Reserve the reviewed full vendor context, at least the input cap")
        self.allow_mock = allow_mock
        self.budget = _SharedBudget(self.directory / "budget.sqlite3", policy)
        self.budget.db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_execution (id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL,started REAL NOT NULL,deadline REAL NOT NULL,finished REAL)"
        )
        self.budget.check = self._admit
        self._in_flight = False
        self.max_payload_bytes = 0
        self.request_metadata: list[dict[str, Any]] = []
        self._delegate: BaseLLM | None = None
        self._wrappers: list[GuardedLLM] = []
        self.quiescent: Callable[[], bool] | None = None
        self._monotonic_deadline: tuple[str, float] | None = None
        with self.budget.transaction():
            if self.budget.db.execute(
                "SELECT 1 FROM assistant_execution WHERE status IN ('running','closing')"
            ).fetchone():
                self.budget.db.execute(
                    "INSERT INTO stop VALUES ('assistant_interrupted_execution')"
                )

    def active(self) -> str:
        row = self.budget.db.execute(
            "SELECT id FROM assistant_execution WHERE status='running'"
        ).fetchone()
        if row is None:
            raise BatchBudgetStopped("No approved assistant execution is active")
        return str(row["id"])

    def begin(self, execution_id: str) -> None:
        self.verify_current()
        if execution_id not in self.allowed:
            raise BatchBudgetStopped("This execution is outside the newly approved phase")
        now = time.time()
        with self.budget.transaction():
            if (
                self.budget.stopped
                or self.budget.db.execute(
                    "SELECT 1 FROM assistant_execution WHERE status IN ('running','closing')"
                ).fetchone()
            ):
                raise BatchBudgetStopped("The batch is stopped or another execution is active")
            if self.budget.db.execute(
                "SELECT 1 FROM assistant_execution WHERE id=?", (execution_id,)
            ).fetchone():
                raise BatchBudgetStopped("Execution IDs cannot be replayed or reset")
            if (
                self.budget.db.execute("SELECT COUNT(*) FROM assistant_execution").fetchone()[0]
                >= MAX_EXECUTIONS
            ):
                raise BatchBudgetStopped("The approved execution count is exhausted")
            self.budget.db.execute(
                "INSERT INTO assistant_execution VALUES(?,'running',?,?,NULL)",
                (execution_id, now, now + EXECUTION_SECONDS),
            )
        self._monotonic_deadline = (execution_id, time.monotonic() + EXECUTION_SECONDS)
        for wrapper in self._wrappers:
            wrapper._bind_once(execution_id)

    def seal(self, execution_id: str) -> None:
        """Close model admission before cleanup, retaining all spending and domain evidence."""
        with self.budget.transaction():
            changed = self.budget.db.execute(
                "UPDATE assistant_execution SET status='closing' WHERE id=? AND status='running'",
                (execution_id,),
            )
            if changed.rowcount != 1:
                raise ValueError("Only the current execution can be sealed")

    def verify_current(self) -> None:
        verify(self.frozen)
        current = json.loads((self.directory / "approval.json").read_text("utf-8"))
        if digest(current) != self._receipt_sha256:
            raise ValueError("Approval changed or was revoked; do not send another request")

    def finish(self, execution_id: str) -> None:
        if self._in_flight or (self.quiescent is not None and not self.quiescent()):
            raise BatchBudgetStopped(
                "Wait for the outstanding request before closing its execution"
            )
        with self.budget.transaction():
            changed = self.budget.db.execute(
                "UPDATE assistant_execution SET status='finished',finished=? WHERE id=? AND status IN ('running','closing')",
                (time.time(), execution_id),
            )
            if changed.rowcount != 1:
                raise ValueError("Only the current execution can be closed")
        # A finished technical window does not imply its business assertions passed.
        self._monotonic_deadline = None

    def _admit(self, execution_id: str) -> None:
        row = self.budget.db.execute(
            "SELECT * FROM assistant_execution WHERE id=? AND status='running'", (execution_id,)
        ).fetchone()
        if row is None or execution_id not in self.allowed:
            raise BatchBudgetStopped("No approved execution owns this model request")
        expired = time.time() >= row["deadline"]
        if self._monotonic_deadline is not None:
            owner, deadline = self._monotonic_deadline
            expired = expired or owner != execution_id or time.monotonic() >= deadline
        if expired:
            raise BatchBudgetStopped("The execution deadline expired before another request")
        total = self.budget.db.execute("SELECT COUNT(*) FROM request").fetchone()[0]
        calls = self.budget.db.execute(
            "SELECT COUNT(*) FROM request WHERE execution_id=?", (execution_id,)
        ).fetchone()[0]
        if total >= MAX_REQUESTS or calls >= REQUESTS_PER_EXECUTION:
            raise BatchBudgetStopped("The batch or execution request allowance is exhausted")

    def remaining_seconds(self, execution_id: str) -> float:
        row = self.budget.db.execute(
            "SELECT deadline FROM assistant_execution WHERE id=?", (execution_id,)
        ).fetchone()
        if row is None:
            raise BatchBudgetStopped("Unknown execution")
        remaining = float(row["deadline"]) - time.time()
        if self._monotonic_deadline is not None:
            remaining = min(remaining, self._monotonic_deadline[1] - time.monotonic())
        return remaining

    def wrap(self, delegate: BaseLLM) -> GuardedLLM:
        valid = type(delegate) is (MockLLM if self.allow_mock else OpenAIChatLLM)
        if not valid or model_config(delegate.settings) != self.frozen["model"]:  # type: ignore[attr-defined]
            raise ValueError(
                "Use the exact frozen native client; mocks require explicit offline mode"
            )
        if self._delegate is not None and self._delegate is not delegate:
            raise ValueError("Main chat and research must share one provider object")
        self._delegate = delegate
        return GuardedLLM(self, delegate)

    def summary(self) -> dict[str, Any]:
        return {
            "schema": "assistant-evaluation-technical-results-v1",
            "freeze_sha256": self.frozen["freeze_sha256"],
            "offline_mock": self.allow_mock,
            "largest_serialized_payload_seen_bytes": self.max_payload_bytes,
            "request_count_semantics": "durable reservations; interrupted intents may not have reached the provider",
            "executions": [
                dict(row)
                for row in self.budget.db.execute(
                    "SELECT * FROM assistant_execution ORDER BY started"
                )
            ],
            "budget": self.budget.summary(),
            "domain_acceptance": "not_scored_by_technical_guard",
        }

    def close(self) -> None:
        if self._in_flight:
            raise RuntimeError("Cannot close a live reservation")
        self.budget.close()


class GuardedLLM(BaseLLM):
    """One counted attempt, with no implicit completion repair or vision fallback."""

    def __init__(
        self,
        guard: EvaluationGuard,
        delegate: BaseLLM,
        owner: str | None = None,
        *,
        role: str = "main_or_background",
    ) -> None:
        self.guard, self.delegate = guard, delegate
        self._owner, self.role = owner, role
        if self._owner is None:
            try:
                self._owner = guard.active()
            except BatchBudgetStopped:
                pass
        guard._wrappers.append(self)
        self.settings = delegate.settings  # type: ignore[attr-defined]
        self.name = delegate.name

    @property
    def owner(self) -> str | None:
        return self._owner

    def _bind_once(self, execution_id: str) -> None:
        if self._owner is None:
            self._owner = execution_id

    def continuation_owner(self) -> str | None:
        return self._owner

    def continuation_allowed(self, owner: str | None) -> bool:
        try:
            self.guard.verify_current()
            return (
                owner is not None
                and owner == self._owner == self.guard.active()
                and not self.guard.budget.stopped
                and self.guard.remaining_seconds(owner) > 0
            )
        except (RuntimeError, ValueError, OSError, sqlite3.Error):
            return False

    async def ask(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        on_delta: DeltaCallback | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.guard.verify_current()
        if model_config(self.settings) != self.guard.frozen["model"]:
            raise ValueError("The frozen model configuration changed")
        if isinstance(self.delegate, OpenAIChatLLM) and (
            self.delegate.client.max_retries != 0
            or str(self.delegate.client.base_url).rstrip("/") != self.settings.base_url.rstrip("/")
        ):
            raise ValueError("The frozen provider transport changed")
        maximum = self.settings.max_tokens if max_tokens is None else max_tokens
        if type(maximum) is not int or not 0 < maximum <= min(
            MAX_OUTPUT_TOKENS, self.settings.max_tokens
        ):
            raise ValueError("Output allowance exceeds the reviewed cap")
        wire = {
            "messages": [message.to_openai() for message in messages],
            "tools": tools,
            "tool_choice": tool_choice,
            "model": self.settings.model,
            "extra_body": self.settings.extra_body,
        }
        payload_bytes = len(json.dumps(wire, ensure_ascii=False).encode())
        self.guard.max_payload_bytes = max(self.guard.max_payload_bytes, payload_bytes)
        if any(message.images for message in messages) or payload_bytes > MAX_REQUEST_BYTES:
            raise ValueError("Only bounded text-only requests are allowed")
        if self.guard._in_flight:
            raise BatchBudgetStopped("Model requests must be serial across chat and research")
        execution_id = self.guard.active()
        if self.owner is None or self.owner != execution_id:
            raise BatchBudgetStopped("A previous execution's client cannot charge the current case")
        timeout = min(
            REQUEST_SECONDS, self.settings.timeout, self.guard.remaining_seconds(execution_id)
        )
        if timeout <= 0:
            raise BatchBudgetStopped("The execution deadline expired")
        self.guard._in_flight = True
        started = time.monotonic()
        previous_requests = self.guard.budget.summary()["requests"]
        try:
            wrapped = BudgetedLLM(self.delegate, self.guard.budget, execution_id)
            async with asyncio.timeout(timeout):
                response = await wrapped.ask(messages, tools, tool_choice, on_delta, maximum)
            # Usage was settled against the full vendor reservation first. A vendor
            # exceeding the smaller acceptance envelope stops all further spending.
            usage = response.usage
            inputs = usage.get("prompt_tokens", usage.get("input_tokens"))
            outputs = usage.get("completion_tokens", usage.get("output_tokens"))
            assert isinstance(inputs, int) and isinstance(outputs, int)
            if inputs > MAX_INPUT_TOKENS or outputs > maximum:
                with self.guard.budget.transaction():
                    self.guard.budget.db.execute(
                        "INSERT INTO stop VALUES ('assistant_token_cap_exceeded')"
                    )
                raise BatchBudgetStopped("Reported tokens exceeded the acceptance envelope")
            self.guard.verify_current()
            return response
        finally:
            if self.guard.budget.summary()["requests"] > previous_requests:
                request = self.guard.budget.db.execute(
                    "SELECT * FROM request WHERE execution_id=? ORDER BY rowid DESC LIMIT 1",
                    (execution_id,),
                ).fetchone()
                self.guard.request_metadata.append(
                    {
                        "request_id": request["id"],
                        "execution_id": execution_id,
                        "role": self.role,
                        "payload_bytes": payload_bytes,
                        "elapsed_seconds": time.monotonic() - started,
                        "system_sha256": hashlib.sha256(
                            "\n".join(
                                message.content or ""
                                for message in messages
                                if message.role.value == "system"
                            ).encode()
                        ).hexdigest(),
                        "injected_preference_ids": sorted(
                            set(
                                re.findall(
                                    r"m_pref_[a-f0-9]{32}",
                                    "\n".join(
                                        message.content or ""
                                        for message in messages
                                        if message.role.value == "system"
                                    ),
                                )
                            )
                        ),
                    }
                )
            self.guard._in_flight = False

    async def ask_complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        return await self.ask(messages, **kwargs)

    def bind_current(self) -> GuardedLLM:
        if self.owner is None or self.owner != self.guard.active():
            raise BatchBudgetStopped("A previous execution's factory cannot borrow another window")
        return GuardedLLM(self.guard, self.delegate, self.owner, role="research")

    async def close(self) -> None:
        await self.delegate.close()


def wire_isolated_service(
    service: Any, research: Any, guard: EvaluationGuard, *, assistant: Any = None
) -> GuardedLLM:
    """Wire a newly constructed isolated service before any worker is started.

    This deliberately does not launch scenarios, access a saved key, enable GUI,
    or start the service. The caller still owns the domain harness and assertions.
    """
    try:
        expected = guard.directory / "cases" / guard.active() / "runtime"
    except BatchBudgetStopped:
        expected = guard.directory / "runtime"
    profile = guard.frozen.get("driver_profile", {})
    if any(
        getattr(getattr(service.settings, name), field) != value
        for name in ("browser", "hands")
        for field, value in profile.get(name, {}).items()
    ):
        raise ValueError("The service drivers differ from the reviewed profile")
    if (
        Path(service.data_dir).resolve() != expected
        or Path(service.settings.agent.workspace).resolve() != expected / "workspace"
        or service.settings.agent.extra_roots
        or service.settings.hub.enabled
        or service.settings.cloud.required
        or service.settings.cloud.models
        or service.settings.cloud.sync
        or service.settings.memory.embeddings != "off"
        or service.settings.server.port == 8787
        or service._scheduler is not None
        or research.task is not None
        or research._reasoners
        or research.reasoner is not None
        or service.settings.server.host != "127.0.0.1"
        or not service.settings.server.auth
        or service.settings.mcp.servers
        or service.settings.skills.enabled
        or any(thread.busy for thread in service.threads.values())
    ):
        raise ValueError("Use a fresh isolated, local, stopped evaluation service")
    wrapped = guard.wrap(service.app.llm)
    from nanomuse.assistant.tool_boundary import install_boundary

    service.settings.agent.task_ledger_only = True
    install_boundary(service)
    service.app.llm = service.app.agent.llm = wrapped
    for thread in service.threads.values():
        thread.agent.llm = wrapped
    research.llm = wrapped.bind_current

    def quiescent() -> bool:
        personal = getattr(assistant, "personal", None)
        worker = getattr(personal, "_worker", None)
        if any(thread.busy for thread in service.threads.values()) or (
            worker is not None and not worker.done()
        ):
            return False
        if (
            not service._started
            and research.task is None
            and (assistant is None or getattr(assistant, "_task", None) is None)
        ):
            return True
        if any(not thread.inbox.empty() for thread in service.threads.values()):
            return False
        if assistant is not None and any(
            task["status"] in {"queued", "running", "waiting_approval"}
            for task in assistant.ledger.tasks()
        ):
            return False
        return not any(
            run["status"] in {"queued", "running"}
            for project in research.store.list("project")
            for run in research.store.runs(project["id"])
        )

    guard.quiescent = quiescent
    service.profile.proactivity = "off"
    # Operator/media/coding tools can construct uncounted provider clients. Keep
    # only the deterministic personal adapters and their existing underlying hands.
    allowed = {
        "assistant_tasks",
        "files",
        "reminders",
        "goals",
        "terminate",
        "recall",
        "ask_user",
        "browser",
        "computer_screen",
        "computer_act",
    }
    for tool in list(service.app.tools):
        if tool.name not in allowed:
            service.app.tools.remove(tool.name)
    return wrapped
