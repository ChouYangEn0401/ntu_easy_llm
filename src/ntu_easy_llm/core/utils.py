"""Core utilities for unified LLM API access.

This module provides:
- Compatible request/response interfaces for ChatGPT, Gemini, and Anthropic
- Model type hints for type-safe API calls
- Adapter pattern for service provider flexibility
- Automatic API key loading from environment
"""
from abc import ABC, abstractmethod
from typing import Literal, Union

from openai import OpenAI
from google import genai
from anthropic import Anthropic

from .config_loader import load_api_key
from .decorators import encap_text_with_title_decorator
from .cryptions import _decode_aes

# =======================
# Model Literals (官方可直接使用)
# =======================

ChatGPTModel = Literal[
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.1",
    "gpt-5",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-mini-search-preview",   
    "gpt-4o-search-preview",
    "gpt-4-turbo",
]

GeminiModel = Literal[
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    # 你若想追 preview/latest：
    # "gemini-3-pro",
    # "gemini-3-flash",
]

AnthropicModel = Literal[
    "claude-opus-4-5-20251101",    # 最新 flagship
    "claude-sonnet-4-5-20250929",  # 主推通用版本
    "claude-haiku-4-5-20251001",   # 快速輕量
    "claude-opus-4-1-20250805",    # 舊 4.x flagship
    "claude-opus-4-20250514",      # 舊 4.x
    "claude-sonnet-4-20250514",    # 舊 4.x
    "claude-3-haiku-20240307",     # 舊 3.x 輕量
    "claude-3-5-haiku-20241022",   # 可選 3.5 family
    "claude-3-7-sonnet-20250219",  # 可選 3.7 family
]

AnyModel = Union[ChatGPTModel, GeminiModel, AnthropicModel]


# =========================
# Public Unified Interface
# =========================

def ask(
    service_provider: Literal["CHATGPT", "GEMINI", "ANTHROPIC"],
    api_key: str,
    prompt: str,
    model_name: AnyModel,
):
    if service_provider == "CHATGPT":
        return _ask_chatgpt(api_key, prompt, model_name)   # type: ignore
    elif service_provider == "GEMINI":
        return _ask_gemini(api_key, prompt, model_name)    # type: ignore
    elif service_provider == "ANTHROPIC":
        return _ask_anthropic(api_key, prompt, model_name) # type: ignore
    else:
        raise ValueError(f"Unsupported service provider: {service_provider}")

# =========================
# Provider Implementations
# =========================

@encap_text_with_title_decorator("CHATGPT", "''''''")
def _ask_chatgpt(api_key: str, prompt: str, model_name: ChatGPTModel):
    client = OpenAI(api_key=api_key)
    resp = client.responses.create(
        model=model_name,
        tools=[{"type":"web_search"}],
        input=prompt
    )
    return resp.output_text.strip()


@encap_text_with_title_decorator("GEMINI", "''''''")
def _ask_gemini(api_key: str, prompt: str, model_name: GeminiModel):
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return resp.text.strip()


@encap_text_with_title_decorator("ANTHROPIC", "''''''")
def _ask_anthropic(api_key: str, prompt: str, model_name: AnthropicModel):
    client = Anthropic(api_key=api_key)
    model = resolve_claude_model(client, model_name)
    resp = client.messages.create(
        model=model_name,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip()

# =========================
# Model Fix
# =========================

def resolve_claude_model(
    client: Anthropic,
    requested: str,
) -> str:
    available = {m.id for m in client.models.list().data}
    if requested in available:
        return requested
    if "claude-haiku-4-5-20251001" in available:
        return "claude-haiku-4-5-20251001"
    raise RuntimeError(f"Request Module `{requested}` Is Not Usable For This API-Key !!\nYour Usable List: {available}")

# =========================
# Public APIs (Asking Service -- Auto Load Key)
# =========================

@encap_text_with_title_decorator("CHATGPT", "''''''")
def ask_chatgpt(
    prompt: str,
    model_name: ChatGPTModel = "gpt-4.1",
    password: str | None = None,

):
    chatgpt_api = load_api_key(tag="chatgpt")
    chatgpt_api = _decode_aes(chatgpt_api, password) if password else chatgpt_api

    client = OpenAI(api_key=chatgpt_api)
    resp = client.responses.create(
        model=model_name,
        input=prompt
    )
    return resp

def ask_chatgpt_resp(
    prompt: str,
    model_name: str = "gpt-5.2",
    password: str | None = None,
    #use_web_search: bool = True,     # ✅ 新增
    #return_response: bool = False,    # ✅ 新增：True 回傳 resp 物件
):
    chatgpt_api = load_api_key(tag="chatgpt")
    chatgpt_api = _decode_aes(chatgpt_api, password) if password else chatgpt_api

    client = OpenAI(api_key=chatgpt_api)
    resp = client.responses.create(
        model=model_name,
        tools=[{"type": "web_search"}],
        tool_choice="required",  # ⭐ 強制搜尋
        include=["web_search_call.action.sources"],
        input=prompt
    )
    return resp


@encap_text_with_title_decorator("GEMINI", "''''''")
def ask_gemini(
    prompt: str,
    model_name: GeminiModel = "gemini-2.5-flash-lite",
    password: str | None = None,
):
    gemini_api = load_api_key(tag="gemini")
    gemini_api = _decode_aes(gemini_api, password) if password else gemini_api

    client = genai.Client(api_key=gemini_api)
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return resp.text.strip()


@encap_text_with_title_decorator("ANTHROPIC", "''''''")
def ask_anthropic(
    prompt: str,
    model_name: AnthropicModel = "claude-haiku-4-5-20251001",
    password: str | None = None,
):
    anthropic_api = load_api_key(tag="anthropic")
    anthropic_api = _decode_aes(anthropic_api, password) if password else anthropic_api

    client = Anthropic(api_key=anthropic_api)
    resp = client.messages.create(
        model=model_name,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text.strip()

# =========================
# Public APIs  (Show Available Models List)
# =========================

def list_chatgpt_models(api_key: str):
    client = OpenAI(api_key=api_key)
    models = client.models.list()
    return [m.id for m in models.data]

def list_gemini_models(api_key: str):
    client = genai.Client(api_key=api_key)
    # 取得模型列表
    models = client.models.list()
    return [m.name for m in models]

def list_anthropic_models(api_key: str):
    return [m.id for m in Anthropic(api_key=api_key).models.list().data]

# =========================
# Public APIs  (Asking Service -- Load Specific Key)
# =========================

class LLMAdapter(ABC):
    def __init__(self, api_key: str, model_name: AnyModel):
        self.api_key = api_key
        self.model_name = model_name

    @abstractmethod
    def ask(self, prompt: str) -> str:
        pass

class ChatGPTAdapter(LLMAdapter):
    def __init__(self, api_key: str, model_name: ChatGPTModel):
        super().__init__(api_key, model_name)
    def ask(self, prompt: str) -> str:
        return _ask_chatgpt(self.api_key, prompt, self.model_name)  # type: ignore

class GeminiAdapter(LLMAdapter):
    def __init__(self, api_key: str, model_name: GeminiModel):
        super().__init__(api_key, model_name)
    def ask(self, prompt: str) -> str:
        return _ask_gemini(self.api_key, prompt, self.model_name)  # type: ignore

class AnthropicAdapter(LLMAdapter):
    def __init__(self, api_key: str, model_name: AnthropicModel):
        super().__init__(api_key, model_name)
    def ask(self, prompt: str) -> str:
        return _ask_anthropic(self.api_key, prompt, self.model_name)  # type: ignore

