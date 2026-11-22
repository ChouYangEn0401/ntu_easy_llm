"""Per-provider error rules (ntu_easy_llm.adapters.classifiers via errors.classify). Offline, pure functions."""
from __future__ import annotations

import json

import pytest

from ntu_easy_llm.agent import LLMHTTPError
from ntu_easy_llm.errors import ErrorKind, classify, register_classifier

K = ErrorKind


def j(obj) -> str:
    return json.dumps(obj)


def gemini_err(code, status, message, reason="", extra_details=()):
    details = ([{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}] if reason else [])
    return j([{"error": {"code": code, "message": message, "status": status,
                         "details": details + list(extra_details)}}])


@pytest.mark.parametrize("provider,status,body,headers,tools,kind,wait", [
    # OpenAI
    ("openai", 429, j({"error": {"code": "insufficient_quota", "type": "insufficient_quota",
                                 "message": "You exceeded your current quota"}}), {}, False, K.QUOTA_EXHAUSTED, None),
    ("openai", 429, j({"error": {"code": "rate_limit_exceeded", "message": "Rate limit reached"}}),
     {"Retry-After": "12"}, False, K.RATE_LIMITED, 12.0),
    ("openai", 429, "", {"retry-after-ms": "1500"}, False, K.RATE_LIMITED, 1.5),
    ("openai", 401, j({"error": {"code": "invalid_api_key"}}), {}, False, K.AUTH_INVALID, None),
    ("openai", 403, j({"error": {"code": "unsupported_country_region_territory"}}), {}, False, K.PERMISSION, None),
    ("openai", 404, j({"error": {"code": "model_not_found"}}), {}, False, K.MODEL_NOT_FOUND, None),
    ("openai", 400, j({"error": {"code": "context_length_exceeded"}}), {}, False, K.CONTEXT_TOO_LONG, None),
    ("openai", 400, j({"error": {"message": "tools is not supported in this model"}}), {}, True,
     K.TOOLS_UNSUPPORTED, None),
    ("openai", 400, j({"error": {"code": "invalid_function_parameters",
                                 "message": "Invalid schema for function 'add'"}}), {}, True, K.BAD_REQUEST, None),
    ("openai", 503, "", {}, False, K.OVERLOADED, None),
    # Anthropic
    ("anthropic", 529, j({"type": "error", "error": {"type": "overloaded_error"}}), {}, False, K.OVERLOADED, None),
    ("anthropic", 401, j({"type": "error", "error": {"type": "authentication_error"}}), {}, False,
     K.AUTH_INVALID, None),
    ("anthropic", 400, j({"type": "error", "error": {"type": "invalid_request_error",
                                                     "message": "Your credit balance is too low"}}), {}, False,
     K.QUOTA_EXHAUSTED, None),
    ("anthropic", 429, j({"type": "error", "error": {"type": "rate_limit_error"}}), {"retry-after": "30"}, False,
     K.RATE_LIMITED, 30.0),
    ("anthropic", 400, j({"type": "error", "error": {"type": "invalid_request_error",
                                                     "message": "tools.0: unknown field"}}), {},
     True, K.TOOLS_UNSUPPORTED, None),
    # Gemini (OpenAI-compatible endpoint, Google error shape)
    ("gemini", 400, gemini_err(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.",
                               "API_KEY_INVALID"), {}, False, K.AUTH_INVALID, None),
    ("gemini", 400, gemini_err(400, "INVALID_ARGUMENT", "API key expired. Please renew the API key."), {}, False,
     K.AUTH_INVALID, None),
    ("gemini", 429, gemini_err(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.",
                               extra_details=[{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProject"}]},
                                              {"retryDelay": "37s"}]), {}, False, K.RATE_LIMITED, 37.0),
    ("gemini", 429, gemini_err(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.",
                               extra_details=[{"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]}]),
     {}, False, K.QUOTA_EXHAUSTED, None),
    ("gemini", 403, gemini_err(403, "PERMISSION_DENIED", "denied"), {}, False, K.PERMISSION, None),
    ("gemini", 404, gemini_err(404, "NOT_FOUND", "models/example is not found"), {}, False, K.MODEL_NOT_FOUND,
     None),
    # local servers
    ("lmstudio", 400, j({"error": "Context size has been exceeded."}), {}, False, K.CONTEXT_TOO_LONG, None),
    ("lmstudio", 400, j({"error": "The number of tokens to keep from the initial prompt is greater than the "
                                  "context length"}), {}, False, K.CONTEXT_TOO_LONG, None),
    ("lmstudio", 404, j({"error": "No models loaded. Please load a model"}), {}, False, K.MODEL_NOT_FOUND, None),
    ("ollama", 404, j({"error": 'model "x" not found, try pulling it first'}), {}, False, K.MODEL_NOT_FOUND, None),
    ("ollama", 400, j({"error": "x does not support tools"}), {}, True, K.TOOLS_UNSUPPORTED, None),
    ("ollama", 500, j({"error": "the input length exceeds the context length"}), {}, False, K.CONTEXT_TOO_LONG,
     None),
    ("vllm", 400, j({"message": "This model's maximum context length is 4096 tokens."}), {}, False,
     K.CONTEXT_TOO_LONG, None),
    ("vllm", 404, j({"message": "The model `x` does not exist."}), {}, False, K.MODEL_NOT_FOUND, None),
    ("llamacpp", 400, j({"error": {"type": "exceed_context_size_error",
                                   "message": "the request exceeds the available context size"}}), {}, False,
     K.CONTEXT_TOO_LONG, None),
    ("llamacpp", 500, j({"error": {"message": "tools param requires --jinja flag"}}), {}, True, K.TOOLS_UNSUPPORTED,
     None),
    ("openai_compatible", 404, "Not Found", {}, False, K.BAD_REQUEST, None),
])
def test_provider_rules(provider, status, body, headers, tools, kind, wait):
    got = classify(provider, status=status, body=body, headers=headers, tools_sent=bool(tools))
    assert got == (kind, wait)


def test_llmhttperror_from_05_agent_uses_the_rules_too():
    e = LLMHTTPError(429, gemini_err(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.",
                                     extra_details=[{"retryDelay": "5s"}]), "gemini")
    assert e.kind is K.RATE_LIMITED and e.retry_after == 5.0
    assert LLMHTTPError(529, "", "anthropic").kind is K.OVERLOADED


def test_caller_rule_replaces_the_builtin_and_removal_falls_back_to_generic():
    register_classifier("openai", lambda **kw: (K.SERVER_ERROR, None))
    try:
        assert classify("openai", status=401)[0] is K.SERVER_ERROR
    finally:
        register_classifier("openai", None)
    assert classify("openai", status=401)[0] is K.AUTH_INVALID          # generic rule
    # restore the built-in for the rest of the session
    from ntu_easy_llm.adapters.classifiers import BUILTIN_CLASSIFIERS
    register_classifier("openai", BUILTIN_CLASSIFIERS["openai"])
    assert classify("openai", status=429, body="")[0] is K.RATE_LIMITED


def test_success_statuses_and_exceptions_fall_through():
    assert classify("openai", status=200, body="not json")[0] is K.BAD_RESPONSE
    assert classify("ollama", exc=ConnectionRefusedError())[0] is K.CONNECTION
    assert classify("anthropic", exc=TimeoutError())[0] is K.TIMEOUT
