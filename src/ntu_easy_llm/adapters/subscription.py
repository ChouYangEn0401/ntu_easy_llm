"""Subscription adapters: an official CLI already signed in on this machine (``remote/subscription/*``).

========================================  ========================  ==========================  ==========================
source                                    login package (import)    pip name                    install
========================================  ========================  ==========================  ==========================
``remote/subscription/claude``            ``claude_subscription``   ``claude-subscription``     ``ntu_easy_llm[claude-login]``
``remote/subscription/github_copilot``    ``github_copilot_login``  ``github_copilot_login``    ``ntu_easy_llm[copilot]``
``remote/subscription/chatgpt``           ``chatgpt_login``         ``chatgpt_login``           ``ntu_easy_llm[chatgpt]``
========================================  ========================  ==========================  ==========================

``ntu_easy_llm[subscriptions]`` installs all three. The extras pin each login
package's GitHub release wheel by URL (they are not on PyPI; do not
``pip install`` the bare names).

The login packages are separate projects and stay that way: each wraps an
official CLI and keeps that CLI's own MCP / agent features. This library only
uses their ``ask()``, so whatever they add later does not change what happens here.

These sources use **your own** subscription through the vendor's own CLI.
They are meant for the person who signed in on this machine; do not put them
behind a service other people use (check the vendor's terms).

All three login packages share one interface (``INTERFACE_VERSION == "1"``):
``ask(prompt_or_messages, *, model=None, timeout=…) -> Result(text, usage,
model, raw)``, errors derived from ``LoginError`` with a ``.kind`` string, and
a ``CAPABILITIES`` dict. This module wraps that interface; the package is
imported only when the adapter is built (importing this module imports none).

What these sources can do: **text in, text out**. No tool calling (a call
with ``tools`` raises ``TOOLS_UNSUPPORTED`` — never silently dropped), no
images, no ``stop``; multi-turn messages are flattened into one transcript by
the login package. ``max_tokens`` / ``temperature`` / ``effort`` are not
supported by the CLIs and are ignored (with a line in ``ChatReply.warnings``).
"""
from __future__ import annotations

import importlib
import importlib.util
import time
from typing import Any, Callable, Mapping

from ..agent import ChatReply, LLMConnectionError, LLMError, Usage, _tool
from ..errors import ConfigError, ErrorKind
from ..profiles import Profile
from ..types import as_messages, normalize_finish
from .base import Capabilities, ModelInfo, ProbeResult

__all__ = ["SubscriptionAdapter", "PACKAGES", "EXTRAS", "TOOLS_UNSUPPORTED_MESSAGE", "kind_from_login",
           "claude", "github_copilot", "chatgpt"]

#: provider -> (import name, pip name, CLI finder function in the package)
PACKAGES: dict[str, tuple[str, str, str]] = {
    "claude": ("claude_subscription", "claude-subscription", "find_claude_binary"),
    "github_copilot": ("github_copilot_login", "github_copilot_login", "find_copilot"),
    "chatgpt": ("chatgpt_login", "chatgpt_login", "find_codex"),
}

#: provider -> the ntu_easy_llm extra that installs its login package (``subscriptions`` installs all three)
EXTRAS: dict[str, str] = {"claude": "claude-login", "github_copilot": "copilot", "chatgpt": "chatgpt"}

#: what a call with ``tools`` gets (kind ``tools_unsupported``)
TOOLS_UNSUPPORTED_MESSAGE = ("這個來源只能聊天，不支援工具（MCP）；要用工具請改用 API 金鑰或本地來源 / "
                             "this source is chat only and does not support tools (MCP); "
                             "use an API-key or local source for tools")

