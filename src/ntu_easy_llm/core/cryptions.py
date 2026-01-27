import os
from abc import ABC, abstractmethod
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives import padding as aes_padding
from cryptography.hazmat.backends import default_backend

from src.ntu_easy_llm.core.config_loader import load_api_key


# =========================
# Encode Utils
# =========================

def rsa_encrypt(plain_text: str, public_key_pem: str) -> str:
    public_key = serialization.load_pem_public_key(
        public_key_pem.encode("utf-8")
    )

    encrypted = public_key.encrypt(
        plain_text.encode("utf-8"),
        rsa_padding.OAEP(
            mgf=rsa_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    return base64.b64encode(encrypted).decode("utf-8")

# =========================
# Decode Utils
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
        unpadder = aes_padding.PKCS7(128).unpadder()
        decrypted = unpadder.update(decrypted_padded) + unpadder.finalize()
        return decrypted.decode("utf-8")

    key, iv = _derive_key_iv(password)
    decoded_content = _aes_decrypt(encoded_content, key, iv)
    return decoded_content

# =========================
# Key Manager
# =========================

class KeyProvider(ABC):
    @abstractmethod
    def get(self) -> str:
        pass

class EnvKeyProvider(KeyProvider):
    def __init__(self, env_password_tag: str):
        self.env_password_tag = env_password_tag

    def get(self) -> str:
        value = load_api_key(self.env_password_tag)
        if not value:
            raise RuntimeError(f"Missing env var: {self.env_password_tag}")
        return value

# =========================
# Decryption Strategy
# =========================

class KeyDecryptStrategy(ABC):
    @abstractmethod
    def decrypt(self, value: str) -> str:
        pass

class PlainTextStrategy(KeyDecryptStrategy):
    def decrypt(self, value: str) -> str:
        return value

class AESDecryptStrategy(KeyDecryptStrategy):
    def __init__(self, env_password_tag: str):
        self.env_password_tag = env_password_tag

    def decrypt(self, value: str) -> str:
        password = load_api_key(tag=self.env_password_tag)
        if not password:
            raise RuntimeError(f"Missing AES password env: {self.env_password_tag}")
        return _decode_aes(value, password)

class RSADecryptStrategy(KeyDecryptStrategy):
    def __init__(self, env_password_tag: str):
        self.env_password_tag = env_password_tag

    def _load_private_key(self):
        pem = os.getenv(self.env_password_tag)
        if not pem:
            raise RuntimeError(f"Missing RSA private key env: {self.env_password_tag}")

        return serialization.load_pem_private_key(
            pem.encode("utf-8"),
            password=None,
        )

    def decrypt(self, value: str) -> str:
        private_key = self._load_private_key()

        encrypted_bytes = base64.b64decode(value)

        decrypted = private_key.decrypt(
            encrypted_bytes,
            rsa_padding.OAEP(
                mgf=rsa_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        return decrypted.decode("utf-8")

# =========================
# Composition
# =========================

class KeyMaterial:
    def __init__(
        self,
        provider: KeyProvider,
        decryptor: KeyDecryptStrategy,
    ):
        self.provider = provider
        self.decryptor = decryptor

    def resolve(self) -> str:
        raw = self.provider.get()
        return self.decryptor.decrypt(raw)
