"""0.6 OpenAI-style HTTP adapters (openai, gemini, openai_compatible, lmstudio, vllm, llamacpp). Offline:
a scripted transport stands in for the server and real network access is blocked."""
from __future__ import annotations

import json

import pytest

from _fakehttp import FakeResponse, FakeTransport, block_network, openai_body, sse
from ntu_easy_llm.adapters import Adapter, get_adapter
from ntu_easy_llm.adapters._http import UrllibTransport
from ntu_easy_llm.adapters.openai_style import (GeminiAdapter, LlamaCppAdapter, LMStudioAdapter,
                                                LocalOpenAICompatibleAdapter, OpenAIAdapter,
                                                OpenAICompatibleAdapter, VLLMAdapter)
from ntu_easy_llm.agent import LLMConnectionError, LLMError, LLMHTTPError, LLMResponseError, Tool, ToolCall
from ntu_easy_llm.errors import ConfigError, ErrorKind
from ntu_easy_llm.profiles import Profile, load
from ntu_easy_llm.types import ImagePart, Message

USER = [{"role": "user", "content": "hi"}]
ENV = {"EXAMPLE_KEY": "example-secret-value"}
TOOL = Tool("add", "add a number", {"type": "object", "properties": {"n": {"type": "number"}}})
PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    block_network(monkeypatch)


def make(source: str, http, *, model="example-model", base_url=None, key=True, environ=ENV, **options):
    p = Profile("p1", source, model=model, base_url=base_url, secret="env:EXAMPLE_KEY" if key else None,
                options=options)
    return get_adapter(p, http=http, environ=environ)


# --------------------------------------------------------------------------- #
# registry / construction
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source,cls", [
    ("remote/api_key/openai", OpenAIAdapter), ("remote/api_key/gemini", GeminiAdapter),
    ("local/lmstudio", LMStudioAdapter), ("local/vllm", VLLMAdapter), ("local/llamacpp", LlamaCppAdapter),
])
def test_builtin_sources_build_the_right_adapter(source, cls):
    a = make(source, FakeTransport())
    assert type(a) is cls and isinstance(a, Adapter) and a.profile.id == "p1"


def test_openai_compatible_needs_base_url_and_remote_keys_are_required():
    with pytest.raises(ConfigError, match="base_url"):
        make("local/openai_compatible", FakeTransport())
    with pytest.raises(ConfigError, match="base_url"):
        make("remote/api_key/openai_compatible", FakeTransport())
    with pytest.raises(ConfigError, match="api_key_env or secret"):
        make("remote/api_key/openai", FakeTransport(), key=False)
    a = make("remote/api_key/openai_compatible", FakeTransport(), base_url="https://gateway.example.invalid/v1",
             key=False)                                    # gateways may need no key
    assert isinstance(a, OpenAICompatibleAdapter)
    assert isinstance(make("local/openai_compatible", FakeTransport(), base_url="http://127.0.0.1:9/v1", key=False),
                      LocalOpenAICompatibleAdapter)


def test_profile_from_config_file_shape():
    ps = load({"profiles": {"lms": {"source": "local/lmstudio", "model": "example-model",
                                    "options": {"context_size": 4096, "tools": False}}}}, environ={})
    a = ps.adapter("lms", http=FakeTransport())
    assert a.base_url == "http://127.0.0.1:1234/v1" and a.capabilities().tools is False


# --------------------------------------------------------------------------- #
# chat: request shape and reply
# --------------------------------------------------------------------------- #

def test_openai_chat_request_and_reply():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, openai_body("hello"))))
    a = make("remote/api_key/openai", t, max_tokens=50, temperature=0.2)
    r = a.chat([{"role": "system", "content": "sys"}, *USER], stop=["\n\n"])
    req = t.sent[0]
    assert req["url"].endswith("/chat/completions") and req["url"].startswith("https://")
    assert req["headers"]["Authorization"] == "Bearer example-secret-value"
    body = req["json"]
    assert body["model"] == "example-model" and body["max_completion_tokens"] == 50
    assert body["temperature"] == 0.2 and body["stop"] == ["\n\n"] and "stream" not in body
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert r.text == "hello" and r.finish == "stop" and r.usage.input_tokens == 12 and r.warnings == []


