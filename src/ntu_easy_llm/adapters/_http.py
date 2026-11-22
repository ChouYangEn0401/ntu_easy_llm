"""Shared plumbing for the 0.6 HTTP adapters: transport, proxy choice, the common adapter base.

Transport
---------
Every HTTP adapter sends through one object with a requests-style method::

    http.request(method, url, json=None, headers=None, timeout=None, stream=False) -> response

and reads ``response.status_code``, ``response.headers``, ``response.text`` and
``response.iter_lines()``. A ``requests.Session`` fits as is; so does any test
fake with those names. An object that only has ``post`` (the 0.5 ``agent``
fake-session shape) is wrapped. With no ``http=``, :class:`UrllibTransport`
(standard library only) is used.

Proxies
-------
Addresses on this machine or the LAN (loopback, private ranges, link-local,
100.64/10, ``localhost``, ``*.local`` / ``*.lan`` / ``*.internal`` /
``*.home.arpa``, single-label host names) and every ``local/*`` source go
**direct** (``ProxyHandler({})``): a system proxy or VPN client would otherwise
swallow them. Remote services use the system proxy settings. A profile can
force it with ``options.proxy``: ``"auto"`` (default) / ``"none"`` / ``"system"``.
"""
from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..agent import (ChatReply, LLMConnectionError, LLMError, LLMHTTPError, LLMResponseError, Tool, _tool)
from ..errors import ConfigError, ErrorKind, classify
from ..profiles import Profile
from ..types import Message, as_messages, normalize_finish
from .base import Capabilities, ModelInfo, ProbeResult

__all__ = ["UrllibTransport", "is_local_url", "HttpAdapter", "iter_sse", "make_transport"]

_LAN_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")      # carrier-grade NAT; also used by mesh VPNs


