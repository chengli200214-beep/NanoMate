"""Reminders and routines: things the user asked to hear about, or have done, at a time.

"Remind me at six to call mum" is a *reminder*: at 18:00 the agent says so, and does nothing
else. "Every Monday morning, pull my week's calendar into a page" is a *routine*: at that time
the agent does the work and reports the result. Both live here; the service fires them from
its scheduler loop, and each one becomes a normal message from the agent in the chat it was
set from — with the notice that started it, so the Feed can tell what happened while you
were away.

One-off items fire once and are kept as ``done`` for a while so the list shows what
happened; repeating ones use the same cadence grammar as goal check-ins
(``daily 08:00``, ``weekdays 07:30``, ``weekly mon 09:00``, ``monthly 1 09:00``).
"""

from __future__ import annotations

import builtins  # ``list`` is a method name below; the class body needs the type
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nanomuse.goals.store import next_check_in, parse_check_in

KINDS = ("remind", "task")
STATUSES = ("active", "done", "cancelled")
# how long finished one-offs stay in the list
_KEEP_DONE = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse_when(value: str) -> datetime | None:
    """A point in time as the model or the app writes it: ``2026-09-23 18:00``,
    ``2026-09-23T18:00``, with or without seconds or an offset. Naive values are local
    time. ``None`` when it does not parse."""
    text = (value or "").strip().replace("T", " ")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()  # local
        return parsed
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


@dataclass
class Reminder:
    id: str
    text: str
    kind: str  # remind | task
    thread: str
    status: str
    next_at: str  # ISO UTC; "" once a one-off has fired
    repeat: str  # "" or a cadence spec
    created_at: str
    last_fired_at: str
    fired: int
    revision: int = 1

    @property
    def repeating(self) -> bool:
        return bool(self.repeat)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "thread": self.thread,
            "status": self.status,
            "next_at": self.next_at or None,
            "repeat": self.repeat,
            "created_at": self.created_at,
            "last_fired_at": self.last_fired_at or None,
            "fired": self.fired,
            "revision": self.revision,
        }

    def render(self) -> str:
        when = self.repeat or (self.next_at and _local(self.next_at)) or "(no time)"
        what = "remind" if self.kind == "remind" else "do"
        return f"[{self.id}] {when} · {what}: {self.text} ({self.status}, revision {self.revision})"