#: LoginError.kind -> ErrorKind
_LOGIN_KIND: dict[str, ErrorKind] = {
    "quota_exhausted": ErrorKind.QUOTA_EXHAUSTED,
    "auth_invalid": ErrorKind.AUTH_INVALID,
    "timeout": ErrorKind.TIMEOUT,
    "not_installed": ErrorKind.CONNECTION,     # the CLI cannot be started on this machine
    "bad_request": ErrorKind.BAD_REQUEST,
    "unsupported": ErrorKind.BAD_REQUEST,
    "unknown": ErrorKind.UNKNOWN,
}


def kind_from_login(exc: BaseException) -> ErrorKind:
    """``LoginError.kind`` (a string) → :class:`ErrorKind`."""
    if type(exc).__name__ == "PromptTooLongError":
        return ErrorKind.CONTEXT_TOO_LONG
    return _LOGIN_KIND.get(str(getattr(exc, "kind", "") or "unknown"), ErrorKind.UNKNOWN)


def _install_hint(provider: str) -> str:
    _, pip_name, _ = PACKAGES[provider]
    extra = EXTRAS[provider]
    return (f"要先安裝 ntu_easy_llm[{extra}]（{pip_name} 套件）/ "
            f"install ntu_easy_llm[{extra}] (the {pip_name} package) first")


def _check_for(provider: str) -> Callable[[], tuple[bool, str]]:
    module = PACKAGES[provider][0]

    def check() -> tuple[bool, str]:
        # find_spec on a top-level name does not import (run) the package
        if importlib.util.find_spec(module) is None:
            return False, _install_hint(provider)
        return True, ""

    return check


