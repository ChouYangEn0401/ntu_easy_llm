"""Offline, deterministic tests for the public API.

These never touch the network or real API keys, so they are safe to run in CI
(`pytest`). For live end-to-end checks against real providers, use the
runnable script `tests/smoke_test.py --live` instead.
"""
from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

import ntu_easy_llm as L


# --------------------------------------------------------------------------- #
# package surface
# --------------------------------------------------------------------------- #

def test_version_is_nonempty_str():
    assert isinstance(L.__version__, str) and L.__version__


def test_all_public_names_lazy_importable():
    missing = []
    for name in L.__all__:
        if name == "__version__":
            continue
        try:
            getattr(L, name)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{name} ({exc})")
    assert not missing, f"these public names failed to load: {missing}"


# --------------------------------------------------------------------------- #
# response parsers (fake SDK objects)
# --------------------------------------------------------------------------- #

def test_parse_openai_response():
    assert L.parse_openai_response(SimpleNamespace(output_text=" hi ")) == "hi"
    assert L.parse_openai_response(SimpleNamespace(output_text=None)) == ""


def test_parse_openai_completion():
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=" yo "))]
    )
    assert L.parse_openai_completion(resp) == "yo"


def test_parse_gemini_response():
    assert L.parse_gemini_response(SimpleNamespace(text=" g ")) == "g"


def test_parse_anthropic_response():
    resp = SimpleNamespace(content=[SimpleNamespace(text=" a ")])
    assert L.parse_anthropic_response(resp) == "a"
    assert L.parse_anthropic_response(SimpleNamespace(content=[])) == ""


# --------------------------------------------------------------------------- #
# key management (real .env discovery via a temp cwd, fake keys)
# --------------------------------------------------------------------------- #