def test_call_arguments_override_profile_options_and_timeout_default():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, openai_body("x"))))
    a = make("local/vllm", t, key=False, max_tokens=50, timeout=12)
    a.chat(USER, max_tokens=7, temperature=0.0, timeout=3)
    assert t.sent[0]["json"]["max_tokens"] == 7 and t.sent[0]["json"]["temperature"] == 0.0
    assert t.sent[0]["timeout"] == 3
    a.chat(USER)
    assert t.sent[1]["json"]["max_tokens"] == 50 and t.sent[1]["timeout"] == 12
    b = make("local/vllm", t, key=False)
    b.chat(USER)
    assert t.sent[2]["timeout"] == 300.0 and "max_tokens" not in t.sent[2]["json"]
    assert "Authorization" not in t.sent[2]["headers"]


def test_tools_and_tool_call_reply():
    calls = [{"id": "c1", "type": "function", "function": {"name": "add", "arguments": '{"n": 2}'}}]
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, openai_body(None, calls=calls,
                                                                                  finish="tool_calls"))))
    a = make("remote/api_key/openai", t)
    history = [*USER, {"role": "assistant", "content": "", "tool_calls": [ToolCall("c0", "add", {"n": 1})]},
               {"role": "tool", "tool_call_id": "c0", "name": "add", "content": "1"}]
    r = a.chat(history, tools=[TOOL])
    body = t.sent[0]["json"]
    assert body["tools"][0]["function"]["name"] == "add"
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"n": 1}'
    assert body["messages"][2] == {"role": "tool", "tool_call_id": "c0", "content": "1"}
    assert r.finish == "tool_calls" and r.tool_calls[0].arguments == {"n": 2} and r.text == ""


def test_images_become_data_url_parts_and_content_parts_are_accepted():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, openai_body("a cat"))))
    a = make("remote/api_key/openai", t)
    a.chat([Message("user", "what is this?", images=[ImagePart("image/png", PNG)])])
    parts = t.sent[0]["json"]["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    # OpenAI-style content parts in a plain dict are split and re-sent the same way
    a.chat([{"role": "user", "content": [{"type": "text", "text": "and this?"},
                                         {"type": "image_url", "image_url": {"url": parts[1]["image_url"]["url"]}}]}])
    assert t.sent[1]["json"]["messages"][0]["content"] == parts[:0] + [{"type": "text", "text": "and this?"},
                                                                         parts[1]]


def test_capability_options_block_tools_and_images_before_sending():
    t = FakeTransport()
    a = make("local/lmstudio", t, key=False, tools=False, vision=False)
    with pytest.raises(LLMError) as ei:
        a.chat(USER, tools=[TOOL])
    assert ei.value.kind is ErrorKind.TOOLS_UNSUPPORTED and ei.value.profile_id == "p1"
    with pytest.raises(LLMError) as ei:
        a.chat([Message("user", "x", images=[ImagePart("image/png", PNG)])])
    assert ei.value.kind is ErrorKind.BAD_REQUEST
    assert t.sent == []


def test_bad_message_is_bad_request_and_missing_model():
    a = make("local/vllm", FakeTransport(), key=False)
    with pytest.raises(LLMError) as ei:
        a.chat([{"role": "robot", "content": "x"}])
    assert ei.value.kind is ErrorKind.BAD_REQUEST
    b = make("local/vllm", FakeTransport(), key=False, model="")
    with pytest.raises(LLMError) as ei:
        b.chat(USER)
    assert ei.value.kind is ErrorKind.BAD_REQUEST


def test_finish_length_and_thinking():
    t = FakeTransport(("POST", "/chat/completions", [FakeResponse(200, openai_body("partial", finish="length")),
                                                      FakeResponse(200, openai_body("", finish="length"))]))
    a = make("local/llamacpp", t, key=False)
    assert a.chat(USER).finish == "length"
    assert a.chat(USER).finish == "thinking"


def test_extra_body_is_merged():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, openai_body("x"))))
    a = make("remote/api_key/gemini", t, extra_body={"reasoning_effort": "low"})
    a.chat(USER)
    assert t.sent[0]["json"]["reasoning_effort"] == "low"


def test_post_only_session_from_05_still_works():
    class Session:
        def __init__(self):
            self.sent = []

        def post(self, url, json=None, headers=None, timeout=None):
            self.sent.append(url)
            return FakeResponse(200, openai_body("ok"))
    s = Session()
    assert make("remote/api_key/openai", s).chat(USER).text == "ok" and s.sent[0].endswith("/chat/completions")


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #

def test_stream_text_tool_calls_and_usage():
    lines = sse({"choices": [{"delta": {"role": "assistant", "content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                                        "function": {"name": "add", "arguments": '{"n"'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ': 3}'}}]}}]},
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}})
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, lines=lines)))
    a = make("remote/api_key/openai", t)
    pieces: list[str] = []
    r = a.chat(USER, tools=[TOOL], on_delta=pieces.append)
    body = t.sent[0]
    assert body["stream"] is True and body["json"]["stream"] is True
    assert body["json"]["stream_options"] == {"include_usage": True}
    assert pieces == ["Hel", "lo"] and r.text == "Hello"
    assert r.tool_calls == [ToolCall("c1", "add", {"n": 3})] and r.finish == "tool_calls"
    assert (r.usage.input_tokens, r.usage.output_tokens) == (5, 9)


def test_stream_options_only_where_known_to_work():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, lines=sse({"choices": [{"delta": {
        "content": "x"}, "finish_reason": "stop"}]}))))
    make("local/lmstudio", t, key=False).chat(USER, on_delta=lambda s: None)
    assert "stream_options" not in t.sent[0]["json"]


def test_server_that_ignores_stream_still_works():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, openai_body("whole reply"))))
    pieces: list[str] = []
    r = make("local/llamacpp", t, key=False).chat(USER, on_delta=pieces.append)
    assert r.text == "whole reply" and pieces == ["whole reply"] and r.finish == "stop"


def test_error_event_in_stream_is_classified():
    lines = sse({"choices": [{"delta": {"content": "a"}}]},
                {"error": {"message": "Context size has been exceeded.", "code": 400}}, done=False)
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, lines=lines)))
    with pytest.raises(LLMHTTPError) as ei:
        make("local/lmstudio", t, key=False).chat(USER, on_delta=lambda s: None)
    assert ei.value.kind is ErrorKind.CONTEXT_TOO_LONG and ei.value.profile_id == "p1"


def test_garbage_stream_is_bad_response():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, lines=["data: {not json", ""])))
    with pytest.raises(LLMResponseError) as ei:
        make("local/vllm", t, key=False).chat(USER, on_delta=lambda s: None)
    assert ei.value.kind is ErrorKind.BAD_RESPONSE and ei.value.profile_id == "p1"


# --------------------------------------------------------------------------- #
# errors
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source,status,body,headers,tools,kind,wait", [
    ("remote/api_key/openai", 429, {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}, {},
     False, ErrorKind.QUOTA_EXHAUSTED, None),
    ("remote/api_key/openai", 429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "7"}, False,
     ErrorKind.RATE_LIMITED, 7.0),
    ("remote/api_key/openai", 401, {"error": {"code": "invalid_api_key"}}, {}, False, ErrorKind.AUTH_INVALID, None),
    ("remote/api_key/openai", 403, {"error": {"code": "unsupported_country_region_territory"}}, {}, False,
     ErrorKind.PERMISSION, None),
    ("remote/api_key/openai", 404, {"error": {"code": "model_not_found"}}, {}, False, ErrorKind.MODEL_NOT_FOUND,
     None),
    ("remote/api_key/openai", 400, {"error": {"message": "tools is not supported in this model"}}, {}, True,
     ErrorKind.TOOLS_UNSUPPORTED, None),
    ("remote/api_key/gemini", 400, [{"error": {"code": 400, "message": "API key not valid. Please pass a valid "
                                                                        "API key.", "status": "INVALID_ARGUMENT",
                                               "details": [{"reason": "API_KEY_INVALID"}]}}], {}, False,
     ErrorKind.AUTH_INVALID, None),
    ("local/lmstudio", 400, {"error": "Context size has been exceeded."}, {}, False, ErrorKind.CONTEXT_TOO_LONG,
     None),
    ("local/lmstudio", 404, {"error": {"message": "model 'x' not found", "code": "model_not_found"}}, {}, False,
     ErrorKind.MODEL_NOT_FOUND, None),
    ("local/vllm", 400, {"message": "This model's maximum context length is 4096 tokens. However, you requested "
                                    "5000 tokens."}, {}, False, ErrorKind.CONTEXT_TOO_LONG, None),
    ("local/vllm", 400, {"message": '"auto" tool choice requires --enable-auto-tool-choice'}, {}, True,
     ErrorKind.TOOLS_UNSUPPORTED, None),
    ("local/llamacpp", 400, {"error": {"type": "exceed_context_size_error",
                                       "message": "the request exceeds the available context size"}}, {}, False,
     ErrorKind.CONTEXT_TOO_LONG, None),
    ("local/llamacpp", 500, "", {}, False, ErrorKind.SERVER_ERROR, None),
])
def test_http_errors_are_classified(source, status, body, headers, tools, kind, wait):
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(status, body, headers=headers)))
    a = make(source, t, key=source.startswith("remote"))
    with pytest.raises(LLMHTTPError) as ei:
        a.chat(USER, tools=[TOOL] if tools else None)
    e = ei.value
    assert e.kind is kind and e.status == status and e.profile_id == "p1" and e.retry_after == wait
    assert "example-secret-value" not in str(e)