def is_local_url(url: str) -> bool:
    """True when ``url`` points at this machine or the LAN (see module docstring)."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(_LAN_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host                    # single-label names resolve on the LAN
    if ip.version == 4 and ip in _CGNAT:
        return True
    return ip.is_loopback or ip.is_private or ip.is_link_local


# =============================================================================
# Transport
# =============================================================================

class _UrllibResponse:
    """requests-like view of a urllib response (or of an HTTPError)."""

    def __init__(self, status: int, headers: Mapping[str, str], fp: Any):
        self.status_code = status
        self.headers = dict(headers)
        self._fp = fp
        self._text: str | None = None

    @property
    def text(self) -> str:
        if self._text is None:
            raw = self._fp.read() if self._fp is not None else b""
            self._text = raw.decode("utf-8", "replace")
            self.close()
        return self._text

    def json(self) -> Any:
        return json.loads(self.text)

    def iter_lines(self) -> Iterator[str]:
        if self._text is not None:
            yield from self._text.splitlines()
            return
        try:
            for raw in self._fp:                 # line by line as the server sends them
                yield raw.decode("utf-8", "replace").rstrip("\r\n")
        finally:
            self.close()

    def close(self) -> None:
        fp, self._fp = self._fp, None
        if fp is not None:
            try:
                fp.close()
            except Exception:  # noqa: BLE001
                pass


class UrllibTransport:
    """Standard-library transport. ``direct=True`` ignores every proxy setting."""

    def __init__(self, *, direct: bool = False):
        self.direct = bool(direct)
        handlers = [urllib.request.ProxyHandler({})] if self.direct else []
        self._opener = urllib.request.build_opener(*handlers)

    def request(self, method: str, url: str, json: Any = None, headers: Mapping[str, str] | None = None,
                timeout: float | None = None, stream: bool = False) -> _UrllibResponse:
        import json as _json
        data = None if json is None else _json.dumps(json, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=dict(headers or {}))
        try:
            resp = self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            return _UrllibResponse(e.code, dict(e.headers.items()) if e.headers else {}, e)
        resp_obj = _UrllibResponse(resp.status, dict(resp.headers.items()), resp)
        if not stream:
            _ = resp_obj.text                    # read now so the socket is released
        return resp_obj


class _PostOnlySession:
    """Wrap a 0.5-style fake session that only has ``post`` (and maybe ``get``)."""

    def __init__(self, session: Any):
        self._s = session

    def request(self, method: str, url: str, json: Any = None, headers=None, timeout=None, stream=False):
        if method.upper() == "GET":
            get = getattr(self._s, "get", None)
            if get is None:
                raise LLMConnectionError("the injected http session cannot send GET requests")
            return get(url, headers=headers, timeout=timeout)
        return self._s.post(url, json=json, headers=headers, timeout=timeout)


def make_transport(http: Any, *, direct: bool) -> Any:
    """The transport an adapter uses: the injected ``http`` (wrapped if needed) or urllib."""
    if http is None:
        return UrllibTransport(direct=direct)
    if callable(getattr(http, "request", None)):
        return http
    if callable(getattr(http, "post", None)):
        return _PostOnlySession(http)
    raise TypeError("http= must have request(method, url, json=, headers=, timeout=, stream=) or post(...)")


def _lines(resp: Any) -> Iterator[str]:
    it = getattr(resp, "iter_lines", None)
    if callable(it):
        for line in it():
            yield line.decode("utf-8", "replace") if isinstance(line, (bytes, bytearray)) else str(line)
    else:
        yield from str(getattr(resp, "text", "") or "").splitlines()


def iter_sse(lines: Iterator[str]) -> Iterator[tuple[str, str]]:
    """Server-sent events → ``(event, data)`` pairs (multi-line ``data:`` joined with ``\\n``)."""
    event, data = "", []
    for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


# =============================================================================
# Adapter base
# =============================================================================

def _first(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


class HttpAdapter:
    """Common part of every HTTP adapter. Subclasses set the class attributes and implement
    ``_chat`` and ``_list``."""

    #: provider name for :func:`~ntu_easy_llm.errors.classify` and messages
    provider: str = ""
    #: default ``base_url`` when the profile has none (``None`` → base_url is required)
    default_base_url: str | None = None
    #: a key is required (remote api_key sources)
    needs_key: bool = False
    #: default per-call timeout in seconds when neither the call nor ``options.timeout`` give one
    default_timeout: float = 90.0
    #: probe(send=False) fails when the configured model is not listed (False for generic gateways)
    strict_model_check: bool = True
    #: base capabilities; local adapters let ``options.tools`` / ``options.vision`` override
    base_capabilities: Capabilities = Capabilities()

    def __init__(self, profile: Profile, *, http: Any = None,
                 resolve_secret: Callable[[Any], str] | None = None):
        self.profile = profile
        url = profile.base_url or self.default_base_url
        if not url:
            raise ConfigError(f"profile {profile.id}: {profile.source} needs base_url (or base_url_env)")
        self.base_url = url.rstrip("/")
        if self.needs_key and profile.secret is None:
            raise ConfigError(f"profile {profile.id}: {profile.source} needs api_key_env or secret")
        self._resolve = resolve_secret
        proxy = str(profile.option("proxy", "auto") or "auto").lower()
        if proxy not in ("auto", "none", "system"):
            raise ConfigError(f"profile {profile.id}: options.proxy must be auto, none or system")
        self.direct = proxy == "none" or (proxy == "auto" and (profile.family == "local"
                                                               or is_local_url(self.base_url)))
        self.http = make_transport(http, direct=self.direct)

    # -- options -----------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        c = self.base_capabilities
        tools = self.profile.option("tools")
        vision = self.profile.option("vision")
        if tools is None and vision is None:
            return c
        return Capabilities(tools=c.tools if tools is None else bool(tools),
                            vision=c.vision if vision is None else bool(vision),
                            stop=c.stop, stream=c.stream, usage=c.usage)

    def _timeout(self, timeout: float | None) -> float:
        return float(_first(timeout, self.profile.option("timeout"), self.default_timeout))

    def _max_tokens(self, max_tokens: int | None) -> int | None:
        v = _first(max_tokens, self.profile.option("max_tokens"))
        return int(v) if v is not None else None

    def _temperature(self, temperature: float | None) -> float | None:
        v = _first(temperature, self.profile.option("temperature"))
        return float(v) if v is not None else None

    def _model(self) -> str:
        if not self.profile.model:
            raise LLMError(f"profile {self.profile.id}: no model configured", kind=ErrorKind.BAD_REQUEST,
                           profile_id=self.profile.id)
        return self.profile.model

    def _key(self) -> str:
        """The API key, fetched now (never cached on the object, never logged)."""
        if self.profile.secret is None:
            return ""
        if self._resolve is None:
            raise LLMError(f"profile {self.profile.id}: no secret resolver", kind=ErrorKind.AUTH_INVALID,
                           profile_id=self.profile.id)
        try:
            return self._resolve(self.profile.secret)
        except ConfigError as e:                  # message names the reference only
            raise LLMError(f"profile {self.profile.id}: {e}", kind=ErrorKind.AUTH_INVALID,
                           profile_id=self.profile.id) from None

    def _extra_body(self) -> dict:
        extra = self.profile.option("extra_body") or {}
        if not isinstance(extra, Mapping):
            raise LLMError(f"profile {self.profile.id}: options.extra_body must be an object",
                           kind=ErrorKind.BAD_REQUEST, profile_id=self.profile.id)
        return dict(extra)

    # -- HTTP ----------------------------------------------------------------------
    def _send(self, method: str, url: str, *, body: Any = None, headers: Mapping[str, str] | None = None,
              timeout: float, stream: bool = False, tools_sent: bool = False) -> Any:
        """Send one request; non-2xx → :class:`LLMHTTPError` (classified); unreachable → connection error."""
        try:
            resp = self.http.request(method, url, json=body, headers=dict(headers or {}), timeout=timeout,
                                     stream=stream)
        except LLMError as e:
            if not e.profile_id:
                e.profile_id = self.profile.id
            raise
        except Exception as e:  # noqa: BLE001 — urllib / requests / socket errors all mean "unreachable"
            kind = classify(self.provider, exc=e)[0]
            if kind is ErrorKind.UNKNOWN:
                kind = ErrorKind.CONNECTION
            raise LLMConnectionError(f"{self.provider}: cannot reach {url}: {type(e).__name__}: {e}",
                                     kind=kind, profile_id=self.profile.id) from e
        status = int(getattr(resp, "status_code", 0) or 0)
        if not 200 <= status < 300:
            self._raise_http(status, str(getattr(resp, "text", "") or ""), getattr(resp, "headers", None),
                             tools_sent=tools_sent)
        return resp

    def _raise_http(self, status: int, body: str, headers: Any, *, tools_sent: bool = False) -> None:
        hdrs = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, Mapping) else {}
        kind, wait = classify(self.provider, status=status, body=body, headers=hdrs, tools_sent=tools_sent)
        raise LLMHTTPError(status, body, self.provider, headers=hdrs, kind=kind, retry_after=wait,
                           profile_id=self.profile.id)

    def _json(self, resp: Any) -> Any:
        try:
            if callable(getattr(resp, "json", None)):
                return resp.json()
            return json.loads(resp.text)
        except (ValueError, TypeError) as e:
            raise LLMResponseError(f"{self.provider} returned a non-JSON body",
                                   profile_id=self.profile.id) from e

    def _get_json(self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float) -> Any:
        return self._json(self._send("GET", url, headers=headers, timeout=timeout))

    def _bad_response(self, what: str, cause: BaseException | None = None) -> LLMResponseError:
        e = LLMResponseError(f"{self.provider}: {what}", profile_id=self.profile.id)
        if cause is not None:
            e.__cause__ = cause
        return e

    # -- Adapter -------------------------------------------------------------------
    def chat(self, messages: Sequence[Message | Mapping[str, Any]],
             tools: Sequence[Tool | Mapping[str, Any]] | None = None, *,
             max_tokens: int | None = None, temperature: float | None = None, stop: Sequence[str] = (),
             timeout: float | None = None, on_delta: Callable[[str], None] | None = None) -> ChatReply:
        try:
            msgs = as_messages(messages)
            tool_list = [_tool(t) for t in (tools or [])]
        except (ValueError, TypeError, KeyError) as e:
            raise LLMError(f"profile {self.profile.id}: bad message or tool: {e}", kind=ErrorKind.BAD_REQUEST,
                           profile_id=self.profile.id) from e
        caps = self.capabilities()
        if tool_list and caps.tools is False:
            raise LLMError(f"profile {self.profile.id}: tool calling is turned off for this source",
                           kind=ErrorKind.TOOLS_UNSUPPORTED, profile_id=self.profile.id)
        if caps.vision is False and any(m.images for m in msgs):
            raise LLMError(f"profile {self.profile.id}: this source does not accept images",
                           kind=ErrorKind.BAD_REQUEST, profile_id=self.profile.id)
        warnings: list[str] = []
        stop_list = [str(s) for s in (stop or ()) if str(s)]
        if stop_list and not caps.stop:
            warnings.append("stop is not supported by this source; ignored")
            stop_list = []
        reply = self._chat(msgs, tool_list, max_tokens=self._max_tokens(max_tokens),
                           temperature=self._temperature(temperature), stop=stop_list,
                           timeout=self._timeout(timeout), on_delta=on_delta)
        reply.warnings[:0] = warnings
        if not reply.finish:
            reply.finish = normalize_finish(reply.stop_reason, text=reply.text,
                                            has_tool_calls=bool(reply.tool_calls))
        return reply

    def list_models(self, *, timeout: float = 15.0) -> list[ModelInfo]:
        return self._list(timeout=timeout)

    def _model_listed(self, models: Sequence[ModelInfo]) -> bool:
        want = self.profile.model
        return not want or any(m.id == want for m in models)

    def probe(self, *, send: bool = False, timeout: float = 30.0) -> ProbeResult:
        start = time.monotonic()
        models: tuple[ModelInfo, ...] = ()
        note = ""
        try:
            try:
                models = tuple(self.list_models(timeout=timeout))
            except LLMHTTPError as e:
                if e.status != 404:
                    raise
                note = "connected; this server does not list models"
            if models and not self._model_listed(models):
                msg = f"model {self.profile.model!r} is not in the server's model list"
                if self.strict_model_check and not send:
                    return ProbeResult(False, msg, ErrorKind.MODEL_NOT_FOUND, models, time.monotonic() - start)
                note = msg
            if send:
                reply = self.chat([{"role": "user", "content": "Reply with one word: ok"}],
                                  max_tokens=256, timeout=timeout)
                if not (reply.text or reply.tool_calls):
                    why = ("the model used every token thinking" if reply.finish == "thinking"
                           else "connected, but the model returned an empty reply")
                    return ProbeResult(False, why, ErrorKind.BAD_RESPONSE, models, time.monotonic() - start)
                note = ""
        except LLMError as e:
            return ProbeResult(False, str(e), e.kind, models, time.monotonic() - start)
        except ConfigError as e:
            return ProbeResult(False, str(e), ErrorKind.BAD_REQUEST, models, time.monotonic() - start)
        except Exception as e:  # noqa: BLE001 — probe never raises
            return ProbeResult(False, f"{type(e).__name__}: {e}", ErrorKind.UNKNOWN, models,
                               time.monotonic() - start)
        return ProbeResult(True, note, None, models, time.monotonic() - start)

    # -- subclasses ----------------------------------------------------------------
    def _chat(self, msgs: list[Message], tools: list[Tool], *, max_tokens: int | None,
              temperature: float | None, stop: list[str], timeout: float,
              on_delta: Callable[[str], None] | None) -> ChatReply:  # pragma: no cover - abstract
        raise NotImplementedError

    def _list(self, *, timeout: float) -> list[ModelInfo]:  # pragma: no cover - abstract
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}(profile={self.profile.id!r}, source={self.profile.source!r})"


def model_kind(model_id: str) -> str:
    """Guess ``ModelInfo.kind`` from an id: ``embedding`` / ``unknown`` (audio, image, moderation) / ``chat``."""
    low = model_id.lower()
    if "embed" in low:
        return "embedding"
    if any(t in low for t in ("whisper", "tts", "dall-e", "gpt-image", "moderation", "transcribe", "imagen",
                              "veo", "rerank")):
        return "unknown"
    return "chat"
