"""Offline tests for the agent API (ntu_easy_llm.agent). No network: a fake
requests-style session records what would have been sent."""
from __future__ import annotations

import json

import pytest

import ntu_easy_llm as L
from ntu_easy_llm import agent
from ntu_easy_llm.agent import (ChatReply, LLMConnectionError, LLMHTTPError, LLMResponseError, Tool, ToolCall,
                                chat, probe)

TOOL = Tool("money_add", "Record an expense",
            {"type": "object", "properties": {"amount": {"type": "string"}}, "required": ["amount"]})


class Resp:
    def __init__(self, data, status=200, text=None):
        self._data, self.status_code = data, status
        self.text = text if text is not None else json.dumps(data)

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class Session:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.sent = resp, exc, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.resp


def openai_reply(content=None, calls=(), finish="stop"):
    return Resp({"choices": [{"finish_reason": finish, "message": {"content": content, "tool_calls": list(calls)}}],
                 "usage": {"prompt_tokens": 12, "completion_tokens": 3}})


HISTORY = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "add 80"},
    {"role": "assistant", "content": "", "tool_calls": [ToolCall("c0", "money_add", {"amount": "1"})]},
    {"role": "tool", "tool_call_id": "c0", "name": "money_add", "content": "ok"},
]


# --------------------------------------------------------------------------- #
# package surface / compatibility
# --------------------------------------------------------------------------- #

def test_version_bumped_and_agent_reachable_lazily():
    assert L.__version__ == "0.5.0"
    assert L.agent is agent
    # the old public surface is untouched
    for name in ("ask", "ask_chatgpt", "ask_gemini", "ask_anthropic", "LLMSession", "KeyMaterial"):
        assert name in L.__all__


def test_importing_agent_does_not_load_provider_sdks():
    import subprocess
    import sys
    code = ("import sys, ntu_easy_llm.agent as a; "
            "print(any(m in sys.modules for m in ('openai', 'anthropic', 'google.genai')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_normalize_provider_aliases():
    assert agent.normalize_provider("CHATGPT") == "openai"
    assert agent.normalize_provider("ollama") == "openai_compatible"
    assert agent.normalize_provider("Claude") == "anthropic"
    with pytest.raises(ValueError):
        agent.normalize_provider("nope")


# --------------------------------------------------------------------------- #
# OpenAI-style (openai / gemini / openai_compatible)
# --------------------------------------------------------------------------- #

def test_openai_tool_call_round_trip():
    s = Session(openai_reply(calls=[{"id": "c1", "type": "function",
                                     "function": {"name": "money_add", "arguments": "{\"amount\": \"80\"}"}}],
                             finish="tool_calls"))
    reply = chat("openai", "gpt-x", HISTORY, tools=[TOOL], api_key="k", session=s, max_tokens=50)
    assert isinstance(reply, ChatReply)
    assert reply.tool_calls == [ToolCall("c1", "money_add", {"amount": "80"})]
    assert reply.usage.as_dict() == {"input_tokens": 12, "output_tokens": 3}
    assert reply.stop_reason == "tool_calls" and reply.raw["choices"]
    sent = s.sent[0]
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer k"
    body = sent["json"]
    assert body["max_completion_tokens"] == 50 and "max_tokens" not in body
    assert body["tools"][0] == {"type": "function", "function": {
        "name": "money_add", "description": "Record an expense", "parameters": TOOL.parameters}}
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"amount": "1"}'
    assert body["messages"][2]["content"] is None
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "c0", "content": "ok"}


