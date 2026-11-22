"""0.6 contract: profile loading (secrets never in config), chains and waterfall types. Offline."""
from __future__ import annotations

import json

import pytest

from ntu_easy_llm.errors import ConfigError, ErrorKind
from ntu_easy_llm.profiles import Profile, SecretRef, load, looks_like_secret, make_secret_resolver
from ntu_easy_llm.waterfall import (AttemptStarted, ChatResult, Exhausted, FallbackNeeded, FallbackRequired,
                                    PendingFallback, Switched, WaterfallExhausted, WaterfallPolicy,
                                    event_from_record, switch_notice)
from ntu_easy_llm.agent import ChatReply, LLMHTTPError

FAKE_KEY = "sk-" + "A1b2C3d4" * 4          # built at runtime so no key-shaped literal sits in the repo

CONFIG = {
    "version": 1,
    "default": "chat",
    "profiles": {
        "home": {"source": "local/lmstudio", "base_url": "http://127.0.0.1:1234/v1/", "model": "example-model",
                 "options": {"context_size": 8192}},
        "main": {"source": "remote/api_key/openai", "api_key_env": "EXAMPLE_OPENAI_KEY", "model": "example-model"},
        "relay": {"source": "remote/api_key/llop", "llop_profile": "default", "transport": "auto", "model": "any"},
        "vaulted": {"source": "remote/api_key/anthropic", "secret": "vault:anthropic-main", "model": "m"},
        "_comment": "ignored",
    },
    "chains": {
        "chat": {"members": ["relay", "main", "home"], "waterfall": {"mode": "auto", "retry_429": 1}},
        "plain": ["main", "home"],
    },
}


def test_load_dict_basic_shape():
    ps = load(CONFIG, environ={})
    assert ps.names() == ["home", "main", "relay", "vaulted", "chat", "plain"]
    home = ps.get("home")
    assert home.base_url == "http://127.0.0.1:1234/v1" and home.option("context_size") == 8192
    assert (home.family, home.auth, home.provider) == ("local", "", "lmstudio")
    main = ps.get("main")
    assert (main.family, main.auth, main.provider) == ("remote", "api_key", "openai")
    assert main.secret == SecretRef("env:EXAMPLE_OPENAI_KEY")
    relay = ps.get("relay")
    assert relay.options["llop_profile"] == "default" and relay.options["transport"] == "auto"
    members, policy = ps.resolve()
    assert [p.id for p in members] == ["relay", "main", "home"]
    assert policy.mode == "auto" and policy.retry_429 == 1
    members, policy = ps.resolve("plain")
    assert policy.mode == "off" and not policy.enabled        # no waterfall key = off
    members, policy = ps.resolve("home")
    assert [p.id for p in members] == ["home"] and policy == WaterfallPolicy()


def test_load_json_file_text_and_yaml(tmp_path):
    p = tmp_path / "llm.json"
    p.write_text(json.dumps(CONFIG), encoding="utf-8")
    assert load(p, environ={}).default == "chat"
    assert load(str(p), environ={}).default == "chat"
    assert load(json.dumps(CONFIG), environ={}).default == "chat"
    yaml = pytest.importorskip("yaml")
    y = tmp_path / "llm.yaml"
    y.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    assert load(y, environ={}).get("main").model == "example-model"


def test_yaml_without_pyyaml_gives_install_hint(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "yaml":
            raise ImportError("no yaml")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    y = tmp_path / "llm.yml"
    y.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"ntu_easy_llm\[yaml\]"):
        load(y)


def test_round_trip_to_dict():
    ps = load(CONFIG, environ={})
    again = load(ps.to_dict(), environ={})
    assert again.to_dict() == ps.to_dict()
    assert again.get("vaulted").secret.ref == "vault:anthropic-main"