@pytest.mark.parametrize("exc,kind", [(ConnectionRefusedError("refused"), ErrorKind.CONNECTION),
                                      (TimeoutError("timed out"), ErrorKind.TIMEOUT),
                                      (OSError("network unreachable"), ErrorKind.CONNECTION)])
def test_connection_errors(exc, kind):
    t = FakeTransport(("POST", "/chat/completions", exc))
    with pytest.raises(LLMConnectionError) as ei:
        make("local/vllm", t, key=False).chat(USER)
    assert ei.value.kind is kind and ei.value.profile_id == "p1"


def test_non_json_reply_is_bad_response():
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, "<html>proxy page</html>")))
    with pytest.raises(LLMResponseError) as ei:
        make("remote/api_key/openai", t).chat(USER)
    assert ei.value.kind is ErrorKind.BAD_RESPONSE and ei.value.profile_id == "p1"
    t = FakeTransport(("POST", "/chat/completions", FakeResponse(200, {"unexpected": True})))
    with pytest.raises(LLMResponseError) as ei:
        make("remote/api_key/openai", t).chat(USER)
    assert ei.value.profile_id == "p1"


def test_missing_key_is_auth_invalid_and_never_echoed():
    t = FakeTransport()
    a = make("remote/api_key/openai", t, environ={})        # built fine: the key is read only when needed
    with pytest.raises(LLMError) as ei:
        a.chat(USER)
    assert ei.value.kind is ErrorKind.AUTH_INVALID and "EXAMPLE_KEY" in str(ei.value) and t.sent == []
    res = a.probe()
    assert not res.ok and res.kind is ErrorKind.AUTH_INVALID


# --------------------------------------------------------------------------- #
# models / probe
# --------------------------------------------------------------------------- #

def test_openai_models_and_kinds():
    data = {"data": [{"id": "example-chat"}, {"id": "text-embedding-x"}, {"id": "whisper-x"}]}
    t = FakeTransport(("GET", "/models", FakeResponse(200, data)))
    ms = make("remote/api_key/openai", t).list_models()
    assert [(m.id, m.kind) for m in ms] == [("example-chat", "chat"), ("text-embedding-x", "embedding"),
                                            ("whisper-x", "unknown")]
    assert t.sent[0]["headers"]["Authorization"].startswith("Bearer ")


def test_gemini_models_strip_prefix_and_probe_matches():
    data = {"object": "list", "data": [{"id": "models/example-model", "object": "model"}]}
    t = FakeTransport(("GET", "/models", FakeResponse(200, data)))
    a = make("remote/api_key/gemini", t)
    assert [m.id for m in a.list_models()] == ["example-model"]
    assert a.probe().ok


def test_lmstudio_models_from_rest_api_with_loaded_flags():
    v1 = {"models": [{"type": "llm", "key": "example-model", "display_name": "Example", "max_context_length": 32768,
                      "loaded_instances": [{"id": "example-model"}],
                      "capabilities": {"vision": False, "trained_for_tool_use": True}},
                     {"type": "embedding", "key": "example-embed", "loaded_instances": []}]}
    t = FakeTransport(("GET", "/api/v1/models", FakeResponse(200, v1)))
    ms = make("local/lmstudio", t, key=False).list_models()
    assert ms[0].id == "example-model" and ms[0].loaded is True and ms[0].context == 32768
    assert ms[0].tools is True and ms[0].vision is False and ms[0].kind == "chat"
    assert ms[1].kind == "embedding" and ms[1].loaded is False
    assert t.sent[0]["url"] == "http://127.0.0.1:1234/api/v1/models"