def _local(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class ReminderStore:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'remind',
                thread TEXT NOT NULL DEFAULT 'main',
                status TEXT NOT NULL DEFAULT 'active',
                next_at TEXT NOT NULL DEFAULT '',
                repeat TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_fired_at TEXT NOT NULL DEFAULT '',
                fired INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS reminder_operations (
                request_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        with self._write():
            columns = {r[1] for r in self._conn.execute("PRAGMA table_info(reminders)")}
            if "revision" not in columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _write(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------ write
    def create(
        self,
        text: str,
        at: str = "",
        repeat: str = "",
        kind: str = "remind",
        thread: str = "main",
    ) -> Reminder:
        """``at`` for a one-off (any time in the future), ``repeat`` for a routine (the first
        occurrence is computed; ``at`` is ignored). One of the two is required."""
        with self._write():
            return self._create(text, at, repeat, kind, thread)

    def _create(self, text: str, at: str, repeat: str, kind: str, thread: str) -> Reminder:
        text = text.strip()
        if not text:
            raise ValueError("what should the reminder say?")
        kind = kind if kind in KINDS else "remind"
        repeat = (repeat or "").strip().lower()
        first = self._first(at, repeat)
        rid = "r_" + uuid.uuid4().hex[:6]
        self._conn.execute(
            "INSERT INTO reminders (id,text,kind,thread,status,next_at,repeat,created_at,"
            "last_fired_at,fired) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, text, kind, thread or "main", "active", _iso(first), repeat, _iso(_now()), "", 0),
        )
        item = self.get(rid)
        assert item is not None
        return item

    @staticmethod
    def _first(at: str, repeat: str) -> datetime:
        if repeat:
            if parse_check_in(repeat) is None:
                raise ValueError(
                    f"unknown cadence {repeat!r}; use 'daily 08:00', 'weekdays 07:30', "
                    "'weekly mon 09:00' or 'monthly 1 09:00'"
                )
            first = next_check_in(repeat)
            if first is None:  # pragma: no cover - the grammar always yields a next time
                raise ValueError("could not compute the first occurrence")
        else:
            parsed = parse_when(at)
            if parsed is None:
                raise ValueError(f"could not read the time {at!r}; use 'YYYY-MM-DD HH:MM'")
            if parsed <= _now() - timedelta(minutes=1):
                # the model often works from a time quoted earlier in the conversation
                now_local = _now().astimezone().strftime("%Y-%m-%d %H:%M")
                raise ValueError(f"{at} is in the past; it is now {now_local}")
            first = parsed
        return first

    def update(
        self,
        reminder_id: str,
        *,
        expected_revision: int,
        text: str | None = None,
        at: str | None = None,
        repeat: str | None = None,
        kind: str | None = None,
    ) -> Reminder:
        """Edit the active item in place, rejecting a stale view or a completed occurrence."""
        with self._write():
            return self._update(reminder_id, expected_revision, text, at, repeat, kind)

    def _active(self, reminder_id: str, expected_revision: int) -> Reminder:
        item = self.get(reminder_id)
        if item is None:
            raise ValueError(f"no reminder {reminder_id}")
        if item.revision != expected_revision:
            raise ValueError("reminder changed; refresh its revision before editing")
        if item.status != "active":
            raise ValueError(f"reminder is {item.status}; create a new reminder instead")
        return item

    def _update(
        self,
        reminder_id: str,
        expected_revision: int,
        text: str | None,
        at: str | None,
        repeat: str | None,
        kind: str | None,
    ) -> Reminder:
        item = self._active(reminder_id, expected_revision)
        if all(value is None for value in (text, at, repeat, kind)):
            raise ValueError("give at least one reminder field to update")
        new_text = item.text if text is None else text.strip()
        if not new_text:
            raise ValueError("what should the reminder say?")
        if kind is not None and kind not in KINDS:
            raise ValueError("kind must be 'remind' or 'task'")
        new_repeat = item.repeat if repeat is None else repeat.strip().lower()
        if at is not None:
            new_repeat = "" if repeat is None else new_repeat
        if at is not None or repeat is not None:
            if at and new_repeat:
                raise ValueError("give either at or repeat when editing a reminder")
            first = self._first(at if at is not None else item.next_at, new_repeat)
            next_at = _iso(first)
        else:
            next_at = item.next_at
        self._conn.execute(
            "UPDATE reminders SET text=?,kind=?,next_at=?,repeat=?,revision=revision+1 WHERE id=?",
            (new_text, kind or item.kind, next_at, new_repeat, reminder_id),
        )
        updated = self.get(reminder_id)
        assert updated is not None
        return updated

    def cancel(self, reminder_id: str, *, expected_revision: int | None = None) -> Reminder | None:
        with self._write():
            return self._cancel(reminder_id, expected_revision)

    def _cancel(self, reminder_id: str, expected_revision: int | None) -> Reminder | None:
        if expected_revision is not None:
            self._active(reminder_id, expected_revision)
        cur = self._conn.execute(
            "UPDATE reminders SET status='cancelled', next_at='', revision=revision+1 "
            "WHERE id=? AND status='active'",
            (reminder_id,),
        )
        return self.get(reminder_id) if cur.rowcount else None

    def apply_operation(self, payload: dict[str, Any], request_key: str) -> dict[str, Any]:
        """Commit a domain change and its retry receipt in the same SQLite transaction."""
        if not request_key or len(request_key) > 500:
            raise ValueError("a request key of at most 500 characters is required")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._write():
            previous = self._conn.execute(
                "SELECT payload,result FROM reminder_operations WHERE request_key=?", (request_key,)
            ).fetchone()
            if previous is not None:
                if previous["payload"] != encoded:
                    raise ValueError("request key already used with different reminder inputs")
                return json.loads(previous["result"])  # type: ignore[no-any-return]
            action = payload["action"]
            if action == "create":
                item = self._create(
                    payload["text"],
                    payload.get("at", ""),
                    payload.get("repeat", ""),
                    payload.get("kind", "remind"),
                    payload.get("thread", "main"),
                )
            elif action == "update":
                item = self._update(
                    payload["reminder_id"],
                    payload["expected_revision"],
                    payload.get("text"),
                    payload.get("at"),
                    payload.get("repeat"),
                    payload.get("kind"),
                )
            elif action == "cancel":
                cancelled = self._cancel(payload["reminder_id"], payload["expected_revision"])
                assert cancelled is not None
                item = cancelled
            else:
                raise ValueError("unknown reminder action")
            result = {
                "reminder": item.to_dict(),
                "operation": {
                    "request_key": request_key,
                    "action": action,
                    "reminder_id": item.id,
                    "revision": item.revision,
                },
            }
            self._conn.execute(
                "INSERT INTO reminder_operations VALUES(?,?,?,?)",
                (request_key, encoded, json.dumps(result, ensure_ascii=False), _iso(_now())),
            )
            return result

    def delete(self, reminder_id: str) -> bool:
        with self._write():
            cur = self._conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            return cur.rowcount > 0

    def mark_fired(
        self,
        reminder_id: str,
        now: datetime | None = None,
        *,
        expected_revision: int | None = None,
        expected_next_at: str | None = None,
    ) -> Reminder | None:
        """Called when the item was handed to the agent: a routine moves to its next
        occurrence, a one-off is done."""
        with self._write():
            item = self.get(reminder_id)
            if item is None or item.status != "active":
                return None
            if expected_revision is not None and item.revision != expected_revision:
                return None
            if expected_next_at is not None and item.next_at != expected_next_at:
                return None
            now = now or _now()
            nxt = next_check_in(item.repeat, now.astimezone()) if item.repeat else None
            self._conn.execute(
                "UPDATE reminders SET next_at=?, status=?, last_fired_at=?, fired=fired+1, "
                "revision=revision+1 WHERE id=?",
                (_iso(nxt) if nxt else "", "active" if nxt else "done", _iso(now), reminder_id),
            )
            return self.get(reminder_id)

    # ------------------------------------------------------------------ read
    def get(self, reminder_id: str) -> Reminder | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
            return self._row(row) if row else None

    def list(self, status: str | None = "active") -> builtins.list[Reminder]:
        """Active items soonest first; ``None`` for everything (recent finished ones
        included, older finished ones pruned)."""
        with self._lock:
            if status is None:
                self._prune()
                rows = self._conn.execute(
                    "SELECT * FROM reminders ORDER BY status = 'active' DESC, "
                    "CASE WHEN next_at = '' THEN last_fired_at ELSE next_at END"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM reminders WHERE status = ? ORDER BY next_at", (status,)
                ).fetchall()
            return [self._row(r) for r in rows]

    def due(self, now: datetime | None = None) -> builtins.list[Reminder]:
        with self._lock:
            cutoff = _iso(now or _now())
            rows = self._conn.execute(
                "SELECT * FROM reminders WHERE status = 'active' AND next_at != '' AND next_at <= ? "
                "ORDER BY next_at",
                (cutoff,),
            ).fetchall()
            return [self._row(r) for r in rows]

    def _prune(self) -> None:
        with self._write():
            self._conn.execute(
                "DELETE FROM reminders WHERE status != 'active' AND last_fired_at != '' "
                "AND last_fired_at < ?",
                (_iso(_now() - _KEEP_DONE),),
            )
            self._conn.execute(
                "DELETE FROM reminders WHERE status = 'cancelled' AND last_fired_at = '' "
                "AND created_at < ?",
                (_iso(_now() - _KEEP_DONE),),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            text=row["text"],
            kind=row["kind"],
            thread=row["thread"],
            status=row["status"],
            next_at=row["next_at"],
            repeat=row["repeat"],
            created_at=row["created_at"],
            last_fired_at=row["last_fired_at"],
            fired=int(row["fired"]),
            revision=int(row["revision"]),
        )