@pytest.mark.parametrize("profile", [
    {"source": "remote/api_key/openai", "api_key": "anything"},
    {"source": "remote/api_key/openai", "options": {"token": "x"}},
    {"source": "remote/api_key/openai", "options": {"Authorization": "x"}},
    {"source": "remote/api_key/openai", "model": FAKE_KEY},
    {"source": "remote/api_key/openai", "options": {"headers": ["Bearer abcdefghijklmnop"]}},
    {"source": "remote/api_key/openai", "secret": FAKE_KEY},
    {"source": "local/vllm", "base_url": "http://user:pw@127.0.0.1:8000/v1"},
    {"source": "local/vllm", "base_url": f"http://127.0.0.1:8000/v1?key={FAKE_KEY}"},
])
def test_literal_secrets_are_rejected_without_echoing_them(profile):
    with pytest.raises(ConfigError) as ei:
        load({"profiles": {"p": profile}}, environ={})
    assert FAKE_KEY not in str(ei.value) and "anything" not in str(ei.value)


def test_looks_like_secret():
    assert looks_like_secret(FAKE_KEY)
    assert looks_like_secret("AIza" + "x" * 35)
    assert not looks_like_secret("example-model") and not looks_like_secret("env:EXAMPLE_OPENAI_KEY")


@pytest.mark.parametrize("bad,match", [
    ({"profiles": {"p": {"model": "m"}}}, "source"),
    ({"profiles": {"p": {"source": "cloud/openai"}}}, "catalog path"),
    ({"profiles": {"p": {"source": "local/ollama", "api_key_env": "A", "secret": "env:B"}}}, "not both"),
    ({"profiles": {"p": {"source": "local/ollama", "api_key_env": "1BAD"}}}, "environment variable"),
    ({"profiles": {"p": {"source": "local/ollama", "base_url": "ftp://x"}}}, "http"),
    ({"profiles": {"p": {"source": "local/ollama", "x": 1, "options": {"x": 2}}}}, "both"),
    ({"profiles": {"bad id": {"source": "local/ollama"}}}, "profile id"),
    ({"version": 2, "profiles": {}}, "version"),
    ({"profiles": {}, "extra": 1}, "unknown top-level"),
    ({"profiles": {"a": {"source": "local/ollama"}}, "default": "zzz"}, "default"),
    ({"profiles": {"a": {"source": "local/ollama"}}, "chains": {"c": {"members": ["a", "b"]}}}, "unknown profiles"),
    ({"profiles": {"a": {"source": "local/ollama"}}, "chains": {"c": {"members": ["a", "a"]}}}, "twice"),
    ({"profiles": {"a": {"source": "local/ollama"}}, "chains": {"c": {"members": []}}}, "non-empty"),
    ({"profiles": {"a": {"source": "local/ollama"}}, "chains": {"a": ["a"]}}, "both a profile and a chain"),
    ({"profiles": {"a": {"source": "local/ollama"}},
      "chains": {"c": {"members": ["a"], "waterfall": {"mode": "always"}}}}, "mode"),
    ({"profiles": {"a": {"source": "local/ollama"}},
      "chains": {"c": {"members": ["a"], "waterfall": {"triggers": ["trust"]}}}}, "trust"),
    ({"profiles": {"a": {"source": "local/ollama"}},
      "chains": {"c": {"members": ["a"], "waterfall": {"triggers": ["nope"]}}}}, "triggers"),
    ({"profiles": {"a": {"source": "local/ollama"}},
      "chains": {"c": {"members": ["a"], "waterfall": {"retries": 3}}}}, "unknown keys"),
])
def test_config_errors(bad, match):
    with pytest.raises(ConfigError, match=match):
        load(bad, environ={})


def test_single_profile_becomes_default_and_base_url_env():
    ps = load({"profiles": {"only": {"source": "local/ollama", "base_url_env": "EXAMPLE_URL"}}},
              environ={"EXAMPLE_URL": "http://127.0.0.1:11434/"})
    assert ps.default == "only" and ps.get("only").base_url == "http://127.0.0.1:11434"
    ps = load({"profiles": {"only": {"source": "local/ollama", "base_url_env": "EXAMPLE_URL"}}}, environ={})
    assert ps.get("only").base_url is None and ps.get("only").base_url_env == "EXAMPLE_URL"


