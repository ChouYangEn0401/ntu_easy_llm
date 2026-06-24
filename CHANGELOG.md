# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Deployment smoke test (`tests/smoke_test.py`) covering every public API,
  with an offline tier (no keys / no network) and a `--live` tier; full run
  results recorded in `TEST_LOG.md`.
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
