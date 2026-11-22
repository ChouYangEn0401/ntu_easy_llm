"""remote/api_key/llop adapter, driven by a fake ``llmop.sdk`` module (the real SDK is never imported)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
import types

import pytest

from ntu_easy_llm.adapters import FakeAdapter, adapter_status, get_adapter
from ntu_easy_llm.agent import LLMError
from ntu_easy_llm.catalog import find
from ntu_easy_llm.errors import AdapterNotFound, ErrorKind
from ntu_easy_llm.profiles import Profile
from ntu_easy_llm.router import Router
from ntu_easy_llm.types import ImagePart, Message


class SdkError(Exception):
    def __init__(self, code, message="", *, status=None, retry_after=None, kind_hint="unknown"):
        super().__init__(code, message)
        self.code, self.message, self.status, self.retry_after, self.kind_hint = (
            code, message, status, retry_after, kind_hint)


class Entry:
    def __init__(self, id, owned_by="", context_length=None):
        self.id, self.owned_by, self.context_length = id, owned_by, context_length


def completion(text="hello", **extra):
    msg = {"role": "assistant", "content": text, **extra}
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if extra.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2}}


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, name, script=(), models=None):
        self.name = name
        self.script = list(script)
        self.models_result = models if models is not None else [Entry("served-model", "worker", 8192)]
        self.calls: list[tuple[dict, dict]] = []
        self.refreshed = 0
        self.closed = False

    @classmethod
    def from_profile(cls, name="default", *, home=None, http=None):
        c = cls(name, script=FAKE_SCRIPT.pop(name, ()))
        cls.instances.append(c)
        return c

    def chat(self, body, **kw):
        self.calls.append((body, kw))
        step = self.script.pop(0) if self.script else completion()
        if isinstance(step, BaseException):
            raise step
        return step

    def models(self):
        if isinstance(self.models_result, BaseException):
            raise self.models_result
        return self.models_result

    def refresh_link(self):          # must never be called by the adapter
        self.refreshed += 1
        return ""

    def close(self):
        self.closed = True


FAKE_SCRIPT: dict[str, list] = {}


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeClient.instances.clear()
    FAKE_SCRIPT.clear()
    pkg = types.ModuleType("llmop")
    pkg.__spec__ = importlib.machinery.ModuleSpec("llmop", None, is_package=True)
    pkg.__path__ = []
    sdk = types.ModuleType("llmop.sdk")
    sdk.__spec__ = importlib.machinery.ModuleSpec("llmop.sdk", None)
    sdk.Client = FakeClient
    sdk.SdkError = SdkError
    pkg.sdk = sdk
    monkeypatch.setitem(sys.modules, "llmop", pkg)
    monkeypatch.setitem(sys.modules, "llmop.sdk", sdk)
    return sdk


def llop_profile(pid="relay", **opts):
    return Profile(id=pid, source="remote/api_key/llop", model=opts.pop("model", "any"),
                   options={"llop_profile": "home", **opts})


def test_import_does_not_load_llmop():
    code = ("import sys, ntu_easy_llm.adapters.llop, ntu_easy_llm.catalog as c; c.catalog(); "
            "assert 'llmop' not in sys.modules")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_without_extra_check_says_install(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "llmop" else real(n, *a))
    ok, reason = adapter_status("remote/api_key/llop")
    assert not ok and "ntu_easy_llm[llop]" in reason
    with pytest.raises(AdapterNotFound) as ei:
        get_adapter(llop_profile())
    assert "ntu_easy_llm[llop]" in str(ei.value)
    assert "ntu_easy_llm[llop]" in find("remote/api_key/llop").unavailable_reason


def test_with_sdk_source_is_available(fake_sdk):
    assert adapter_status("remote/api_key/llop") == (True, "")
    assert find("remote/api_key/llop").available


def test_chat_builds_openai_body_and_parses_reply(fake_sdk):
    a = get_adapter(llop_profile(transport="e2e", max_tokens=300, timeout=90))
    img = ImagePart("image/png", b"\x89PNG")
    reply = a.chat([{"role": "system", "content": "be brief"},
                    Message(role="user", content="look", images=[img])],
                   tools=[{"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}],
                   temperature=0.2, stop=["END"])
    client = FakeClient.instances[0]
    assert client.name == "home"
    body, kw = client.calls[0]
    assert kw == {"model": None, "max_tokens": 300, "transport": "e2e", "deadline": 90.0}
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "look"} and parts[1]["image_url"]["url"].startswith("data:image/png")
    assert body["tools"][0]["function"]["name"] == "f" and body["temperature"] == 0.2 and body["stop"] == ["END"]
    assert reply.text == "hello" and reply.finish == "stop"
    assert reply.usage.input_tokens == 7 and reply.usage.output_tokens == 2


def test_chat_tool_calls_and_explicit_model(fake_sdk):
    FAKE_SCRIPT["home"] = [completion("", tool_calls=[{"id": "c1", "type": "function",
                                                       "function": {"name": "f", "arguments": '{"x": 1}'}}])]
    a = get_adapter(llop_profile(model="served-model"))
    reply = a.chat([{"role": "user", "content": "hi"}])
    assert reply.tool_calls[0].name == "f" and reply.tool_calls[0].arguments == {"x": 1}
    assert reply.finish == "tool_calls"
    assert FakeClient.instances[0].calls[0][1] == {"model": "served-model"}


@pytest.mark.parametrize("hint,kind", [
    ("quota", ErrorKind.QUOTA_EXHAUSTED), ("auth", ErrorKind.AUTH_INVALID), ("rate", ErrorKind.RATE_LIMITED),
    ("offline", ErrorKind.CONNECTION), ("timeout", ErrorKind.TIMEOUT), ("trust", ErrorKind.TRUST),
    ("bad_request", ErrorKind.BAD_REQUEST), ("server", ErrorKind.SERVER_ERROR), ("unknown", ErrorKind.UNKNOWN),
    ("link_changed", ErrorKind.LINK_CHANGED),
])
def test_kind_hint_mapping(fake_sdk, hint, kind):
    FAKE_SCRIPT["home"] = [SdkError("x", "something", kind_hint=hint, status=None, retry_after=3)]
    a = get_adapter(llop_profile())
    with pytest.raises(LLMError) as ei:
        a.chat([{"role": "user", "content": "hi"}])
    assert ei.value.kind is kind and ei.value.profile_id == "relay" and ei.value.retry_after == 3


def test_refined_kinds(fake_sdk):
    FAKE_SCRIPT["home"] = [SdkError("queue_full", "busy", status=503, kind_hint="server"),
                           SdkError("context_length_exceeded", "too long", status=400, kind_hint="bad_request"),
                           SdkError("insufficient_quota", "out", status=429, retry_after=60, kind_hint="quota")]
    a = get_adapter(llop_profile())
    kinds = []
    for _ in range(3):
        with pytest.raises(LLMError) as ei:
            a.chat([{"role": "user", "content": "hi"}])
        kinds.append((ei.value.kind, ei.value.retry_after))
    assert kinds == [(ErrorKind.OVERLOADED, None), (ErrorKind.CONTEXT_TOO_LONG, None),
                     (ErrorKind.QUOTA_EXHAUSTED, 60.0)]


def test_link_changed_is_raised_once_without_refresh(fake_sdk):
    FAKE_SCRIPT["home"] = [SdkError("link_changed", "API 連結已更換", status=404, kind_hint="link_changed"),
                           completion("should not be reached")]
    a = get_adapter(llop_profile())
    r = Router([a, FakeAdapter(["backup"], profile=Profile("b", "local/fake"))], waterfall={"mode": "auto"},
               sleep=lambda s: None)
    with pytest.raises(LLMError) as ei:
        r.chat([{"role": "user", "content": "hi"}])
    client = FakeClient.instances[0]
    assert ei.value.kind is ErrorKind.LINK_CHANGED and "set-link --profile home" in str(ei.value)
    assert len(client.calls) == 1 and client.refreshed == 0


def test_quota_falls_back_in_router(fake_sdk):
    FAKE_SCRIPT["home"] = [SdkError("insufficient_quota", "", status=429, kind_hint="quota")]
    r = Router([get_adapter(llop_profile()), FakeAdapter(["backup"], profile=Profile("b", "local/fake"))],
               waterfall={"mode": "auto"})
    res = r.chat([{"role": "user", "content": "hi"}])
    assert res.profile_id == "b" and res.switched


def test_list_models_and_probe(fake_sdk):
    a = get_adapter(llop_profile())
    models = a.list_models()
    assert models[0].id == "served-model" and models[0].context == 8192
    assert a.probe().ok
    assert a.probe(send=True).ok
    a.close()
    assert FakeClient.instances[0].closed


def test_probe_never_raises(fake_sdk):
    a = get_adapter(llop_profile())
    a._get_client().models_result = SdkError("network_error", "down", kind_hint="offline")
    p = a.probe()
    assert not p.ok and p.kind is ErrorKind.CONNECTION


def test_bad_sdk_profile_is_an_llm_error(fake_sdk, monkeypatch):
    def boom(cls, name="default", **kw):
        raise SdkError("profile_not_found", "no such profile", kind_hint="bad_request")
    monkeypatch.setattr(FakeClient, "from_profile", classmethod(boom))
    a = get_adapter(llop_profile())
    with pytest.raises(LLMError) as ei:
        a.chat([{"role": "user", "content": "hi"}])
    assert ei.value.kind is ErrorKind.BAD_REQUEST