class SubscriptionAdapter:
    """One subscription source. ``module`` (the login package) can be injected for tests."""

    def __init__(self, profile: Profile, provider: str, *, module: Any = None):
        if provider not in PACKAGES:
            raise ConfigError(f"unknown subscription provider {provider!r}")
        self.profile = profile
        self.provider = provider
        self._module = module

    def _mod(self) -> Any:
        if self._module is None:
            name = PACKAGES[self.provider][0]
            if importlib.util.find_spec(name) is None:
                raise ConfigError(_install_hint(self.provider))
            self._module = importlib.import_module(name)
        return self._module

    def _raise(self, e: BaseException) -> LLMError:
        kind = kind_from_login(e)
        msg = f"{self.provider}: {' '.join(str(e).split())[:500]}"
        retry_after = getattr(e, "retry_after", None)
        if kind is ErrorKind.AUTH_INVALID:
            msg += " — sign in with the official CLI on this machine first"
        if kind in (ErrorKind.TIMEOUT, ErrorKind.CONNECTION):
            return LLMConnectionError(msg, kind=kind, retry_after=retry_after, profile_id=self.profile.id)
        return LLMError(msg, kind=kind, retry_after=retry_after, profile_id=self.profile.id)

    # -- Adapter ---------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        caps: Mapping[str, Any] = getattr(self._module, "CAPABILITIES", None) or {}
        return Capabilities(tools=bool(caps.get("tools", False)) if caps else False,
                            vision=bool(caps.get("images", False)) if caps else False,
                            stop=False, stream=False, usage=bool(caps.get("usage", False)) if caps else False)

    def chat(self, messages, tools=None, *, max_tokens=None, temperature=None, stop=(), timeout=None,
             on_delta: Callable[[str], None] | None = None) -> ChatReply:
        msgs = as_messages(messages)
        if [_tool(t) for t in (tools or [])]:
            raise LLMError(f"{self.provider} subscription: {TOOLS_UNSUPPORTED_MESSAGE}",
                           kind=ErrorKind.TOOLS_UNSUPPORTED, profile_id=self.profile.id)
        if any(m.images for m in msgs):
            raise LLMError(f"{self.provider} subscription: images are not supported (text only)",
                           kind=ErrorKind.BAD_REQUEST, profile_id=self.profile.id)
        if any(m.role == "tool" or m.tool_calls for m in msgs):
            raise LLMError(f"{self.provider} subscription: tool messages cannot be sent to a text-only source",
                           kind=ErrorKind.BAD_REQUEST, profile_id=self.profile.id)
        if not msgs or msgs[-1].role != "user":
            raise LLMError(f"{self.provider} subscription: the last message must be from the user",
                           kind=ErrorKind.BAD_REQUEST, profile_id=self.profile.id)
        warnings = []
        if stop:
            warnings.append("stop is not supported by this source; ignored")
        for name, value in (("max_tokens", max_tokens), ("temperature", temperature),
                            ("effort", self.profile.option("effort"))):
            if value is not None:
                warnings.append(f"{name} is not supported by this source; ignored")
        mod = self._mod()
        wire = [{"role": m.role, "content": m.content} for m in msgs]
        t = timeout or self.profile.option("timeout") or 180
        try:
            result = mod.ask(wire, model=self.profile.model or None, timeout=t)
        except Exception as e:  # noqa: BLE001 — LoginError family, ValueError from message checks
            if getattr(e, "kind", None) is None and isinstance(e, ValueError):
                raise LLMError(f"{self.provider} subscription: {e}", kind=ErrorKind.BAD_REQUEST,
                               profile_id=self.profile.id) from None
            raise self._raise(e) from None
        text = str(getattr(result, "text", "") or "")
        usage = getattr(result, "usage", None) or {}
        reply = ChatReply(text=text, usage=Usage(int(usage.get("input") or 0), int(usage.get("output") or 0)),
                          raw=getattr(result, "raw", None), stop_reason="stop",
                          finish=normalize_finish("stop", text=text), warnings=warnings)
        if on_delta and text:
            on_delta(text)
        return reply

    def list_models(self, *, timeout: float = 15.0) -> list[ModelInfo]:
        """The configured model, or the CLI's default (the CLIs do not list models)."""
        caps = getattr(self._mod(), "CAPABILITIES", None) or {}
        model = self.profile.model or str(caps.get("default_model") or "")
        return [ModelInfo(id=model, label=model or "(CLI default)")] if model else []

    def probe(self, *, send: bool = False, timeout: float = 30.0) -> ProbeResult:
        """``send=False``: is the CLI there (no call, no login check). ``send=True``: one tiny prompt."""
        start = time.monotonic()
        try:
            mod = self._mod()
            finder = getattr(mod, PACKAGES[self.provider][2], None)
            if finder is not None:
                try:
                    found = finder()
                except Exception:  # noqa: BLE001 — e.g. ClaudeNotFoundError
                    found = None
                if not found:
                    return ProbeResult(False, f"{self.provider}: the official CLI was not found on this machine",
                                       ErrorKind.CONNECTION, (), time.monotonic() - start)
            models = tuple(self.list_models(timeout=timeout))
            if send:
                reply = self.chat([{"role": "user", "content": "Reply with one word: ok"}], timeout=timeout)
                if not reply.text:
                    return ProbeResult(False, "the CLI returned an empty reply", ErrorKind.BAD_RESPONSE, models,
                                       time.monotonic() - start)
            else:
                return ProbeResult(True, "CLI found; sign-in is only checked when a prompt is sent (send=True)",
                                   None, models, time.monotonic() - start)
        except LLMError as e:
            return ProbeResult(False, str(e), e.kind, (), time.monotonic() - start)
        except ConfigError as e:
            return ProbeResult(False, str(e), None, (), time.monotonic() - start)
        return ProbeResult(True, "", None, models, time.monotonic() - start)


def _factory(provider: str) -> Callable[..., SubscriptionAdapter]:
    def factory(profile: Profile, *, http: Any = None, resolve_secret: Any = None) -> SubscriptionAdapter:
        return SubscriptionAdapter(profile, provider)

    factory.check = _check_for(provider)  # type: ignore[attr-defined]
    factory.__name__ = provider
    return factory


claude = _factory("claude")
github_copilot = _factory("github_copilot")
chatgpt = _factory("chatgpt")
