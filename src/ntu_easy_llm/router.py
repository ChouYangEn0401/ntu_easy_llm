"""Waterfall router: several adapters behind one adapter-shaped object (added in 0.6).

Like ad-network mediation: the first member serves every call; only when it
cannot (quota used up, key invalid, still rate-limited after retries — the
policy's *triggers*) does the next member get the same conversation. Ordinary
errors (a 500, a bad request, a timeout…) are raised as they are.

::

    from ntu_easy_llm import load_profiles
    from ntu_easy_llm.router import Router, FallbackRequired

    ps = load_profiles("llm.json")
    llm = ps.router("chat")                 # a chain id, or a single profile id
    r = llm.chat([{"role": "user", "content": "hi"}])
    print(r.text, r.profile_id, r.notice)   # notice: "已改用 backup（原因：額度用完）" after a switch

Modes (:class:`~ntu_easy_llm.waterfall.WaterfallPolicy`):

- ``off`` (default; also a chain without a ``waterfall`` key, or a single
  profile): only the first member is used, errors are raised unchanged — the
  same as using that adapter directly. No retries, no cooldown.
- ``auto``: a trigger error moves on to the next member that is not cooling
  down; the result says so (``switched``, ``notice``, ``events``).
- ``ask``: a trigger error raises :class:`FallbackRequired` carrying a
  :class:`PendingFallback`; the caller (a UI button…) calls
  :meth:`Router.resume` with the member to use, or ``use=None`` to give up.

Rules that hold in every mode:

- ``trust`` (encryption / key pinning failed) never switches — it cannot be a
  trigger. ``link_changed`` is raised as is and not retried.
- ``rate_limited`` is retried on the same member ``retry_429`` times (waiting
  ``Retry-After``, at most ``max_retry_wait_s``) before it counts as a trigger.
- A member that hit a trigger cools down in memory (``Retry-After`` or
  ``cooldown_s``); later calls skip it while it cools. Nothing is persisted.
- With a :class:`~ntu_easy_llm.conversation.ChatSession`, every member sees
  the same session; a switch is recorded with ``session.add_event`` and the
  reply is appended to the session.
- The order of members is whatever the caller configured; this module has no
  provider preference of its own.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .agent import ChatReply, LLMError
from .errors import ConfigError, ErrorKind, kind_of
from .profiles import Profile, ProfileSet
from .types import Message, as_messages
from .waterfall import (AttemptStarted, ChatResult, Event, Exhausted, Failed, FallbackNeeded, FallbackRequired,
                        MemberStatus, PendingFallback, Retrying, Skipped, Succeeded, Switched, WaterfallExhausted,
                        WaterfallPolicy, switch_notice)

__all__ = [
    "Router", "LLM", "ChatResult", "FallbackRequired", "WaterfallExhausted", "PendingFallback",
    "MemberStatus", "WaterfallPolicy",
]

_COOLING_KINDS = frozenset({ErrorKind.QUOTA_EXHAUSTED, ErrorKind.RATE_LIMITED, ErrorKind.AUTH_INVALID})
_MAX_PENDING = 64


@dataclass
class _Member:
    profile: Profile
    adapter: Any = None
    build_error: BaseException | None = None


@dataclass
class _State:
    cooling_until: float | None = None
    last_error: ErrorKind | None = None
    last_message: str = ""


@dataclass
class _Call:
    """Everything needed to run (or resume) one call."""

    messages: list[Message]
    tools: Any
    kw: dict
    session: Any = None
    events: list[Event] = field(default_factory=list)
    errors: list[tuple[str, BaseException]] = field(default_factory=list)


def _one_line(exc: BaseException, limit: int = 300) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("…" if len(text) > limit else "")


class Router:
    """A chain of adapters that looks like one adapter (``chat`` / ``list_models`` / ``probe`` / ``capabilities``).

    ``members``: adapters, :class:`Profile` objects, or profile ids (ids need
    ``profiles=``). Profiles are turned into adapters on first use, so a member
    that cannot run on this machine only matters when it is reached.

    ``waterfall``: a :class:`WaterfallPolicy`, its JSON dict, or ``None``
    (off). ``on_event`` is called for every :class:`~ntu_easy_llm.waterfall.Event`
    (exceptions from it are ignored). ``sleep`` / ``clock`` are injectable for tests.
    """

    def __init__(self, members: Any, *, waterfall: WaterfallPolicy | Mapping[str, Any] | None = None,
                 profiles: ProfileSet | None = None, chain_id: str = "", http: Any = None,
                 resolve_secret: Callable[[str], str | None] | None = None,
                 environ: Mapping[str, str] | None = None,
                 on_event: Callable[[Event], None] | None = None, locale: str = "zh-TW",
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time):
        if isinstance(members, (str, Profile)) or not isinstance(members, Sequence):
            members = [members]
        if not members:
            raise ConfigError("a router needs at least one member")
        self.policy = waterfall if isinstance(waterfall, WaterfallPolicy) else WaterfallPolicy.from_dict(waterfall)
        self.chain_id = str(chain_id or "")
        self.locale = locale
        self._http = http
        self._resolve_secret = resolve_secret if resolve_secret is not None else (
            profiles.resolve_secret if profiles is not None else None)
        self._environ = environ if environ is not None else (profiles.environ if profiles is not None else None)
        self._on_event = on_event
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.RLock()
        self._members: list[_Member] = []
        for m in members:
            if isinstance(m, str):
                if profiles is None:
                    raise ConfigError(f"member {m!r} is a profile id; pass profiles= to resolve it")
                self._members.append(_Member(profiles.get(m)))
            elif isinstance(m, Profile):
                self._members.append(_Member(m))
            elif hasattr(m, "chat") and isinstance(getattr(m, "profile", None), Profile):
                self._members.append(_Member(m.profile, adapter=m))
            else:
                raise ConfigError(f"router member must be an adapter, a Profile or a profile id, "
                                  f"not {type(m).__name__}")
        ids = [m.profile.id for m in self._members]
        if len(set(ids)) != len(ids):
            raise ConfigError("a router member is listed twice (profile ids must be unique)")
        self._state: dict[str, _State] = {pid: _State() for pid in ids}
        self._pending: dict[str, tuple[PendingFallback, _Call]] = {}

    # ------------------------------------------------------------------ #
    # construction helpers
    # ------------------------------------------------------------------ #
    @classmethod
    def from_profiles(cls, profiles: ProfileSet, name: str | None = None, **kw: Any) -> "Router":
        """A router for a chain id or a single profile id (``None`` → the config's default)."""
        members, policy = profiles.resolve(name)
        chain_id = (name or profiles.default) if (name or profiles.default) in profiles.chains else ""
        return cls(members, waterfall=policy, profiles=profiles, chain_id=chain_id, **kw)

    # ------------------------------------------------------------------ #
    # Adapter-shaped surface
    # ------------------------------------------------------------------ #
    @property
    def profile(self) -> Profile:
        """The first (primary) member's profile."""
        return self._members[0].profile

    @property
    def members(self) -> tuple[str, ...]:
        return tuple(m.profile.id for m in self._members)

    def adapter(self, profile_id: str) -> Any:
        """The adapter of one member (built on first use)."""
        for i, m in enumerate(self._members):
            if m.profile.id == profile_id:
                return self._adapter(i)
        raise ConfigError(f"{profile_id!r} is not a member of this router")

    def capabilities(self) -> Any:
        """The primary member's capabilities."""
        return self._adapter(0).capabilities()

    def list_models(self, *, timeout: float = 15.0) -> list:
        """The primary member's models."""
        return self._adapter(0).list_models(timeout=timeout)

    def probe(self, *, send: bool = False, timeout: float = 30.0) -> Any:
        """Probe the primary member (``status()`` shows the others' cooldowns)."""
        return self._adapter(0).probe(send=send, timeout=timeout)

    def chat(self, messages: Any, tools: Any = None, *, max_tokens: int | None = None,
             temperature: float | None = None, stop: Sequence[str] = (), timeout: float | None = None,
             on_delta: Callable[[str], None] | None = None, window_tokens: int | None = None) -> ChatResult:
        """Send ``messages`` (a list, or a :class:`~ntu_easy_llm.conversation.ChatSession`).

        With a session, ``session.window(window_tokens)`` is sent and the reply is
        appended to it. Returns :class:`ChatResult`; raises the member's error
        (off mode, or a non-trigger error), :class:`FallbackRequired` (ask) or
        :class:`WaterfallExhausted` (auto, nobody left).
        """
        session = messages if hasattr(messages, "window") and hasattr(messages, "add_event") else None
        msgs = session.window(window_tokens) if session is not None else as_messages(messages)
        call = _Call(messages=msgs, tools=tools, session=session,
                     kw={"max_tokens": max_tokens, "temperature": temperature, "stop": tuple(stop or ()),
                         "timeout": timeout, "on_delta": on_delta})
        if not self.policy.enabled:
            return self._finish(call, 0, self._adapter(0).chat(msgs, tools, **call.kw), switched=None)
        start = self._first_usable(call)
        if start is None:
            # nobody is free: the primary is cooling down
            return self._on_trigger(call, 0, self._cooled_error(0), tried=False)
        if start:
            return self._run(call, start, switched_reason=self._state[self.profile.id].last_error or ErrorKind.UNKNOWN)
        return self._run(call, start)

    def resume(self, pending: PendingFallback, *, use: str | None = None, messages: Any = None) -> ChatResult:
        """Continue an ``ask`` decision: ``use`` = one of ``pending.candidates``; ``None`` = give up.

        Giving up — or a pending that expired — raises the original error. The
        call's messages are kept by the router that raised; pass ``messages=``
        to resume on another router instance.
        """
        with self._lock:
            entry = self._pending.pop(pending.id, None)
        error = pending.error or (entry[0].error if entry else None) or LLMError(
            pending.message or "fallback declined", kind=pending.reason, profile_id=pending.profile_id)
        if use is None:
            raise error
        if pending.expired(self._clock()):
            raise error
        if use not in pending.candidates:
            raise ConfigError(f"{use!r} is not a candidate of this decision ({', '.join(pending.candidates)})")
        if entry is not None:
            call = entry[1]
        elif messages is not None:
            session = messages if hasattr(messages, "window") and hasattr(messages, "add_event") else None
            call = _Call(messages=session.window() if session is not None else as_messages(messages),
                         tools=None, session=session,
                         kw={"max_tokens": None, "temperature": None, "stop": (), "timeout": None, "on_delta": None})
        else:
            raise ConfigError("this router does not know that decision; pass messages= to resume it")
        idx = self._index(use)
        self._event(call, Switched(profile_id=pending.profile_id, to_id=use, reason=pending.reason))
        return self._run(call, idx, switched_reason=pending.reason)

    def status(self) -> list[MemberStatus]:
        """Each member's cooldown and last error, in chain order (for status screens)."""
        now = self._clock()
        out = []
        with self._lock:
            for m in self._members:
                st = self._state[m.profile.id]
                until = st.cooling_until if st.cooling_until and st.cooling_until > now else None
                out.append(MemberStatus(m.profile.id, until, st.last_error, st.last_message))
        return out

    def reset(self, profile_id: str | None = None) -> None:
        """Forget cooldowns (all members, or one)."""
        with self._lock:
            for pid in ([profile_id] if profile_id else list(self._state)):
                if pid in self._state:
                    self._state[pid] = _State()

    def pending(self) -> list[PendingFallback]:
        """``ask`` decisions this router is still holding (expired ones are dropped)."""
        self._prune()
        with self._lock:
            return [p for p, _ in self._pending.values()]

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _index(self, profile_id: str) -> int:
        for i, m in enumerate(self._members):
            if m.profile.id == profile_id:
                return i
        raise ConfigError(f"{profile_id!r} is not a member of this router")

    def _adapter(self, i: int) -> Any:
        m = self._members[i]
        with self._lock:
            if m.adapter is None:
                from .adapters.base import get_adapter
                m.adapter = get_adapter(m.profile, http=self._http, resolve_secret=self._resolve_secret,
                                        environ=self._environ)
            return m.adapter

    def _event(self, call: _Call, event: Event) -> None:
        call.events.append(event)
        if isinstance(event, Switched) and call.session is not None:
            call.session.add_event(event)
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 — an observer must not break the call
                pass

    def _cooling(self, i: int) -> float | None:
        st = self._state[self._members[i].profile.id]
        if st.cooling_until and st.cooling_until > self._clock():
            return st.cooling_until
        return None

    def _first_usable(self, call: _Call) -> int | None:
        """Index of the first member not cooling down; ask mode never skips the primary silently."""
        until = self._cooling(0)
        if until is None:
            return 0
        nxt = self._next_candidates(call, 0)
        if not nxt:
            return 0                                   # nobody else can take it: try the primary anyway
        if self.policy.mode == "ask":
            return None
        self._event(call, Skipped(profile_id=self.profile.id, until=until,
                                  reason=self._state[self.profile.id].last_error or ErrorKind.UNKNOWN))
        return self._index(nxt[0])

    def _cooled_error(self, i: int) -> LLMError:
        pid = self._members[i].profile.id
        st = self._state[pid]
        return LLMError(st.last_message or f"{pid} is cooling down", kind=st.last_error or ErrorKind.UNKNOWN,
                        profile_id=pid)

    def _fits(self, i: int, call: _Call) -> bool:
        """Can member ``i`` take this call at all (adapter builds; tools / images it says it cannot do)?"""
        m = self._members[i]
        try:
            caps = self._adapter(i).capabilities()
        except Exception as e:  # noqa: BLE001 — e.g. AdapterNotFound: the source is not installed here
            m.build_error = e
            return False
        if call.tools and getattr(caps, "tools", None) is False:
            return False
        if getattr(caps, "vision", None) is False and any(msg.images for msg in call.messages):
            return False
        return True

    def _next_candidates(self, call: _Call, after: int) -> list[str]:
        out = []
        for j in range(after + 1, len(self._members)):
            if self._cooling(j) is not None or not self._fits(j, call):
                continue
            out.append(self._members[j].profile.id)
        return out

    def _attempt(self, call: _Call, i: int) -> ChatReply:
        """Call member ``i``, retrying ``rate_limited`` per the policy."""
        adapter = self._adapter(i)
        pid = self._members[i].profile.id
        retries = 0
        while True:
            self._event(call, AttemptStarted(profile_id=pid, attempt=retries + 1, model=adapter.profile.model))
            try:
                return adapter.chat(call.messages, call.tools, **call.kw)
            except LLMError as e:
                if kind_of(e) is not ErrorKind.RATE_LIMITED or retries >= self.policy.retry_429:
                    raise
                ra = e.retry_after
                if ra is not None and ra > self.policy.max_retry_wait_s:
                    raise
                wait = ra if ra is not None else min(float(2 ** retries), self.policy.max_retry_wait_s)
                retries += 1
                self._event(call, Retrying(profile_id=pid, attempt=retries, wait_s=wait))
                if wait > 0:
                    self._sleep(wait)

    def _run(self, call: _Call, i: int, *, switched_reason: ErrorKind | None = None) -> ChatResult:
        try:
            reply = self._attempt(call, i)
        except LLMError as e:
            return self._on_trigger(call, i, e, tried=True)
        return self._finish(call, i, reply, switched=switched_reason)

    def _on_trigger(self, call: _Call, i: int, e: LLMError, *, tried: bool) -> ChatResult:
        pid = self._members[i].profile.id
        kind = kind_of(e)
        if not e.profile_id:
            e.profile_id = pid
        if kind not in self.policy.triggers:
            self._event(call, Failed(profile_id=pid, reason=kind, message=_one_line(e)))
            raise e
        if tried:
            now = self._clock()
            cool = e.retry_after if (e.retry_after and kind is not ErrorKind.AUTH_INVALID) else self.policy.cooldown_s
            with self._lock:
                st = self._state[pid]
                st.last_error, st.last_message = kind, _one_line(e)
                if kind in _COOLING_KINDS and cool:
                    st.cooling_until = now + float(cool)
            call.errors.append((pid, e))
        candidates = self._next_candidates(call, i)
        if self.policy.mode == "ask":
            now = self._clock()
            pending = PendingFallback(profile_id=pid, reason=kind, candidates=tuple(candidates),
                                      message=_one_line(e), chain_id=self.chain_id,
                                      session_id=getattr(call.session, "id", "") or "",
                                      created_at=now,
                                      expires_at=now + self.policy.ask_ttl_s if self.policy.ask_ttl_s else 0.0,
                                      error=e)
            self._event(call, FallbackNeeded(profile_id=pid, reason=kind, candidates=pending.candidates,
                                             pending_id=pending.id))
            if candidates:
                self._prune()
                with self._lock:
                    self._pending[pending.id] = (pending, call)
                    while len(self._pending) > _MAX_PENDING:
                        self._pending.pop(next(iter(self._pending)))
            raise FallbackRequired(pending)
        if not candidates:
            if not call.errors:
                call.errors.append((pid, e))
            self._event(call, Exhausted(profile_id=pid, reasons=tuple((p, kind_of(x)) for p, x in call.errors)))
            raise WaterfallExhausted(call.errors)
        nxt = candidates[0]
        self._event(call, Switched(profile_id=pid, to_id=nxt, reason=kind))
        return self._run(call, self._index(nxt), switched_reason=kind)

    def _finish(self, call: _Call, i: int, reply: ChatReply, *, switched: ErrorKind | None) -> ChatResult:
        m = self._members[i]
        pid = m.profile.id
        with self._lock:
            st = self._state[pid]
            st.cooling_until = None
        did_switch = switched is not None
        if self.policy.enabled:
            self._event(call, Succeeded(profile_id=pid, switched=did_switch))
        notice = switch_notice(pid, switched, self.locale, to_label=m.profile.label) if did_switch else ""
        model = getattr(getattr(m.adapter, "profile", None), "model", "") or m.profile.model
        result = ChatResult.from_reply(reply, profile_id=pid, model=model, switched=did_switch, notice=notice,
                                       events=list(call.events))
        if call.session is not None:
            call.session.add(Message(role="assistant", content=reply.text, tool_calls=list(reply.tool_calls),
                                     meta={"profile_id": pid, "model": model,
                                           "usage": {"input_tokens": reply.usage.input_tokens,
                                                     "output_tokens": reply.usage.output_tokens}}))
        return result

    def _prune(self) -> None:
        now = self._clock()
        with self._lock:
            for pid in [k for k, (p, _) in self._pending.items() if p.expired(now)]:
                self._pending.pop(pid, None)


#: The name the design notes use; same class.
LLM = Router