def test_openai_compatible_needs_base_url_and_uses_max_tokens():
    with pytest.raises(ValueError):
        chat("openai_compatible", "m", [{"role": "user", "content": "hi"}], session=Session(openai_reply("x")))
    s = Session(openai_reply("好"))
    reply = chat("ollama", "llama3", [{"role": "user", "content": "hi"}], base_url="http://localhost:11434/v1/",
                 session=s, max_tokens=99)
    assert reply.text == "好" and reply.tool_calls == []
    assert s.sent[0]["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in s.sent[0]["headers"]             # empty key → no header
    assert s.sent[0]["json"]["max_tokens"] == 99 and "tools" not in s.sent[0]["json"]


def test_gemini_default_endpoint_and_dict_tools():
    s = Session(openai_reply("hi"))
    chat("gemini", "gemini-2.5-flash", [{"role": "user", "content": "hi"}],
         tools=[{"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}],
         api_key="g", session=s)
    assert s.sent[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert s.sent[0]["json"]["tools"][0]["function"]["name"] == "t"


def test_bad_tool_arguments_are_kept_raw():
    s = Session(openai_reply(calls=[{"id": "", "function": {"name": "x", "arguments": "{not json"}}]))
    reply = chat("openai", "m", [{"role": "user", "content": "hi"}], session=s)
    assert reply.tool_calls[0].arguments == {"_raw": "{not json"} and reply.tool_calls[0].id == "call_0"


def test_extra_headers_and_body_are_merged():
    s = Session(openai_reply("x"))
    chat("openai_compatible", "m", [{"role": "user", "content": "hi"}], base_url="https://gw.example/v1",
         session=s, extra_headers={"X-Team": "a"}, extra_body={"user": "u1"}, temperature=0.2)
    assert s.sent[0]["headers"]["X-Team"] == "a"
    assert s.sent[0]["json"]["user"] == "u1" and s.sent[0]["json"]["temperature"] == 0.2


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #

def test_anthropic_wire_and_parse():
    s = Session(Resp({"content": [{"type": "text", "text": "I'll add it"},
                                  {"type": "tool_use", "id": "t1", "name": "money_add", "input": {"amount": "8"}}],
                      "stop_reason": "tool_use", "usage": {"input_tokens": 20, "output_tokens": 4}}))
    history = HISTORY[:2] + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x1", "name": "a", "arguments": {}},
                                                           {"id": "x2", "name": "b", "arguments": "{}"}]},
        {"role": "tool", "content": "r1", "tool_call_id": "x1"},
        {"role": "tool", "content": "r2", "tool_call_id": "x2"},
    ]
    reply = chat("anthropic", "claude-x", history, tools=[TOOL], api_key="k", session=s)
    assert reply.text == "I'll add it" and reply.stop_reason == "tool_use"
    assert reply.tool_calls == [ToolCall("t1", "money_add", {"amount": "8"})]
    assert reply.usage.input_tokens == 20
    sent = s.sent[0]
    assert sent["url"] == "https://api.anthropic.com/v1/messages"
    assert sent["headers"]["x-api-key"] == "k" and sent["headers"]["anthropic-version"]
    body = sent["json"]
    assert body["system"] == "sys" and body["max_tokens"] == 1024
    assert body["tools"][0]["input_schema"] == TOOL.parameters
    assert body["messages"][1]["content"][0] == {"type": "tool_use", "id": "x1", "name": "a", "input": {}}
    assert [b["tool_use_id"] for b in body["messages"][2]["content"]] == ["x1", "x2"]   # merged into one user msg


def test_message_objects_with_attributes_are_accepted():
    class M:
        def __init__(self, role, content):
            self.role, self.content, self.tool_calls, self.tool_call_id = role, content, [], ""
    system, wire = agent.to_anthropic_messages([M("system", "s"), M("user", "u")])
    assert system == "s" and wire == [{"role": "user", "content": "u"}]


# --------------------------------------------------------------------------- #
# errors & probe
# --------------------------------------------------------------------------- #

def test_http_error_carries_status_and_body():
    s = Session(Resp({"error": {"message": "bad key"}}, status=401))
    with pytest.raises(LLMHTTPError) as e:
        chat("openai", "m", [{"role": "user", "content": "hi"}], api_key="k", session=s)
    assert e.value.status == 401 and "bad key" in e.value.body and e.value.provider == "openai"


def test_connection_and_shape_errors():
    with pytest.raises(LLMConnectionError):
        chat("openai", "m", [{"role": "user", "content": "hi"}], session=Session(exc=OSError("refused")))
    with pytest.raises(LLMResponseError):
        chat("openai", "m", [{"role": "user", "content": "hi"}], session=Session(Resp({"nope": 1})))
    with pytest.raises(LLMResponseError):
        chat("anthropic", "m", [{"role": "user", "content": "hi"}], session=Session(Resp(ValueError("x"), text="<")))
    with pytest.raises(ValueError):
        chat("openai", "", [{"role": "user", "content": "hi"}], session=Session(openai_reply("x")))


def test_probe():
    assert probe("openai", "m", api_key="k", session=Session(openai_reply("ok"))) is None
    assert "401" in probe("openai", "m", api_key="k", session=Session(Resp({}, status=401)))
    assert "empty" in probe("openai", "m", session=Session(openai_reply("")))
    assert "base_url" in probe("openai_compatible", "m", session=Session(openai_reply("ok")))


def test_default_session_uses_urllib(monkeypatch):
    seen = {}

    class FakeHTTP:
        status = 200

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "hey"}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["body"], seen["timeout"] = req.full_url, json.loads(req.data), timeout
        seen["auth"] = req.get_header("Authorization")
        return FakeHTTP()

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)
    reply = chat("openai_compatible", "m", [{"role": "user", "content": "嗨"}], api_key="k",
                 base_url="https://gw.example/v1", timeout=7)
    assert reply.text == "hey" and seen["url"] == "https://gw.example/v1/chat/completions"
    assert seen["body"]["messages"][0]["content"] == "嗨" and seen["timeout"] == 7 and seen["auth"] == "Bearer k"
