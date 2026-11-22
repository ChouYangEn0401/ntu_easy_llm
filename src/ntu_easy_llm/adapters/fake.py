"""A scripted adapter for tests (no network). Source path: ``local/fake``.

Give it a *script* — one step per ``chat`` call — and it answers or fails in
that order. Steps can be::

    "hello"                                   # a text reply
    ChatReply(...)                            # returned as is
    ErrorKind.QUOTA_EXHAUSTED                 # raise an LLMError of that kind
    LLMHTTPError(429, "...")                  # any exception: raised
    lambda messages, tools, **kw: ...         # called; returns any step above
    {"text": "hi", "tool_calls": [{"id": "c1", "name": "f", "arguments": {}}],
     "usage": {"input_tokens": 3, "output_tokens": 1}, "finish": "tool_calls"}
    {"error": "rate_limited", "retry_after": 2, "message": "slow down"}

The dict forms are plain JSON, so a profiles file can drive it::

    {"source": "local/fake", "model": "fake-1",
     "options": {"script": ["first", {"error": "quota_exhausted"}], "default": "again"}}

After the script runs out, ``default`` is used for every further call; with no
``default`` the adapter raises :class:`FakeScriptExhausted` (an AssertionError,
so a test that calls more often than planned fails loudly).

Every call is recorded in ``adapter.calls`` (messages already converted to
:class:`~ntu_easy_llm.types.Message`).
"""
from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..agent import (ChatReply, LLMConnectionError, LLMError, LLMHTTPError, LLMResponseError, Tool, ToolCall,
                     Usage, _tool)
from ..errors import ErrorKind
from ..profiles import Profile
from ..types import as_messages, normalize_finish
from .base import Capabilities, ModelInfo, ProbeResult

__all__ = ["FakeAdapter", "FakeScriptExhausted", "fake_error", "from_profile"]

#: (status, body) used when a scripted error kind is an HTTP failure.
_HTTP_FOR_KIND: dict[ErrorKind, tuple[int, str]] = {
    ErrorKind.QUOTA_EXHAUSTED: (429, '{"error": {"code": "insufficient_quota"}}'),
    ErrorKind.AUTH_INVALID: (401, '{"error": {"code": "invalid_api_key"}}'),
    ErrorKind.RATE_LIMITED: (429, '{"error": {"code": "rate_limit_exceeded"}}'),
    ErrorKind.PERMISSION: (403, '{"error": {"code": "forbidden"}}'),
    ErrorKind.OVERLOADED: (503, '{"error": {"code": "overloaded"}}'),
    ErrorKind.CONTEXT_TOO_LONG: (400, '{"error": {"code": "context_length_exceeded"}}'),
    ErrorKind.MODEL_NOT_FOUND: (404, '{"error": {"code": "model_not_found"}}'),
    ErrorKind.TOOLS_UNSUPPORTED: (400, '{"error": {"message": "tools are not supported"}}'),
    ErrorKind.BAD_REQUEST: (400, '{"error": {"code": "bad_request"}}'),
    ErrorKind.SERVER_ERROR: (500, '{"error": {"code": "server_error"}}'),
    ErrorKind.LINK_CHANGED: (404, "Not Found"),
}


class FakeScriptExhausted(AssertionError):
    """The fake adapter was called more often than its script allows."""


def fake_error(kind: ErrorKind | str, *, message: str = "", retry_after: float | None = None,
               status: int | None = None, profile_id: str = "") -> LLMError:
    """Build the error a real adapter would raise for ``kind`` (right class, ``.kind`` set)."""
    kind = ErrorKind(kind)
    if kind in (ErrorKind.TIMEOUT, ErrorKind.CONNECTION):
        return LLMConnectionError(message or f"fake {kind}", kind=kind, retry_after=retry_after,
                                  profile_id=profile_id)
    if kind is ErrorKind.BAD_RESPONSE:
        return LLMResponseError(message or "fake bad response", retry_after=retry_after, profile_id=profile_id)
    if kind in _HTTP_FOR_KIND or status is not None:
        code, body = _HTTP_FOR_KIND.get(kind, (500, ""))
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        return LLMHTTPError(status or code, message or body, "fake", headers=headers, kind=kind,
                            retry_after=retry_after, profile_id=profile_id)
    return LLMError(message or f"fake {kind}", kind=kind, retry_after=retry_after, profile_id=profile_id)


def _reply_from_dict(d: Mapping[str, Any]) -> ChatReply:
    calls = [c if isinstance(c, ToolCall) else ToolCall(str(c.get("id") or f"call_{i}"), str(c.get("name") or ""),
                                                          dict(c.get("arguments") or {}))
             for i, c in enumerate(d.get("tool_calls") or [])]
    usage = d.get("usage") or {}
    text = str(d.get("text") or "")
    finish = str(d.get("finish") or normalize_finish("stop", text=text, has_tool_calls=bool(calls)))
    return ChatReply(text=text, tool_calls=calls,
                     usage=Usage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)),
                     raw=dict(d), stop_reason=finish, finish=finish, warnings=list(d.get("warnings") or []))


