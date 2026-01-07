import base64
from dotenv import load_dotenv
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from typing import Literal
from dotenv import load_dotenv
from openai import OpenAI, AsyncOpenAI
from google import genai

from .config_loader import load_api_key


ChatGPTModel = Literal["gpt-4.1-mini", "gpt-4.1", "gpt-4.1-preview", "o3-mini"]
GeminiModel = Literal["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

def ask(service_provider: Literal["CHATGPT", "GEMINI", "CLAUDE"], api_key: str, prompt: str, model_name: str):
    if service_provider == "CHATGPT":
        return _ask_chatgpt(api_key, prompt, model_name)
    elif service_provider == "GEMINI":
        return _ask_gemini(api_key, prompt, model_name)
    else:
        raise ValueError("No Mode Found Or Not Support Yet !!")

def encap_text(title: str, content: str, seperator='```'):
    return f"{title}\n{seperator}\n{content}\n{seperator}\n"
def encap_text_decorator(func):
    def wrapper(*args, **kwargs):
        return encap_text("", func(*args, **kwargs))
    return wrapper
def encap_text_with_title_decorator(title: str, seperator='```'):
    def _encap_text_decorator(func):
        def wrapper(*args, **kwargs):
            return encap_text(title, func(*args, **kwargs), seperator)
        return wrapper
    return _encap_text_decorator

@encap_text_with_title_decorator("CHATGPT", '\'\'\'\'\'\'')
def _ask_chatgpt(api_key: str, prompt: str, model_name: ChatGPTModel):
    resp = OpenAI(api_key=api_key).responses.create(model=model_name, input=prompt)
    return resp.output_text.strip()

@encap_text_with_title_decorator("GEMINI", '\'\'\'\'\'\'')
def _ask_gemini(api_key: str, prompt: str, model_name: GeminiModel):
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return resp.text.strip()




def _decode_aes(encoded_content, password):
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

@encap_text_with_title_decorator("CHATGPT", '\'\'\'\'\'\'')
def ask_chatgpt(prompt: str, model_name: ChatGPTModel = "gpt-4.1", password: str = None):
    chatgpt_api = load_api_key(tag="chatgpt")
    chatgpt_api = _decode_aes(chatgpt_api, password) if password is not None else chatgpt_api
    resp = OpenAI(api_key=chatgpt_api).responses.create(model=model_name, input=prompt)
    return resp.output_text.strip()

@encap_text_with_title_decorator("GEMINI", '\'\'\'\'\'\'')
def ask_gemini(prompt: str, model_name: GeminiModel = "gemini-2.5-flash-lite", password: str = None):
    gemini_api = load_api_key(tag="gemini")
    gemini_api = _decode_aes(gemini_api, password) if password else gemini_api
    client = genai.Client(api_key=gemini_api)
    resp = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return resp.text.strip()

