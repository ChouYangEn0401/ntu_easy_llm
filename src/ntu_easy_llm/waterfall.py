"""Waterfall (fallback chain) types and the JSON config format (added in 0.6).

A *chain* is an ordered list of profiles. With a waterfall policy turned on, a
call goes to the first member; when it fails with one of the policy's trigger
kinds, the next member gets the same conversation — like an ad network
mediation list: the primary is used until it cannot serve, then the next one.

**This module only defines the types and the config format.** The routing
itself (the class that wraps several adapters and looks like one adapter to
the caller) lives in ``ntu_easy_llm.router`` from a later 0.6 pre-release.

Config (a ``chains`` entry in a profiles file, see :mod:`ntu_easy_llm.profiles`)::

    {"members": ["main", "backup"],
     "waterfall": {"mode": "auto",
                   "triggers": ["quota_exhausted", "auth_invalid", "rate_limited"],
                   "retry_429": 2, "max_retry_wait_s": 30,
                   "cooldown_s": 900, "ask_ttl_s": 600}}

No ``waterfall`` key (or ``"mode": "off"``) means: no fallback — only the first
member is used, exactly as if it had been picked on its own.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, Iterable, Mapping

from .agent import ChatReply, LLMError
from .errors import DEFAULT_TRIGGERS, UNTRIGGERABLE, ConfigError, ErrorKind, describe_kind

__all__ = [
    "MODES", "WaterfallPolicy",
    "Event", "AttemptStarted", "Retrying", "Switched", "Skipped", "FallbackNeeded",
    "Succeeded", "Failed", "Exhausted", "EVENT_TYPES", "event_from_record",
    "ChatResult", "PendingFallback", "MemberStatus",
    "FallbackRequired", "WaterfallExhausted", "switch_notice",
]

MODES: tuple[str, ...] = ("off", "auto", "ask")


# =============================================================================
# Policy
# =============================================================================

@dataclass(frozen=True)
class WaterfallPolicy:
    """When and how a chain moves on to its next member.

    - ``mode``: ``"off"`` (default — no fallback), ``"auto"`` (switch and say
      so in ``ChatResult.notice``) or ``"ask"`` (raise :class:`FallbackRequired`
      and let the caller decide via the router's ``resume``).
    - ``triggers``: kinds that cause a switch. Default: quota used up, key
      invalid, and 429 after ``retry_429`` retries. ``trust`` is never allowed.
    - ``retry_429``: retries on ``rate_limited`` before it counts as a trigger.
    - ``max_retry_wait_s``: never wait longer than this for one retry; a longer
      ``Retry-After`` counts as "retries used up".
    - ``cooldown_s``: how long a member that hit its quota / rate limit is
      skipped when the server gave no ``Retry-After``.
    - ``ask_ttl_s``: how long a pending "ask" decision stays valid.
    """

    mode: str = "off"
    triggers: frozenset[ErrorKind] = DEFAULT_TRIGGERS
    retry_429: int = 2
    max_retry_wait_s: float = 30.0
    cooldown_s: float = 900.0
    ask_ttl_s: float = 600.0

    def __post_init__(self) -> None:
        mode = str(self.mode or "off").strip().lower()
        if mode not in MODES:
            raise ConfigError(f"waterfall.mode must be one of {', '.join(MODES)}, got {self.mode!r}")
        object.__setattr__(self, "mode", mode)
        try:
            triggers = frozenset(ErrorKind(t) for t in self.triggers)
        except ValueError as e:
            raise ConfigError(f"waterfall.triggers: {e}") from None
        bad = triggers & UNTRIGGERABLE
        if bad:
            raise ConfigError(f"waterfall.triggers may not contain {', '.join(sorted(bad))}")
        object.__setattr__(self, "triggers", triggers)
        for name in ("retry_429", "max_retry_wait_s", "cooldown_s", "ask_ttl_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ConfigError(f"waterfall.{name} must be a number >= 0")
        object.__setattr__(self, "retry_429", int(self.retry_429))

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "WaterfallPolicy":
        """Parse the ``waterfall`` JSON object. ``None`` / ``{}`` → off. Unknown keys → ConfigError."""
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise ConfigError("waterfall must be an object")
        allowed = {f.name for f in fields(cls)}
        unknown = sorted(k for k in data if k not in allowed and not str(k).startswith("_"))
        if unknown:
            raise ConfigError(f"waterfall: unknown keys {', '.join(unknown)}")
        kw = {k: v for k, v in data.items() if k in allowed}
        if "triggers" in kw:
            if isinstance(kw["triggers"], (str, bytes)) or not isinstance(kw["triggers"], Iterable):
                raise ConfigError("waterfall.triggers must be a list")
            kw["triggers"] = frozenset(kw["triggers"])
        return cls(**kw)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["triggers"] = sorted(str(t) for t in self.triggers)
        return d


# =============================================================================
# Events
# =============================================================================

def _plain(value: Any) -> Any:
    if isinstance(value, ErrorKind):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class Event:
    """Something the router did. Sent to ``on_event`` and kept in ``ChatResult.events``.

    ``profile_id`` is the member the event is about (for :class:`Switched`, the
    one that failed). ``at`` is a Unix timestamp.
    """

    profile_id: str = ""
    at: float = field(default_factory=time.time)
    name: ClassVar[str] = "event"

    def to_record(self) -> dict:
        """One JSONL record for a session log: ``{"v": 1, "type": "event", "event": <name>, ...}``."""
        rec: dict[str, Any] = {"v": 1, "type": "event", "event": self.name}
        for f in fields(self):
            rec[f.name] = _plain(getattr(self, f.name))
        return rec


@dataclass(frozen=True)
class AttemptStarted(Event):
    attempt: int = 1
    model: str = ""
    name: ClassVar[str] = "attempt_started"


@dataclass(frozen=True)
class Retrying(Event):
    attempt: int = 1
    wait_s: float = 0.0
    reason: ErrorKind = ErrorKind.RATE_LIMITED
    name: ClassVar[str] = "retrying"


@dataclass(frozen=True)
class Switched(Event):
    to_id: str = ""
    reason: ErrorKind = ErrorKind.UNKNOWN
    name: ClassVar[str] = "switched"


@dataclass(frozen=True)
class Skipped(Event):
    until: float | None = None
    reason: ErrorKind = ErrorKind.UNKNOWN
    name: ClassVar[str] = "skipped"


@dataclass(frozen=True)
class FallbackNeeded(Event):
    reason: ErrorKind = ErrorKind.UNKNOWN
    candidates: tuple[str, ...] = ()
    pending_id: str = ""
    name: ClassVar[str] = "fallback_needed"


@dataclass(frozen=True)
class Succeeded(Event):
    switched: bool = False
    name: ClassVar[str] = "succeeded"


@dataclass(frozen=True)
class Failed(Event):
    reason: ErrorKind = ErrorKind.UNKNOWN
    message: str = ""
    name: ClassVar[str] = "failed"


@dataclass(frozen=True)
class Exhausted(Event):
    """Every member failed. ``reasons`` = ``((profile_id, kind), ...)``."""

    reasons: tuple[tuple[str, ErrorKind], ...] = ()
    name: ClassVar[str] = "exhausted"


EVENT_TYPES: dict[str, type[Event]] = {
    cls.name: cls for cls in (AttemptStarted, Retrying, Switched, Skipped, FallbackNeeded,
                              Succeeded, Failed, Exhausted)
}


def event_from_record(rec: Mapping[str, Any]) -> Event:
    """Inverse of :meth:`Event.to_record` (unknown event names → a plain :class:`Event`)."""
    cls = EVENT_TYPES.get(str(rec.get("event") or ""), Event)
    kw: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in rec:
            continue
        v = rec[f.name]
        if f.name == "reason":
            v = ErrorKind(v)
        elif f.name == "candidates":
            v = tuple(v)
        elif f.name == "reasons":
            v = tuple((str(p), ErrorKind(k)) for p, k in v)
        kw[f.name] = v
    return cls(**kw)


# =============================================================================
# Results, pending decisions, member status
# =============================================================================

@dataclass
class ChatResult(ChatReply):
    """A :class:`ChatReply` plus which member answered and what the router did."""

    profile_id: str = ""
    model: str = ""
    switched: bool = False
    notice: str = ""                       # e.g. "已改用 backup（原因：額度用完）"; "" when nothing to say
    events: list[Event] = field(default_factory=list)

    @classmethod
    def from_reply(cls, reply: ChatReply, **extra: Any) -> "ChatResult":
        base = {f.name: getattr(reply, f.name) for f in fields(ChatReply)}
        base.update(extra)
        return cls(**base)


@dataclass(frozen=True)
class PendingFallback:
    """An "ask" mode decision waiting for the caller (UI button, prompt, …).

    ``to_dict()`` is safe to show or store (no exception object, no secrets).
    """

    profile_id: str                         # the member that failed
    reason: ErrorKind
    candidates: tuple[str, ...]             # members the caller may pick, in chain order
    message: str = ""                       # the failure, one line
    chain_id: str = ""
    session_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0                 # 0 = never
    error: BaseException | None = field(default=None, repr=False, compare=False)

    def expired(self, now: float | None = None) -> bool:
        return bool(self.expires_at) and (time.time() if now is None else now) >= self.expires_at

    def to_dict(self) -> dict:
        return {"id": self.id, "profile_id": self.profile_id, "reason": str(self.reason),
                "candidates": list(self.candidates), "message": self.message, "chain_id": self.chain_id,
                "session_id": self.session_id, "created_at": self.created_at, "expires_at": self.expires_at}


@dataclass(frozen=True)
class MemberStatus:
    """What the router knows about one chain member (for status screens)."""

    profile_id: str
    cooling_until: float | None = None
    last_error: ErrorKind | None = None
    last_message: str = ""


class FallbackRequired(LLMError):
    """Raised in ``ask`` mode: the call failed with a trigger kind and the caller must choose."""

    def __init__(self, pending: PendingFallback, message: str = ""):
        self.pending = pending
        super().__init__(message or f"{pending.profile_id}: {describe_kind(pending.reason, 'en')}; "
                                    f"fallback candidates: {', '.join(pending.candidates) or '(none)'}",
                         kind=pending.reason, profile_id=pending.profile_id)


class WaterfallExhausted(LLMError):
    """Every member of a chain failed. ``errors`` = ``((profile_id, exception), ...)`` in try order.

    ``kind`` / ``retry_after`` are those of the last error.
    """

    def __init__(self, errors: Iterable[tuple[str, BaseException]], message: str = ""):
        self.errors = tuple(errors)
        last = self.errors[-1][1] if self.errors else None
        kind = getattr(last, "kind", ErrorKind.UNKNOWN) if last is not None else ErrorKind.UNKNOWN
        summary = "; ".join(f"{p}: {getattr(e, 'kind', ErrorKind.UNKNOWN)}" for p, e in self.errors)
        super().__init__(message or f"all members failed ({summary or 'no members'})",
                         kind=kind, retry_after=getattr(last, "retry_after", None),
                         profile_id=self.errors[-1][0] if self.errors else "")


def switch_notice(to_id: str, reason: ErrorKind | str, locale: str = "zh-TW", *, to_label: str = "") -> str:
    """The one-line notice shown after an automatic switch."""
    who = to_label or to_id
    if locale == "zh-TW":
        return f"已改用 {who}（原因：{describe_kind(reason, locale)}）"
    return f"Switched to {who} (reason: {describe_kind(reason, 'en')})"