def test_secret_resolution_is_lazy_and_scoped():
    ps = load(CONFIG, environ={"EXAMPLE_OPENAI_KEY": "value-from-env"},
              resolve_secret=lambda ref: "value-from-vault" if ref == "vault:anthropic-main" else None)
    r = ps.secret_resolver()
    assert r(ps.get("main").secret) == "value-from-env"
    assert r(ps.get("vaulted").secret) == "value-from-vault"
    with pytest.raises(ConfigError, match="could not be resolved"):
        r("vault:other")
    no_vault = make_secret_resolver(environ={})
    with pytest.raises(ConfigError, match="EXAMPLE_OPENAI_KEY"):
        no_vault("env:EXAMPLE_OPENAI_KEY")
    with pytest.raises(ConfigError, match="resolve_secret"):
        no_vault("vault:x")


def test_profile_is_immutable():
    p = Profile("p", "local/ollama", options={"a": 1})
    with pytest.raises(Exception):
        p.model = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        p.options["a"] = 2  # type: ignore[index]
    assert p.replace(model="m2").model == "m2" and p.model == ""


def test_chain_cannot_be_a_single_adapter():
    ps = load(CONFIG, environ={})
    with pytest.raises(ConfigError, match="chain"):
        ps.adapter("chat")


# --------------------------------------------------------------------------- #
# waterfall types
# --------------------------------------------------------------------------- #

def test_policy_defaults_and_json():
    p = WaterfallPolicy()
    assert p.mode == "off" and p.triggers == {ErrorKind.QUOTA_EXHAUSTED, ErrorKind.AUTH_INVALID,
                                              ErrorKind.RATE_LIMITED}
    assert WaterfallPolicy.from_dict(None) == p and WaterfallPolicy.from_dict({}) == p
    q = WaterfallPolicy.from_dict({"mode": "ASK", "triggers": ["quota_exhausted", "overloaded"], "_note": "x"})
    assert q.mode == "ask" and ErrorKind.OVERLOADED in q.triggers
    assert WaterfallPolicy.from_dict(json.loads(json.dumps(q.to_dict()))) == q
    with pytest.raises(ConfigError):
        WaterfallPolicy(retry_429=-1)


def test_events_round_trip():
    evs = [AttemptStarted("a", attempt=2, model="m"), Switched("a", to_id="b", reason=ErrorKind.QUOTA_EXHAUSTED),
           FallbackNeeded("a", reason=ErrorKind.AUTH_INVALID, candidates=("b", "c"), pending_id="x"),
           Exhausted(reasons=(("a", ErrorKind.QUOTA_EXHAUSTED), ("b", ErrorKind.AUTH_INVALID)))]
    for e in evs:
        rec = json.loads(json.dumps(e.to_record()))
        assert rec["type"] == "event" and rec["v"] == 1
        assert event_from_record(rec) == e
    assert Switched("a", to_id="b").to_record()["event"] == "switched"


def test_chat_result_and_pending():
    r = ChatResult.from_reply(ChatReply(text="hi", finish="stop"), profile_id="b", switched=True,
                              notice=switch_notice("b", ErrorKind.QUOTA_EXHAUSTED))
    assert isinstance(r, ChatReply) and r.text == "hi" and r.finish == "stop"
    assert r.notice == "已改用 b（原因：額度用完）"
    assert switch_notice("b", "auth_invalid", "en") == "Switched to b (reason: key invalid)"
    original = LLMHTTPError(401, "", "x")
    pending = PendingFallback("a", ErrorKind.AUTH_INVALID, ("b",), message="401", expires_at=100.0, error=original)
    assert pending.expired(now=100.0) and not pending.expired(now=99.0)
    assert "error" not in pending.to_dict() and json.dumps(pending.to_dict())
    fr = FallbackRequired(pending)
    assert fr.kind is ErrorKind.AUTH_INVALID and fr.pending is pending and fr.profile_id == "a"
    ex = WaterfallExhausted([("a", LLMHTTPError(429, "insufficient_quota", "x")), ("b", original)])
    assert ex.kind is ErrorKind.AUTH_INVALID and ex.profile_id == "b" and len(ex.errors) == 2
