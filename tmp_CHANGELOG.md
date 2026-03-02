# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.3] - 2026-03-02

### 🔴 Fixed (Critical)
- **Fixed ChatGPT API calls** - Changed from invalid `responses.create()` to correct `chat.completions.create()` with proper message format
- **Fixed import paths** - Changed absolute imports (`from src.ntu_easy_llm`) to relative imports (`from .module`) for compatibility with pip-installed packages
- **Fixed cryptions.py import** - Updated to use relative import for config_loader

### 🟡 Fixed (High Priority)
- **Updated README examples** - Corrected all 6 code examples to use `from ntu_easy_llm import` instead of `from src.ntu_easy_llm import`
- **Fixed model names** - Updated deprecated OpenAI models (gpt-5.2-pro → gpt-4o, gpt-4.1 → gpt-4o-mini)
- **Deleted requirements.txt** - Removed redundant file; now using only `pyproject.toml` for dependency management

### 🟢 Improved (Medium Priority)
- **Fixed example.py** - Rewrote with correct imports and functional examples
- **Fixed test files** - Updated `tests/code0.py` with correct imports and added module docstring
- **Enhanced .gitignore** - Added critical rules for `.env` and `*.env` files (security fix), old_release folder
- **Added module docstrings** - Improved documentation in 7 core modules for better IDE support and clarity

### 📚 Documentation
- Added comprehensive CODE_REVIEW.md with detailed analysis of all issues
- Added FIXES_SUMMARY.md with before/after comparisons
- Added COMPLETION_REPORT.md with testing checklist

### ⚠️ Note
This release fixes critical issues that prevented the package from working in real Python environments (outside PyCharm IDE). All imports now follow Python best practices.

---

## [0.1.2] - 2026-02-15

### Added
- Encryption support with AES and RSA strategies for API keys
- Configuration loading with smart `.env` file discovery
- CLI command with `--version` flag support
- Adapter pattern for flexible provider selection (ChatGPTAdapter, GeminiAdapter, AnthropicAdapter)

### Changed
- Improved API key loading with better error messages
- Enhanced decorator system for output formatting

---

## [0.1.1] - 2026-01-20

### Added
- Initial release with unified LLM API wrapper
- Support for ChatGPT, Gemini, and Anthropic APIs
- Model listing functions for each provider
- Basic ask functions for each LLM provider
- Type hints with Lit

eral types for model names

---

# 📝 How to Maintain This File

## When to Update?

Update CHANGELOG.md **every time you release a new version**. Add entries as you develop (don't wait until release).

## Version Format

Use **Semantic Versioning**: `MAJOR.MINOR.PATCH`

- **MAJOR** (0.1.0 → 1.0.0): Breaking changes that users must update code for
  - Example: Rename `ask_chatgpt()` to `query_chatgpt()`
  
- **MINOR** (0.1.0 → 0.2.0): New features, backwards compatible
  - Example: Add new `ask_claude()` function
  
- **PATCH** (0.1.0 → 0.1.1): Bug fixes, backwards compatible
  - Example: Fix crash in error handling

## Section Categories

Use these 6 sections (in order) under each version:

```markdown
### Added
- New features

### Changed  
- Behavior changes in existing features

### Deprecated
- Features that will be removed soon (warn users first)

### Removed
- Features that were removed

### Fixed
- Bug fixes

### Security
- Security-related fixes
```

**Tip:** Color code by severity:
- 🔴 = Critical (MUST see this)
- 🟡 = High (Should see this)
- 🟢 = Medium (Nice to know)
- ⚪ = Low (Optional)

## Example Entries

✅ **Good:**
```markdown
### Fixed
- **Fixed authentication failure** - Updated token refresh logic to handle expired tokens properly (#142)
- Memory leak in event listener cleanup when connection closes unexpectedly
```

❌ **Bad:**
```markdown
### Fixed
- Fixed stuff
- Bug fix
```

## Template for Next Release

Copy this when starting a new version:

```markdown
## [X.X.X] - YYYY-MM-DD

### Added
- 

### Changed
-

### Fixed
-

### Security
-

```

## Common Mistakes to Avoid

1. ❌ **Writing too vague** - "Fixed bugs" → ✅ "Fixed crash when API returns null response"
2. ❌ **Writing too technical** - Users need to understand why → Add brief explanation
3. ❌ **Forgetting dates** - Always include date in `YYYY-MM-DD` format
4. ❌ **Not linking to issues** - Add GitHub issue numbers like `(#123)` for traceability
5. ❌ **Mixing old entries** - Never edit past released versions; only add new sections at top

## Our Project's Versions Explained

**v0.1.1** = Initial release (proof of concept)
**v0.1.2** = Added encryption & better features  
**v0.1.3** = Critical bug fixes for real-world usage
**v0.2.0** = (Future) Add async support, new features
**v1.0.0** = (Future) Production-ready, stable API

## When Writing Release Notes

1. After you finish developing a feature, add it here
2. Before publishing to PyPI, review all entries
3. Update version in `pyproject.toml` to match
4. Update version in `src/ntu_easy_llm/_version.py`
5. Commit with message: "Release v0.1.3"

## Real-World Example for Your Next Update

When you add async support:

```markdown
## [0.2.0] - 2026-04-15

### Added
- **Async API support** - New `ask_chatgpt_async()` for concurrent LLM calls
- **Model caching** - Cache model lists to reduce API calls
- **Configuration profiles** - Support dev, staging, production environments

### Changed
- Improved error messages with actionable suggestions

### Deprecated
- Synchronous adapters will be removed in v1.0.0 (use async versions instead)

### Security
- Added rate limiting to prevent API abuse
```

## Links for Reference

- [Keep a Changelog](https://keepachangelog.com/) - Official guide
- [Semantic Versioning](https://semver.org/) - Version numbering rules
- [Python Versioning PEPs](https://www.python.org/dev/peps/pep-0440/) - Official Python versioning

---

**Pro Tip:** Generate release notes from CHANGELOG.md when publishing to PyPI and GitHub. This prevents users from missing important information!
