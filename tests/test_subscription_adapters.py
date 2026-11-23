"""remote/subscription/* adapters, driven by fake login packages (no real CLI is ever called)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
import types

import pytest

from ntu_easy_llm.adapters import FakeAdapter, adapter_status, get_adapter
from ntu_easy_llm.adapters.subscription import EXTRAS, PACKAGES, TOOLS_UNSUPPORTED_MESSAGE
from ntu_easy_llm.agent import LLMError
from ntu_easy_llm.catalog import find
from ntu_easy_llm.errors import AdapterNotFound, ErrorKind
from ntu_easy_llm.profiles import Profile
from ntu_easy_llm.router import Router
from ntu_easy_llm.types import ImagePart, Message

PROVIDERS = ("claude", "github_copilot", "chatgpt")


def make_login_module(name: str, *, found=True):
    mod = types.ModuleType(name)
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    mod.INTERFACE_VERSION = "1"
    mod.CAPABILITIES = {"interface": "1", "text": True, "tools": False, "images": False, "usage": True,
                        "default_model": "default-x"}

    class LoginError(Exception):
        kind = "unknown"

    class UsageLimitError(LoginError):
        kind = "quota_exhausted"

        def __init__(self, msg="limit", resets="later", retry_after=None):
            super().__init__(msg)
            self.resets, self.retry_after = resets, retry_after

    class AuthError(LoginError):
        kind = "auth_invalid"

    class NotInstalledError(LoginError):
        kind = "not_installed"

    class CallTimeoutError(LoginError):
        kind = "timeout"

    class PromptTooLongError(LoginError, ValueError):
        kind = "bad_request"

    class Result:
        def __init__(self, text, model=""):
            self.text, self.model, self.usage, self.raw = text, model, {"input": 5, "output": 3}, {"r": 1}

    mod.LoginError, mod.UsageLimitError, mod.AuthError = LoginError, UsageLimitError, AuthError
    mod.NotInstalledError, mod.CallTimeoutError, mod.PromptTooLongError = NotInstalledError, CallTimeoutError, \
        PromptTooLongError
    mod.calls = []
    mod.script = []

    def ask(prompt, *, model=None, system=None, timeout=180, **opts):
        mod.calls.append({"prompt": prompt, "model": model, "system": system, "timeout": timeout, "opts": opts})
        step = mod.script.pop(0) if mod.script else "answer"
        if isinstance(step, BaseException):
            raise step
        return Result(step, model or "")

    mod.ask = ask
    finder = PACKAGES[{v[0]: k for k, v in PACKAGES.items()}[name]][2]
    setattr(mod, finder, (lambda: "/fake/cli") if found else (lambda: None))
    return mod


@pytest.fixture
def logins(monkeypatch):
    mods = {}
    for p in PROVIDERS:
        name = PACKAGES[p][0]
        mods[p] = make_login_module(name)
        monkeypatch.setitem(sys.modules, name, mods[p])
    return mods


def sub_profile(p, **kw):
    return Profile(id=f"sub-{p}", source=f"remote/subscription/{p}", model=kw.pop("model", ""), options=kw)


def test_import_does_not_load_login_packages():
    code = ("import sys, ntu_easy_llm.adapters.subscription, ntu_easy_llm.catalog as c; c.catalog(); "
            "assert not {'claude_subscription','github_copilot_login','chatgpt_login'} & set(sys.modules)")
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("p", PROVIDERS)
def test_not_installed_check(monkeypatch, p):
    name, pip_name, _ = PACKAGES[p]
    real = importlib.util.find_spec
    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == name else real(n, *a))
    extra = EXTRAS[p]
    ok, reason = adapter_status(f"remote/subscription/{p}")
    assert not ok and pip_name in reason and f"ntu_easy_llm[{extra}]" in reason
    with pytest.raises(AdapterNotFound):
        get_adapter(sub_profile(p))
    node = find(f"remote/subscription/{p}")
    assert not node.available and f"ntu_easy_llm[{extra}]" in node.unavailable_reason and node.extra == extra


@pytest.mark.parametrize("p", PROVIDERS)
def test_catalog_marks_own_use_only(logins, p):
    node = find(f"remote/subscription/{p}")
    assert node.available and "只給本人使用" in node.description
    assert "own use only" in find(f"remote/subscription/{p}", locale="en").description


@pytest.mark.parametrize("p", PROVIDERS)
def test_catalog_marks_no_tools(logins, p):
    node = find(f"remote/subscription/{p}")
    assert node.tools is False and node.extra == EXTRAS[p]
    assert node.to_dict()["tools"] is False and node.to_dict()["extra"] == EXTRAS[p]
    assert "不支援工具（MCP）" in node.description
    assert "no tools (MCP)" in find(f"remote/subscription/{p}", locale="en").description


def test_catalog_tools_column_for_other_sources():
    from ntu_easy_llm.catalog import leaves
    tools = {n.path: n.tools for n in leaves()}
    assert tools["remote/api_key/openai"] is True and tools["remote/api_key/anthropic"] is True
    assert tools["local/ollama"] is None and tools["remote/api_key/llop"] is None
    assert [p for p, t in tools.items() if t is False] == [f"remote/subscription/{p}" for p in PROVIDERS]


def test_cli_catalog_prints_chat_only(capsys):
    from ntu_easy_llm.cli import main
    assert main(["catalog"]) == 0
    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines() if ln.strip()}
    for p in PROVIDERS:
        assert lines[f"remote/subscription/{p}"].endswith("[工具 ✗ 只能聊天]")
    assert lines["remote/api_key/openai"].endswith("[工具 ✓]")
    assert lines["local/ollama"].endswith("[工具：看伺服器與模型]")
    assert main(["catalog", "--locale", "en"]) == 0
    en = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines() if ln.strip()}
    assert en["remote/subscription/claude"].endswith("[tools ✗ chat only]")


def test_catalog_extras_match_adapter():
    from ntu_easy_llm import catalog as cat
    assert cat._SUBSCRIPTION_EXTRA == EXTRAS


@pytest.mark.parametrize("p", PROVIDERS)
def test_chat_text_only(logins, p):
    a = get_adapter(sub_profile(p, model="m1", timeout=42))
    caps = a.capabilities()
    assert caps.tools is False and caps.vision is False and caps.stop is False
    r = a.chat([{"role": "system", "content": "short"}, {"role": "user", "content": "hi"}])
    call = logins[p].calls[0]
    assert call["prompt"] == [{"role": "system", "content": "short"}, {"role": "user", "content": "hi"}]
    assert call["model"] == "m1" and call["timeout"] == 42 and call["system"] is None and call["opts"] == {}
    assert r.text == "answer" and r.finish == "stop" and r.usage.input_tokens == 5 and r.warnings == []


@pytest.mark.parametrize("p", PROVIDERS)
def test_tools_are_rejected_not_dropped(logins, p):
    a = get_adapter(sub_profile(p))
    with pytest.raises(LLMError) as ei:
        a.chat([{"role": "user", "content": "hi"}], tools=[{"name": "f"}])
    assert ei.value.kind is ErrorKind.TOOLS_UNSUPPORTED and logins[p].calls == []
    msg = str(ei.value)
    assert TOOLS_UNSUPPORTED_MESSAGE in msg
    assert "只能聊天" in msg and "工具（MCP）" in msg and "API 金鑰或本地來源" in msg
    assert "chat only" in msg and "tools (MCP)" in msg and "API-key or local source" in msg


def test_images_and_tool_messages_rejected(logins):
    a = get_adapter(sub_profile("claude"))
    with pytest.raises(LLMError) as ei:
        a.chat([Message(role="user", content="x", images=[ImagePart("image/png", b"1")])])
    assert ei.value.kind is ErrorKind.BAD_REQUEST
    with pytest.raises(LLMError):
        a.chat([{"role": "user", "content": "x"}, {"role": "tool", "tool_call_id": "c", "content": "r"}])
    assert logins["claude"].calls == []


def test_unsupported_options_become_warnings(logins):
    a = get_adapter(sub_profile("chatgpt", effort="high"))
    r = a.chat([{"role": "user", "content": "hi"}], max_tokens=10, temperature=0.1, stop=["x"])
    assert len(r.warnings) == 4 and all("ignored" in w for w in r.warnings)


@pytest.mark.parametrize("p", PROVIDERS)
def test_error_kinds(logins, p):
    m = logins[p]
    m.script = [m.UsageLimitError("used up", retry_after=30), m.AuthError("not signed in"),
                m.CallTimeoutError("slow"), m.NotInstalledError("no cli"), m.LoginError("odd"),
                m.PromptTooLongError("too long"), ValueError("last message must be user")]
    a = get_adapter(sub_profile(p))
    got = []
    for _ in range(7):
        with pytest.raises(LLMError) as ei:
            a.chat([{"role": "user", "content": "hi"}])
        got.append(ei.value.kind)
        assert ei.value.profile_id == f"sub-{p}"
    assert got == [ErrorKind.QUOTA_EXHAUSTED, ErrorKind.AUTH_INVALID, ErrorKind.TIMEOUT, ErrorKind.CONNECTION,
                   ErrorKind.UNKNOWN, ErrorKind.CONTEXT_TOO_LONG, ErrorKind.BAD_REQUEST]


def test_quota_retry_after_and_router_fallback(logins):
    m = logins["claude"]
    m.script = [m.UsageLimitError("used up", retry_after=120)]
    r = Router([get_adapter(sub_profile("claude")), FakeAdapter(["fallback"], profile=Profile("b", "local/fake"))],
               waterfall={"mode": "auto"})
    res = r.chat([{"role": "user", "content": "hi"}])
    assert res.profile_id == "b" and res.switched
    assert r.status()[0].last_error is ErrorKind.QUOTA_EXHAUSTED


def test_router_skips_subscription_when_tools_needed(logins):
    first = FakeAdapter([ErrorKind.QUOTA_EXHAUSTED], profile=Profile("a", "local/fake"))
    last = FakeAdapter(["tools ok"], profile=Profile("c", "local/fake"))
    r = Router([first, get_adapter(sub_profile("github_copilot")), last], waterfall={"mode": "auto"})
    res = r.chat([{"role": "user", "content": "hi"}], tools=[{"name": "f"}])
    assert res.profile_id == "c" and logins["github_copilot"].calls == []


def test_list_models_and_probe(logins, monkeypatch):
    a = get_adapter(sub_profile("claude"))
    assert [m.id for m in a.list_models()] == ["default-x"]
    p = a.probe()
    assert p.ok and logins["claude"].calls == []          # send=False never calls the CLI
    assert a.probe(send=True).ok and len(logins["claude"].calls) == 1
    monkeypatch.setattr(logins["claude"], "find_claude_binary", lambda: None)
    bad = a.probe()
    assert not bad.ok and bad.kind is ErrorKind.CONNECTION
