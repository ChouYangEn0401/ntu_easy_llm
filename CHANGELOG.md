# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.0a2] - 2026-11-23

### Security
- The `[llop]` extra now pins the LLOP SDK by its GitHub release wheel URL instead of a bare package
  name, so pip can never pick up a same-named package someone registers on PyPI.

## [0.6.0a1] - 2026-11-23

### Added (0.6.0.dev0 — interface freeze; see `docs/design/20261123-v06-architecture.md`)
- `ntu_easy_llm.types`: provider-neutral `Message` and `ImagePart` (repr never
  prints image bytes), `as_message` / `as_messages` (dicts, objects, OpenAI /
  Anthropic content parts), `normalize_finish`; re-exports `Tool`, `ToolCall`,
  `Usage`, `ChatReply` (the same classes as `ntu_easy_llm.agent`).
- `ChatReply.finish` (`"" | "stop" | "length" | "thinking" | "tool_calls"`) and
  `ChatReply.warnings`, both with defaults.
- `ntu_easy_llm.errors`: `ErrorKind`, the generic `classify()` rule set with
  `register_classifier()` for provider rules, `parse_retry_after`,
  `describe_kind` (zh-TW / en), `ConfigError`, `AdapterNotFound`.
- `LLMError` and its subclasses gain `.kind`, `.retry_after`, `.profile_id`
  (class defaults + keyword-only constructor arguments); `LLMHTTPError`
  classifies itself and keeps `.headers`; `agent.chat` now reports timeouts as
  `kind == TIMEOUT` and reads `Retry-After`.
- `ntu_easy_llm.profiles`: `Profile`, `ProfileSet`, `ChainSpec`, `SecretRef`,
  `load()` for dicts, JSON and YAML. Configs may only *reference* keys
  (`api_key_env`, `secret: "<scheme>:<name>"`); a literal key raises
  `ConfigError` without echoing it.
- `ntu_easy_llm.adapters`: the `Adapter` protocol, `Capabilities`, `ModelInfo`,
  `ProbeResult`, a registry with the `ntu_easy_llm.adapters` entry point group,
  and `FakeAdapter` (`local/fake`) — scripted replies and errors of every kind
  for downstream tests.
- `ntu_easy_llm.catalog`: the source tree (`local/*`, `remote/api_key/*`
  incl. `llop`, `remote/subscription/{claude,github_copilot,chatgpt}`) with
  field specs and per-machine availability.
- `ntu_easy_llm.conversation` (optional): `ChatSession`, `SessionStore`,
  `MemorySessionStore`, `JsonlSessionStore`.
- `ntu_easy_llm.waterfall`: fallback-chain types and JSON config format
  (`WaterfallPolicy`, events, `ChatResult`, `PendingFallback`,
  `FallbackRequired`, `WaterfallExhausted`). No config = off. The routing
  itself comes in a later 0.6 pre-release.
- Extra `yaml` (PyYAML); `dev` now includes PyYAML for the tests.

### Added (HTTP adapters; see section 5 of the architecture doc)
- Adapters for `local/lmstudio`, `local/ollama` (native `/api/chat` with
  `num_ctx`, falls back to `/v1`), `local/vllm`, `local/llamacpp`,
  `local/openai_compatible`, `remote/api_key/openai`, `remote/api_key/anthropic`,
  `remote/api_key/gemini` (OpenAI-compatible endpoint) and
  `remote/api_key/openai_compatible`. Standard library only; the catalog now
  shows them as available. `stop`, images (`ImagePart`), `max_tokens`,
  `temperature`, `timeout` and streaming through `on_delta` (SSE / NDJSON).
- `list_models()` (LM Studio and Ollama say which models are loaded) and
  `probe(send=False)` only read; `probe(send=True)` sends one short message.
- Addresses on this machine or the LAN bypass proxies; remote ones use the
  system settings (`options.proxy` overrides).
- Per-provider error rules (`ntu_easy_llm.adapters.classifiers`) for OpenAI,
  Anthropic, Gemini and the local servers, loaded on first `classify`; a
  caller's own `register_classifier` for the same name wins. Wording that is
  not yet verified against the live services is marked in the code and docs.
- Command line: `ntu_easy_llm catalog`, `models`, `probe [--send]` and
  `chat [--stream]` (`--config FILE --profile P`); `--version` unchanged.

### Changed
- `agent.to_openai_messages` / `to_anthropic_messages` send the images of a
  0.6 `Message` (`image_url` data URLs / base64 `image` blocks). Messages
  without images are converted exactly as before.

### Added (0.6 pre-release — waterfall routing, LLOP and subscription sources)
- `ntu_easy_llm.router.Router` (alias `LLM`): several members behind one
  adapter-shaped object. `off` = first member only, errors unchanged; `auto`
  switches on the policy's triggers (quota, invalid key, 429 after
  `retry_429` retries) and reports it in `ChatResult.switched` / `notice` /
  `events`; `ask` raises `FallbackRequired` and `resume(pending, use=...)`
  continues. In-memory cooldowns (`status()`, `reset()`); `trust` never
  switches; `link_changed` is not retried. A `ChatSession` is shared across
  members, gets a `switched` event and the reply. `ProfileSet.router(name)`.
- `remote/api_key/llop` adapter (extra `llop`, the LLOP client SDK, imported
  lazily): `llop_profile` names the SDK profile; `SdkError.kind_hint` maps to
  `ErrorKind`. LLOP is not on PyPI yet — install its SDK from an LLOP release
  wheel or a checkout; without the extra nothing changes.