def test_lmstudio_falls_back_to_v0_then_v1_models():
    v0 = {"object": "list", "data": [{"id": "example-model", "type": "vlm", "state": "not-loaded",
                                      "max_context_length": 8192}]}
    t = FakeTransport(("GET", "/api/v1/models", FakeResponse(404, "")), ("GET", "/api/v0/models",
                                                                         FakeResponse(200, v0)))
    ms = make("local/lmstudio", t, key=False).list_models()
    assert ms[0].loaded is False and ms[0].vision is True and ms[0].context == 8192
    t = FakeTransport(("GET", "/api/v1/models", FakeResponse(200, {"error": "Unexpected endpoint or method."})),
                      ("GET", "/api/v0/models", FakeResponse(404, "")),
                      ("GET", "/v1/models", FakeResponse(200, {"data": [{"id": "example-model"}]})))
    ms = make("local/lmstudio", t, key=False).list_models()
    assert [m.id for m in ms] == ["example-model"]


def test_vllm_models_carry_context_and_loaded():
    t = FakeTransport(("GET", "/models", FakeResponse(200, {"data": [{"id": "example-model",
                                                                      "max_model_len": 16384}]})))
    m = make("local/vllm", t, key=False).list_models()[0]
    assert m.context == 16384 and m.loaded is True


def test_probe_without_send_only_reads():
    t = FakeTransport(("GET", "/api/v1/models", FakeResponse(200, {"models": [{"key": "example-model",
                                                                              "loaded_instances": []}]})))
    a = make("local/lmstudio", t, key=False)
    res = a.probe()
    assert res.ok and res.models[0].loaded is False and res.latency_s is not None
    assert t.posts() == []                                   # nothing sent to the model, nothing loaded


def test_probe_model_missing_strict_and_lenient():
    t = FakeTransport(("GET", "/models", FakeResponse(200, {"data": [{"id": "other"}]})))
    res = make("remote/api_key/openai", t).probe()
    assert not res.ok and res.kind is ErrorKind.MODEL_NOT_FOUND
    res = make("remote/api_key/openai_compatible", t, base_url="https://gateway.example.invalid/v1").probe()
    assert res.ok and "not in the server's model list" in res.reason


def test_probe_send_and_failures():
    t = FakeTransport(("GET", "/models", FakeResponse(200, {"data": [{"id": "example-model"}]})),
                      ("POST", "/chat/completions", [FakeResponse(200, openai_body("ok")),
                                                     FakeResponse(200, openai_body("", finish="length")),
                                                     FakeResponse(429, {"error": {"code": "insufficient_quota"}})]))
    a = make("remote/api_key/openai", t)
    assert a.probe(send=True).ok and t.posts()[0]["json"]["max_completion_tokens"] == 256
    res = a.probe(send=True)
    assert not res.ok and res.kind is ErrorKind.BAD_RESPONSE and "thinking" in res.reason
    res = a.probe(send=True)
    assert not res.ok and res.kind is ErrorKind.QUOTA_EXHAUSTED


def test_probe_never_raises():
    t = FakeTransport(("GET", "/models", ConnectionRefusedError("refused")))
    res = make("local/vllm", t, key=False).probe()
    assert not res.ok and res.kind is ErrorKind.CONNECTION
    t = FakeTransport(("GET", "/models", FakeResponse(404, "")),
                      ("POST", "/chat/completions", FakeResponse(200, openai_body("ok"))))
    a = make("local/openai_compatible", t, base_url="http://127.0.0.1:9/v1", key=False)
    res = a.probe()
    assert res.ok and "does not list models" in res.reason
    assert a.probe(send=True).ok


# --------------------------------------------------------------------------- #
# proxy choice
# --------------------------------------------------------------------------- #

def test_local_and_lan_go_direct_remote_uses_system_proxy():
    assert make("local/lmstudio", None, key=False).direct is True
    assert make("local/openai_compatible", None, base_url="http://my-nas:8000/v1", key=False).direct is True
    assert make("remote/api_key/openai", None).direct is False
    assert make("remote/api_key/openai_compatible", None, base_url="http://192.168.1.20:8000/v1",
                key=False).direct is True
    assert make("remote/api_key/openai_compatible", None, base_url="https://gateway.example.invalid/v1",
                key=False).direct is False
    a = make("remote/api_key/openai", None, proxy="none")
    assert a.direct is True and isinstance(a.http, UrllibTransport) and a.http.direct is True
    assert make("local/vllm", None, key=False, proxy="system").direct is False
    with pytest.raises(ConfigError):
        make("local/vllm", None, key=False, proxy="sometimes")


def test_repr_has_no_secret():
    a = make("remote/api_key/openai", FakeTransport())
    assert "example-secret-value" not in repr(a) and "p1" in repr(a)
    assert json.dumps(a.profile.to_dict()).count("example-secret-value") == 0