def _write_env(dirpath, mapping):
    lines = [f'{k}="{v}"' for k, v in mapping.items()]
    (dirpath / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_api_key_and_plain_aes_roundtrip(tmp_path, monkeypatch):
    aes_pw = "hunter2"
    _write_env(tmp_path, {
        "plainkey": "sk-plain-FAKE",
        "aeskey": L.aes_encrypt("sk-aes-FAKE", aes_pw),
        "AES_PW": aes_pw,
    })
    monkeypatch.chdir(tmp_path)

    assert L.load_api_key("plainkey") == "sk-plain-FAKE"
    assert L.KeyMaterial.plain("plainkey").resolve() == "sk-plain-FAKE"
    assert L.KeyMaterial.aes("aeskey", "AES_PW").resolve() == "sk-aes-FAKE"


def test_rsa_roundtrip(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from ntu_easy_llm.core.cryptions import RSADecryptStrategy

    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    (tmp_path / ".env").write_text(f'RSA_PEM="{priv_pem}"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cipher = L.rsa_encrypt("sk-rsa-FAKE", pub_pem)
    assert RSADecryptStrategy("RSA_PEM").decrypt(cipher) == "sk-rsa-FAKE"


def test_keymaterial_is_lazy(tmp_path, monkeypatch):
    """Constructing must not read .env; only resolve() should fail on a miss."""
    _write_env(tmp_path, {"exists": "v"})
    monkeypatch.chdir(tmp_path)

    km = L.KeyMaterial.plain("does_not_exist")  # construction: no raise
    with pytest.raises(Exception):
        km.resolve()  # first-touch: now it raises


# --------------------------------------------------------------------------- #
# LLMSession (provider call stubbed out — no network)
# --------------------------------------------------------------------------- #

def test_session_save_resume(tmp_path):
    s = L.LLMSession("chatgpt", model_name="gpt-4.1")
    s._dispatch = lambda messages: "stub-reply"
    s.ask("hi")
    assert s.message_count == 2
    assert s.history[0] == {"role": "user", "content": "hi"}

    s.save(sessions_dir=tmp_path)
    s2 = L.LLMSession.resume(s.session_id, sessions_dir=tmp_path)
    assert s2.session_id == s.session_id
    assert s2.history == s.history
    assert s2.provider == "CHATGPT"


def test_session_compress_history():
    s = L.LLMSession("anthropic")
    s._dispatch = lambda messages: "summary-or-reply"
    for i in range(6):
        s.ask(f"q{i}")
    assert s.message_count == 12
    s.compress_history(keep_last=2)
    assert s.message_count == 5
    assert s.history[0]["role"] == "system"


def test_session_ask_async():
    s = L.LLMSession("gemini")
    s._dispatch = lambda messages: "async-reply"
    box = {}
    fut = s.ask_async("hi", end_callback=lambda t: box.setdefault("cb", t))
    assert isinstance(fut, Future)
    assert fut.result(timeout=10) == "async-reply"
    assert box.get("cb") == "async-reply"
    assert s.message_count == 2


# --------------------------------------------------------------------------- #
# adapters & error handling
# --------------------------------------------------------------------------- #

def test_adapters_construct_without_network():
    cg = L.ChatGPTAdapter("fake", "gpt-4.1")
    gm = L.GeminiAdapter("fake", "gemini-2.5-flash")
    an = L.AnthropicAdapter("fake", "claude-haiku-4-5-20251001")
    for a in (cg, gm, an):
        assert isinstance(a, L.LLMAdapter)
        assert a.api_key == "fake"
        assert hasattr(a, "ask")


@pytest.mark.parametrize("bad", ["nope", ""])
def test_session_unknown_provider_raises(bad):
    with pytest.raises(ValueError):
        L.LLMSession(bad)


def test_dispatcher_unknown_provider_raises():
    with pytest.raises(ValueError):
        L.ask("BADPROVIDER", "k", "p", "m")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# ResponseCache
# --------------------------------------------------------------------------- #

def test_response_cache_roundtrip_and_persist(tmp_path):
    c = L.ResponseCache("unit", cache_dir=tmp_path)
    key = c.make_key("chatgpt", "gpt-4.1", "hi", web_search=False)
    assert key not in c
    assert c.get(key) is None

    c.set(key, "answer")
    assert key in c
    assert c.get(key) == "answer"
    assert len(c) == 1

    # autosave wrote it; a fresh instance reloads from disk
    c2 = L.ResponseCache("unit", cache_dir=tmp_path)
    assert c2.get(key) == "answer"


def test_response_cache_make_key_is_stable_and_distinct():
    k1 = L.ResponseCache.make_key("chatgpt", "gpt-4.1", "hi")
    k2 = L.ResponseCache.make_key("chatgpt", "gpt-4.1", "hi")
    k3 = L.ResponseCache.make_key("chatgpt", "gpt-4.1", "hi", web_search=True)
    assert k1 == k2
    assert k1 != k3


# --------------------------------------------------------------------------- #
# ask_many (provider call stubbed — no network)
# --------------------------------------------------------------------------- #

@pytest.fixture
def stub_chatgpt(monkeypatch):
    """Make ask_many's chatgpt path echo the prompt; count calls."""
    from ntu_easy_llm.core import utils

    calls = {"n": 0}

    def _fake_call(api_key, messages, model, web_search=False):
        calls["n"] += 1
        return f"echo:{messages[0]['content']}"

    monkeypatch.setattr(utils, "_resolve_api_key", lambda *a, **k: "fake-key")
    monkeypatch.setattr(utils, "_call_chatgpt", _fake_call)
    return calls


def test_ask_many_returns_in_order(stub_chatgpt):
    prompts = [f"q{i}" for i in range(10)]
    out = L.ask_many(prompts, "chatgpt", max_concurrent=4)
    assert out == [f"echo:q{i}" for i in range(10)]
    assert stub_chatgpt["n"] == 10


def test_ask_many_uses_cache_to_skip_calls(stub_chatgpt, tmp_path):
    cache = L.ResponseCache("batch", cache_dir=tmp_path)
    prompts = ["a", "b", "c"]

    first = L.ask_many(prompts, "chatgpt", cache=cache)
    assert first == ["echo:a", "echo:b", "echo:c"]
    assert stub_chatgpt["n"] == 3

    # second run: everything served from cache, no new provider calls
    second = L.ask_many(prompts, "chatgpt", cache=cache)
    assert second == first
    assert stub_chatgpt["n"] == 3


def test_ask_many_retries_then_succeeds(monkeypatch):
    from ntu_easy_llm.core import utils

    attempts = {"n": 0}

    def _flaky(api_key, messages, model, web_search=False):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    monkeypatch.setattr(utils, "_resolve_api_key", lambda *a, **k: "fake-key")
    monkeypatch.setattr(utils, "_call_chatgpt", _flaky)

    out = L.ask_many(["only"], "chatgpt", retries=3, backoff=0.0)
    assert out == ["ok"]
    assert attempts["n"] == 3


def test_ask_many_unknown_provider_raises():
    with pytest.raises(ValueError):
        L.ask_many(["x"], "bad")  # type: ignore[arg-type]


def test_ask_many_cache_keys_length_mismatch_raises():
    with pytest.raises(ValueError):
        L.ask_many(["a", "b"], "chatgpt", cache_keys=["only-one"])
