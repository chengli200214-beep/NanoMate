"""Typed reminder commands with domain receipts for task-ledger retries."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nanomuse.reminders import ReminderStore


class ReminderAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    action: Literal["create", "update", "cancel"]
    reminder_id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=1)
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    at: str | None = Field(default=None, max_length=100)
    repeat: str | None = Field(default=None, max_length=100)
    kind: Literal["remind", "task"] | None = None
    thread: str = Field(default="main", min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_action(self) -> ReminderAction:
        edits = any(value is not None for value in (self.text, self.at, self.repeat, self.kind))
        if self.action == "create":
            if self.reminder_id is not None or self.expected_revision is not None:
                raise ValueError("new reminders cannot name an existing reminder or revision")
            if self.text is None or not (self.at or self.repeat):
                raise ValueError("new reminders require text and a time or repeat cadence")
        else:
            if self.reminder_id is None or self.expected_revision is None:
                raise ValueError("editing or cancelling requires reminder_id and expected_revision")
            if self.action == "update" and not edits:
                raise ValueError("give at least one reminder field to update")
            if self.action == "cancel" and edits:
                raise ValueError("cancelling cannot also edit a reminder")
            if self.thread != "main":
                raise ValueError("editing preserves the reminder's original chat")
        if self.at and self.repeat:
            raise ValueError("give either at or repeat")
        return self


class ReminderActions:
    def __init__(self, store: ReminderStore):
        self.store = store

    def execute(self, spec: ReminderAction, request_key: str) -> dict[str, Any]:
        return self.store.apply_operation(spec.model_dump(exclude_none=True), request_key)