- `remote/subscription/{claude,github_copilot,chatgpt}` adapters over the
  shared login-package interface (imported lazily): text only, `tools` raise
  `tools_unsupported`, `LoginError.kind` maps to `ErrorKind`. The catalog marks
  them "for your own use only".

### Unchanged
- Every 0.4 / 0.5 public name and signature (`ask*`, `ask_many`, `LLMSession`,
  `LLMAdapter` family, `KeyMaterial`, `agent.chat` / `agent.probe`). Importing
  any 0.6 module still loads no provider SDK.

## [0.5.0] - 2026-11-09

### Added
- **Agent API** — new module `ntu_easy_llm.agent` (also reachable lazily as
  `ntu_easy_llm.agent`): `chat(provider, model, messages, tools=None, *,
  api_key, base_url, max_tokens, temperature, timeout, session, extra_headers,
  extra_body) -> ChatReply` with `ChatReply(text, tool_calls=[ToolCall(id,
  name, arguments: dict)], usage=Usage(input_tokens, output_tokens), raw,
  stop_reason)`, plus `probe(...) -> str | None` for "test the settings" buttons.
- Providers for the agent API: `openai`, `anthropic` (native tool use),
  `gemini` (OpenAI-compatible endpoint) and **`openai_compatible`** taking any
  `base_url` (company gateways, Ollama, LM Studio, vLLM).
- Typed errors `LLMHTTPError` (`.status`, `.body`), `LLMConnectionError`,
  `LLMResponseError`; message converters `to_openai_messages` /
  `to_anthropic_messages`.
- Offline tests `tests/test_agent.py` (fake HTTP session, no network).

### Unchanged
- Every existing public function, class and signature (`ask*`, `ask_many`,
  `LLMSession`, adapters, key management) is byte-for-byte the same.

## [0.4.3] - 2026-06-29

(Heading added in 0.5.0: these entries sat under "Unreleased" but shipped in
the `ntu_easy_llm-0.4.3` release.)

### Added
- `ask_many()` — batch helper that runs many prompts with **bounded
  concurrency** (`max_concurrent`), per-prompt retry with exponential backoff,
  optional throttle and progress callback, and returns answers in input order.
- `ResponseCache` — a thread-safe, JSON-backed `key -> answer` cache. Pass it to
  `ask_chatgpt` / `ask_gemini` / `ask_anthropic` / `ask_many` (`cache=`) to skip
  questions already answered on a previous run. Keys are auto-hashed from
  `(provider, model, prompt, web_search)` or supplied explicitly (`cache_key` /
  `cache_keys`) for a semantic question→answer mapping.
- Deployment smoke test (`tests/smoke_test.py`) covering every public API,
  with an offline tier (no keys / no network) and a `--live` tier; full run
  results recorded in `TEST_LOG.md`.
- Offline pytest suite (`tests/test_public_api.py`) + pytest config.
- `LICENSE` (MIT) and a `py.typed` marker so the package ships as a typed
  library (consumers' type checkers now pick up the bundled type hints).

### Removed
- Stale planning docs `CODE_REVIEW.md`, `tmp_CHANGELOG.md`, and
  `DevPlanBook.md` (outdated; `CODE_REVIEW.md` was also factually wrong about
  the OpenAI Responses API).

## [0.3.0]

### Added
- Web search mode (`web_search=True`) for ChatGPT (Responses API `web_search`
  tool) and Gemini (Google Search grounding).
- Non-blocking async calls: `ask_chatgpt_async` / `ask_gemini_async` /
  `ask_anthropic_async` and `LLMSession.ask_async`, with `end_callback` and
  `on_error` hooks.
- Multi-turn `LLMSession` with disk persistence (`save` / `resume`) and
  `compress_history` for token control.
- Unified `ask()` dispatcher that takes an explicit API key.
- `KeyMaterial` with named constructors (`.plain` / `.aes` / `.rsa`) and lazy,
  first-touch `resolve()`; `aes_encrypt` / `rsa_encrypt` helpers.
- Response-parsing helpers: `parse_openai_response`, `parse_openai_completion`,
  `parse_gemini_response`, `parse_anthropic_response`.

### Changed
- Provider calls now send a full message list (multi-turn aware); ChatGPT uses
  the OpenAI Responses API (`responses.create`).
- `import ntu_easy_llm` is lightweight via PEP 562 lazy loading — provider SDKs
  load only on first use.
- Standardized Gemini on the `google-genai` SDK.

### Note
- 0.2.0–0.2.1 were internal iterations and were consolidated into 0.3.0 rather
  than released separately.

## [0.1.3] - 2026-03-02

### Fixed
- ChatGPT API call handling and message formatting.
- Import paths switched to relative imports for correct pip-installed usage.

### Changed
- README examples use `from ntu_easy_llm import ...`.
- Dependency management consolidated into `pyproject.toml` (removed
  `requirements.txt`).

## [0.1.2] - 2026-02-15

### Added
- AES / RSA encryption strategies for API-key storage.
- Smart `.env` discovery that walks up the directory tree.
- CLI with a `--version` flag.
- Adapter pattern: `ChatGPTAdapter` / `GeminiAdapter` / `AnthropicAdapter`.
- Per-provider model listing helpers.

## [0.1.1] - 2026-01-20

### Added
- Initial release: unified wrapper for ChatGPT, Gemini, and Anthropic.
- Single-shot `ask_*` functions and `Literal` model-name type hints.

## [0.1.0]

- Initial project scaffold.
