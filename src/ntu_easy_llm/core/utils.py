import base64
from functools import wraps
from typing import Literal, Union

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend

from openai import OpenAI
from google import genai
from anthropic import Anthropic

from .config_loader import load_api_key


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

ClaudeModel = Literal[
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

AnyModel = Union[ChatGPTModel, GeminiModel, ClaudeModel]


# =========================
# Public Unified Interface
# =========================

def ask(
    service_provider: Literal["CHATGPT", "GEMINI", "CLAUDE"],
    api_key: str,
    prompt: str,
    model_name: AnyModel,
):
    if service_provider == "CHATGPT":
        return _ask_chatgpt(api_key, prompt, model_name)   # type: ignore
    elif service_provider == "GEMINI":
        return _ask_gemini(api_key, prompt, model_name)    # type: ignore
    elif service_provider == "CLAUDE":
        return _ask_anthropic(api_key, prompt, model_name) # type: ignore
    else:
        raise ValueError(f"Unsupported service provider: {service_provider}")


# =========================
# Text Encapsulation Utils
# =========================

def encap_text(title: str, content: str, seperator: str = '```') -> str:
    return f"{title}\n{seperator}\n{content}\n{seperator}\n"

def encap_text_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return encap_text("", func(*args, **kwargs))
    return wrapper


def encap_text_with_title_decorator(title: str, separator: str = "```"):
    def _decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return encap_text(title, func(*args, **kwargs), separator)
        return wrapper
    return _decorator

# =========================
# Provider Implementations
# =========================

@encap_text_with_title_decorator("CHATGPT", "''''''")
def _ask_chatgpt(api_key: str, prompt: str, model_name: ChatGPTModel):
    client = OpenAI(api_key=api_key)
    resp = client.responses.create(
        model=model_name,
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
def _ask_anthropic(api_key: str, prompt: str, model_name: ClaudeModel):
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
# AES Decode Utils
# =========================

def _decode_aes(encoded_content: str, password: str) -> str:

    def _derive_key_iv(password: str):
        raw = password.encode("utf-8")
        from hashlib import sha256
        key = sha256(raw).digest()
        iv = key[:16]
        return key, iv

    def _aes_decrypt(enc: str, key: bytes, iv: bytes) -> str:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted_padded = decryptor.update(base64.b64decode(enc)) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decrypted = unpadder.update(decrypted_padded) + unpadder.finalize()
        return decrypted.decode("utf-8")

    key, iv = _derive_key_iv(password)
    decoded_content = _aes_decrypt(encoded_content, key, iv)
    return decoded_content


# =========================
# Public APIs (Auto Load Key)
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
    return resp.output_text.strip()


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
    model_name: ClaudeModel = "claude-haiku-4-5-20251001",
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

