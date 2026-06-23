"""Encryption and decryption strategies for secure API key storage.

Provides multiple approaches to handle API keys:
- PlainTextStrategy: Store keys directly in .env (simple)
- AESDecryptStrategy: Encrypt keys with password (better security)
- RSADecryptStrategy: Asymmetric encryption for keys (enterprise)

Uses composition pattern with KeyProvider and KeyDecryptStrategy
for flexible, extensible key management.
"""
from abc import ABC, abstractmethod
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives import padding as aes_padding
from cryptography.hazmat.backends import default_backend

from .config_loader import load_api_key

__all__ = [
    "aes_encrypt",
    "rsa_encrypt",
    "KeyProvider",
    "EnvKeyProvider",
    "KeyDecryptStrategy",
    "PlainTextStrategy",
    "AESDecryptStrategy",
    "RSADecryptStrategy",
    "KeyMaterial",
]


# =========================
# Encode Utils
# =========================

def aes_encrypt(plain_text: str, password: str) -> str:
    """AES-CBC encrypt ``plain_text`` with ``password``.

    The output (base64) is what :class:`AESDecryptStrategy` expects to find
    in your ``.env``. Use this once to produce the value you store:

        >>> aes_encrypt("sk-my-real-key", "my-password")
        'WnZ4...=='   # paste this into .env, the password goes in another tag
    """
    from hashlib import sha256

    key = sha256(password.encode("utf-8")).digest()
    iv = key[:16]

    padder = aes_padding.PKCS7(128).padder()
    padded = padder.update(plain_text.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    return base64.b64encode(encrypted).decode("utf-8")


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
    """Read a (possibly encrypted) key from the nearest ``.env`` by tag."""

    def __init__(self, tag: str):
        self.tag = tag

    def get(self) -> str:
        value = load_api_key(self.tag)
        if not value:
            raise RuntimeError(f"Missing env var: {self.tag}")
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
        pem = load_api_key(tag=self.env_password_tag)
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
    """A lazy handle to an API key: *source* (provider) + *how to decrypt* (strategy).

    Constructing a ``KeyMaterial`` touches nothing — no ``.env`` read, no
    decryption happens until you call :meth:`resolve`. That first-touch boundary
    is deliberate: build the handle anywhere, pay the cost only when you need
    the plaintext.

    For the common cases use the named constructors instead of wiring the
    provider/strategy by hand::

        KeyMaterial.plain("chatgpt").resolve()
        KeyMaterial.aes("anthropic", "AES_PASSWORD").resolve()
        KeyMaterial.rsa("gemini", "RSA_PRIVATE_KEY_PEM").resolve()

    The explicit two-argument form stays available for mixing custom providers
    or strategies::

        KeyMaterial(EnvKeyProvider("anthropic"), AESDecryptStrategy("AES_PASSWORD"))
    """

    def __init__(
        self,
        provider: KeyProvider,
        decryptor: KeyDecryptStrategy,
    ):
        self.provider = provider
        self.decryptor = decryptor

    @classmethod
    def plain(cls, tag: str) -> "KeyMaterial":
        """Plaintext key stored in ``.env`` under ``tag`` (no decryption)."""
        return cls(EnvKeyProvider(tag), PlainTextStrategy())

    @classmethod
    def aes(cls, tag: str, password_tag: str) -> "KeyMaterial":
        """AES-encrypted key in ``tag``; password read from ``.env`` tag ``password_tag``."""
        return cls(EnvKeyProvider(tag), AESDecryptStrategy(password_tag))

    @classmethod
    def rsa(cls, tag: str, private_key_tag: str) -> "KeyMaterial":
        """RSA-encrypted key in ``tag``; PEM private key read from ``.env`` tag ``private_key_tag``."""
        return cls(EnvKeyProvider(tag), RSADecryptStrategy(private_key_tag))

    def resolve(self) -> str:
        """Read the (possibly encrypted) value and return the decrypted plaintext key."""
        raw = self.provider.get()
        return self.decryptor.decrypt(raw)
