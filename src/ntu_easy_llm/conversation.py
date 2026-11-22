"""Optional conversation sessions and stores (added in 0.6).

Nothing in the library requires these: every adapter takes a plain list of
messages. A :class:`ChatSession` is a convenience for callers that want one
object holding a conversation, the router's events (which source answered,
when it switched) and a store that persists it as it grows.

Storage format (JSONL, one record per line; ``v`` is the record version)::

    {"v": 1, "type": "session", "id": "...", "created_at": 1700000000.0, "meta": {...}}
    {"v": 1, "type": "message", "role": "user", "content": "...", "tool_calls": [...],
     "tool_call_id": "", "name": "", "images": [{"mime": "image/png", "size": 123, "sha256": "..."}],
     "meta": {"profile": "...", "model": "...", "usage": {...}}}
    {"v": 1, "type": "event", "event": "switched", "profile_id": "...", "to_id": "...", "reason": "...", "at": ...}

Images are *not* stored — only their type, size and hash — so a reloaded
session has the text of every turn but no image bytes (the descriptions are
kept in ``Message.meta["images"]``). Unknown record types are skipped, so
newer writers stay readable.

Callers with their own persistence rules implement :class:`SessionStore`.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, runtime_checkable

from .types import Message, as_message

__all__ = [
    "ChatSession", "SessionStore", "SessionInfo", "MemorySessionStore", "JsonlSessionStore",
    "default_estimate", "RECORD_VERSION", "DEFAULT_CHATS_DIR",
]

RECORD_VERSION = 1
DEFAULT_CHATS_DIR = Path.home() / ".ntu_easy_llm" / "chats"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _check_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id) or ".." in session_id:
        raise ValueError(f"invalid session id {session_id!r} (letters, digits, '_', '.', '-'; max 128)")
    return session_id


def default_estimate(m: Message) -> int:
    """Rough token count of one message (~3 characters per token, +overhead). Good enough for budgeting."""
    n = len(m.content)
    for c in m.tool_calls:
        n += len(c.name) + len(json.dumps(c.arguments, ensure_ascii=False))
    return n // 3 + 4 + 800 * len(m.images)


# =============================================================================
# Records
# =============================================================================

def _message_record(m: Message) -> dict:
    return {"v": RECORD_VERSION, "type": "message", "role": m.role, "content": m.content,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls],
            "tool_call_id": m.tool_call_id, "name": m.name,
            "images": [im.describe() for im in m.images], "meta": dict(m.meta)}


def _message_from_record(rec: Mapping[str, Any]) -> Message:
    meta = dict(rec.get("meta") or {})
    if rec.get("images"):
        meta["images"] = list(rec["images"])
    return Message(role=rec.get("role") or "user", content=str(rec.get("content") or ""),
                   tool_calls=list(rec.get("tool_calls") or []), tool_call_id=str(rec.get("tool_call_id") or ""),
                   name=str(rec.get("name") or ""), meta=meta)


def _event_record(event: Any) -> dict:
    if hasattr(event, "to_record"):
        return dict(event.to_record())
    if isinstance(event, Mapping):
        rec = {"v": RECORD_VERSION, "type": "event"}
        rec.update(event)
        rec["type"] = "event"
        return rec
    raise TypeError("event must be a waterfall Event or a mapping")


# =============================================================================
# Session
# =============================================================================

class ChatSession:
    """A conversation: messages in order, router events, free-form ``meta``.

    Bound to a store (``store.load`` / :meth:`bind`), every :meth:`add` and
    :meth:`add_event` is appended to it immediately.
    """

    def __init__(self, id: str | None = None, messages: Iterable[Any] = (), *,
                 meta: Mapping[str, Any] | None = None, created_at: float | None = None):
        self.id = _check_id(id) if id else uuid.uuid4().hex
        self.messages: list[Message] = [as_message(m) for m in messages]
        self.events: list[dict] = []          # event records, each with "_after": number of messages before it
        self.meta: dict = dict(meta or {})
        self.created_at = time.time() if created_at is None else float(created_at)
        self.skipped_records = 0              # unreadable lines skipped on load (not persisted)
        self._store: SessionStore | None = None
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return f"ChatSession(id={self.id!r}, messages={len(self.messages)}, events={len(self.events)})"

    @property
    def store(self) -> "SessionStore | None":
        return self._store

    def bind(self, store: "SessionStore", *, write: bool = True) -> "ChatSession":
        """Attach a store; ``write=True`` saves the whole session to it now."""
        with self._lock:
            self._store = store
            if write:
                store.save(self)
        return self

    def add(self, m: Message | Mapping[str, Any]) -> Message:
        """Append one message (dicts are converted); persisted if bound."""
        msg = as_message(m)
        with self._lock:
            self.messages.append(msg)
            if self._store is not None:
                self._store.append(self.id, _message_record(msg))
        return msg

    def extend(self, messages: Iterable[Message | Mapping[str, Any]]) -> None:
        for m in messages:
            self.add(m)

    def add_event(self, event: Any) -> None:
        """Record a router event (never sent to a model); persisted if bound."""
        rec = _event_record(event)
        with self._lock:
            self.events.append({**rec, "_after": len(self.messages)})
            if self._store is not None:
                self._store.append(self.id, rec)

    def window(self, budget_tokens: int | None = None, *,
               estimate: Callable[[Message], int] = default_estimate) -> list[Message]:
        """Messages to send: leading system messages always, then as many *whole* recent turns as fit.

        A turn starts at a user message, so an assistant tool call is never
        separated from its tool results. Nothing is summarised. If even the
        latest turn does not fit, it is returned anyway (the model will say so).
        """
        with self._lock:
            msgs = list(self.messages)
        if budget_tokens is None:
            return msgs
        head: list[Message] = []
        i = 0
        while i < len(msgs) and msgs[i].role == "system":
            head.append(msgs[i])
            i += 1
        rest = msgs[i:]
        starts = [j for j, m in enumerate(rest) if m.role == "user"] or [0]
        if starts[0] != 0:
            starts.insert(0, 0)
        budget = budget_tokens - sum(estimate(m) for m in head)
        keep_from = starts[-1] if rest else 0
        for s in reversed(starts):
            if sum(estimate(m) for m in rest[s:]) <= budget:
                keep_from = s
            else:
                break
        return head + rest[keep_from:]

    def to_records(self) -> Iterator[dict]:
        """Header, then messages and events in the order they happened."""
        with self._lock:
            yield {"v": RECORD_VERSION, "type": "session", "id": self.id, "created_at": self.created_at,
                   "meta": dict(self.meta)}
            events = sorted(self.events, key=lambda e: e["_after"])
            k = 0
            for idx, m in enumerate(self.messages):
                while k < len(events) and events[k]["_after"] <= idx:
                    yield {key: v for key, v in events[k].items() if key != "_after"}
                    k += 1
                yield _message_record(m)
            for e in events[k:]:
                yield {key: v for key, v in e.items() if key != "_after"}

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]], *, session_id: str | None = None) -> "ChatSession":
        """Rebuild a session from :meth:`to_records` output (unknown record types are skipped)."""
        s = cls(session_id)
        for rec in records:
            t = rec.get("type")
            if t == "session":
                if rec.get("id") and not session_id:
                    s.id = _check_id(str(rec["id"]))
                s.meta.update(rec.get("meta") or {})
                if rec.get("created_at") is not None:
                    s.created_at = float(rec["created_at"])
            elif t == "message":
                s.messages.append(_message_from_record(rec))
            elif t == "event":
                s.events.append({**{k: v for k, v in rec.items()}, "_after": len(s.messages)})
        return s


# =============================================================================
# Stores
# =============================================================================

@dataclass(frozen=True)
class SessionInfo:
    id: str
    updated_at: float
    message_count: int | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class SessionStore(Protocol):
    """Where sessions live. Implement this to use your own persistence."""

    def load(self, session_id: str) -> ChatSession | None: ...

    def append(self, session_id: str, record: dict) -> None: ...

    def save(self, session: ChatSession) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def list(self, limit: int = 50) -> list[SessionInfo]: ...


class MemorySessionStore:
    """Keeps records in memory (tests, short-lived processes)."""

    def __init__(self) -> None:
        self._records: dict[str, list[dict]] = {}
        self._updated: dict[str, float] = {}
        self._lock = threading.Lock()

    def load(self, session_id: str) -> ChatSession | None:
        with self._lock:
            recs = list(self._records.get(_check_id(session_id), ()))
        if not recs:
            return None
        s = ChatSession.from_records(recs, session_id=session_id)
        s._store = self
        return s

    def append(self, session_id: str, record: dict) -> None:
        with self._lock:
            self._records.setdefault(_check_id(session_id), []).append(json.loads(json.dumps(record)))
            self._updated[session_id] = time.time()

    def save(self, session: ChatSession) -> None:
        recs = [json.loads(json.dumps(r)) for r in session.to_records()]
        with self._lock:
            self._records[_check_id(session.id)] = recs
            self._updated[session.id] = time.time()

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._records.pop(_check_id(session_id), None)
            self._updated.pop(session_id, None)

    def list(self, limit: int = 50) -> list[SessionInfo]:
        with self._lock:
            items = [(sid, self._updated.get(sid, 0.0), recs) for sid, recs in self._records.items()]
        items.sort(key=lambda x: x[1], reverse=True)
        out = []
        for sid, ts, recs in items[:limit]:
            head = next((r for r in recs if r.get("type") == "session"), {})
            out.append(SessionInfo(sid, ts, sum(1 for r in recs if r.get("type") == "message"),
                                   dict(head.get("meta") or {})))
        return out


class JsonlSessionStore:
    """One ``<id>.jsonl`` file per session under ``root`` (default ``~/.ntu_easy_llm/chats``).

    Appends are flushed per record; :meth:`save` rewrites the file atomically
    (temp file + ``os.replace``). A torn last line (crash mid-write) is skipped
    on load and counted in ``ChatSession.skipped_records``.
    """

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root is not None else DEFAULT_CHATS_DIR
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, session_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def _path(self, session_id: str) -> Path:
        return self.root / f"{_check_id(session_id)}.jsonl"

    def load(self, session_id: str) -> ChatSession | None:
        path = self._path(session_id)
        with self._lock(session_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return None
        records, bad = [], 0
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(rec, dict):
                records.append(rec)
            else:
                bad += 1
        s = ChatSession.from_records(records, session_id=session_id)
        s.skipped_records = bad
        s._store = self
        return s

    def append(self, session_id: str, record: dict) -> None:
        path = self._path(session_id)
        line = json.dumps(record, ensure_ascii=False)
        with self._lock(session_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as f:
                if f.tell() > 0:                       # a torn last line must not swallow this record
                    with path.open("rb") as r:
                        r.seek(-1, os.SEEK_END)
                        if r.read(1) != b"\n":
                            f.write(b"\n")
                f.write((line + "\n").encode("utf-8"))
                f.flush()

    def save(self, session: ChatSession) -> None:
        path = self._path(session.id)
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in session.to_records())
        with self._lock(session.id):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with tmp.open("w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        with self._lock(session_id):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def list(self, limit: int = 50) -> list[SessionInfo]:
        if not self.root.is_dir():
            return []
        files = sorted((p for p in self.root.glob("*.jsonl") if _SESSION_ID_RE.match(p.stem)),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        out = []
        for p in files:
            count, meta = 0, {}
            try:
                with p.open(encoding="utf-8") as f:
                    for line in f:
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(rec, dict):
                            continue
                        if rec.get("type") == "message":
                            count += 1
                        elif rec.get("type") == "session":
                            meta = dict(rec.get("meta") or {})
            except OSError:
                continue
            out.append(SessionInfo(p.stem, p.stat().st_mtime, count, meta))
        return out
