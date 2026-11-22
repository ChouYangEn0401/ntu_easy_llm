"""Test helpers for the HTTP adapters: a scripted transport, SSE builders, and a network block.

Nothing here touches the network. ``block_network(monkeypatch)`` makes any
socket connection or DNS lookup to a non-loopback address fail, so a test that
accidentally reaches out fails instead of silently passing.
"""
from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any, Callable


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, *, headers: dict | None = None,
                 lines: list[str] | None = None):
        self.status_code = status
        self.headers = dict(headers or {})
        if lines is not None:
            self.text = "\n".join(lines)
        elif isinstance(body, str):
            self.text = body
        elif body is None:
            self.text = ""
        else:
            self.text = json.dumps(body)

    def json(self) -> Any:
        return json.loads(self.text)

    def iter_lines(self):
        yield from self.text.splitlines()


class FakeTransport:
    """``routes``: ``(method, url_substring, response)``; response may be a FakeResponse, a list
    (consumed in order), an exception (raised) or ``callable(request_dict) -> response``."""

    def __init__(self, *routes: tuple[str, str, Any]):
        self.routes = [list(r) for r in routes]
        self.sent: list[dict] = []

    def request(self, method, url, json=None, headers=None, timeout=None, stream=False):
        req = {"method": method, "url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout,
               "stream": stream}
        self.sent.append(req)
        for route in self.routes:
            m, sub, resp = route
            if m == method and sub in url:
                if isinstance(resp, list):
                    resp = resp.pop(0) if len(resp) > 1 else resp[0]
                if isinstance(resp, BaseException):
                    raise resp
                if callable(resp) and not isinstance(resp, FakeResponse):
                    resp = resp(req)
                return resp
        raise AssertionError(f"unexpected request {method} {url}")

    def posts(self) -> list[dict]:
        return [r for r in self.sent if r["method"] == "POST"]


def sse(*chunks: Any, done: bool = True) -> list[str]:
    """OpenAI-style SSE lines for ``chunks`` (dicts → ``data: <json>``)."""
    lines: list[str] = []
    for c in chunks:
        lines += [f"data: {json.dumps(c)}", ""]
    if done:
        lines += ["data: [DONE]", ""]
    return lines


def anthropic_sse(*events: dict) -> list[str]:
    lines: list[str] = []
    for e in events:
        lines += [f"event: {e['type']}", f"data: {json.dumps(e)}", ""]
    return lines


def _is_loopback(host: Any) -> bool:
    if host in ("localhost", None, ""):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def block_network(monkeypatch) -> None:
    """Fail any connection / DNS lookup that is not loopback."""
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback(host):
            raise OSError(f"network blocked in tests: {host}")
        return real_connect(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            raise socket.gaierror(f"DNS blocked in tests: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def openai_body(content: Any = "hi", *, calls=(), finish: str = "stop", usage=(12, 3)) -> dict:
    return {"choices": [{"finish_reason": finish, "message": {"content": content, "tool_calls": list(calls)}}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


Handler = Callable[[dict], FakeResponse]