class FakeAdapter:
    """Scripted adapter. See the module docstring for the step formats."""

    def __init__(self, script: Iterable[Any] = (), *, profile: Profile | None = None,
                 default: Any = None, capabilities: Capabilities | None = None,
                 models: Sequence[ModelInfo | str | Mapping[str, Any]] | ErrorKind | BaseException = ("fake-1",),
                 probe_result: ProbeResult | None = None, delay_s: float = 0.0):
        self.profile = profile or Profile(id="fake", source="local/fake", model="fake-1")
        self._script: deque[Any] = deque(script)
        self._default = default
        self._caps = capabilities or Capabilities(tools=True, vision=True, stop=True, stream=True, usage=True)
        self._models = models
        self._probe = probe_result
        self._delay = float(delay_s)
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []

    # -- scripting helpers ---------------------------------------------------
    def push(self, *steps: Any) -> "FakeAdapter":
        """Append more steps to the script."""
        with self._lock:
            self._script.extend(steps)
        return self

    @property
    def remaining(self) -> int:
        return len(self._script)

    # -- Adapter -------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return self._caps

    def chat(self, messages, tools=None, *, max_tokens=None, temperature=None, stop=(), timeout=None,
             on_delta: Callable[[str], None] | None = None) -> ChatReply:
        msgs = as_messages(messages)
        tool_list: list[Tool] = [_tool(t) for t in (tools or [])]
        with self._lock:
            self.calls.append({"messages": msgs, "tools": tool_list, "max_tokens": max_tokens,
                               "temperature": temperature, "stop": tuple(stop or ()), "timeout": timeout})
            # capability errors do not consume a script step (a caller may retry without tools)
            if tool_list and self._caps.tools is False:
                raise fake_error(ErrorKind.TOOLS_UNSUPPORTED, profile_id=self.profile.id)
            if self._caps.vision is False and any(m.images for m in msgs):
                raise fake_error(ErrorKind.BAD_REQUEST, message="this source does not accept images",
                                 profile_id=self.profile.id)
            if self._script:
                step = self._script.popleft()
            elif self._default is not None:
                step = self._default
            else:
                raise FakeScriptExhausted(f"fake adapter {self.profile.id}: script used up "
                                          f"after {len(self.calls) - 1} calls")
        if self._delay:
            time.sleep(self._delay)
        reply = self._run(step, msgs, tool_list, max_tokens=max_tokens, temperature=temperature,
                          stop=stop, timeout=timeout)
        if stop and not self._caps.stop:
            reply.warnings.append("stop is not supported by this source; ignored")
        if on_delta and reply.text:
            for chunk in re.findall(r"\S+\s*|\s+", reply.text):
                on_delta(chunk)
        return reply

    def _run(self, step: Any, msgs, tools, **kw: Any) -> ChatReply:
        if callable(step) and not isinstance(step, (BaseException, type)):
            step = step(msgs, tools, **kw)
        if isinstance(step, BaseException):
            if isinstance(step, LLMError) and not step.profile_id:
                step.profile_id = self.profile.id
            raise step
        if isinstance(step, ErrorKind):
            raise fake_error(step, profile_id=self.profile.id)
        if isinstance(step, ChatReply):     # copy: a reused default must not collect warnings
            return dataclasses.replace(step, tool_calls=list(step.tool_calls), warnings=list(step.warnings))
        if isinstance(step, str):
            return ChatReply(text=step, stop_reason="stop", finish="stop", usage=Usage(len(json.dumps(
                [m.content for m in msgs])) // 4, max(1, len(step) // 4)))
        if isinstance(step, Mapping):
            if "error" in step:
                raise fake_error(step["error"], message=str(step.get("message") or ""),
                                 retry_after=step.get("retry_after"), status=step.get("status"),
                                 profile_id=self.profile.id)
            return _reply_from_dict(step)
        raise TypeError(f"fake adapter: unsupported script step {type(step).__name__}")

    def list_models(self, *, timeout: float = 15.0) -> list[ModelInfo]:
        m = self._models
        if isinstance(m, ErrorKind):
            raise fake_error(m, profile_id=self.profile.id)
        if isinstance(m, BaseException):
            raise m
        out: list[ModelInfo] = []
        for item in m:
            if isinstance(item, ModelInfo):
                out.append(item)
            elif isinstance(item, Mapping):
                out.append(ModelInfo(**dict(item)))
            else:
                out.append(ModelInfo(id=str(item)))
        return out

    def probe(self, *, send: bool = False, timeout: float = 30.0) -> ProbeResult:
        if self._probe is not None:
            return self._probe
        start = time.monotonic()
        try:
            models = tuple(self.list_models(timeout=timeout))
            if send:
                reply = self.chat([{"role": "user", "content": "Reply with one word: ok"}], timeout=timeout)
                if not (reply.text or reply.tool_calls):
                    return ProbeResult(False, "connected, but the model returned an empty reply",
                                       ErrorKind.BAD_RESPONSE, models, time.monotonic() - start)
        except LLMError as e:
            return ProbeResult(False, str(e), e.kind, (), time.monotonic() - start)
        return ProbeResult(True, "", None, models, time.monotonic() - start)


def from_profile(profile: Profile, *, http: Any = None, resolve_secret: Any = None) -> FakeAdapter:
    """Factory for ``local/fake`` profiles: reads ``script``, ``default``, ``models``, ``capabilities`` options."""
    opts = profile.options
    caps = opts.get("capabilities")
    return FakeAdapter(list(opts.get("script") or ()), profile=profile, default=opts.get("default"),
                       capabilities=Capabilities(**dict(caps)) if caps else None,
                       models=list(opts.get("models") or ([profile.model] if profile.model else ["fake-1"])))
