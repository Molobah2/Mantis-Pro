import base64
import os

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

_KEY_ERROR = (
    "SESSION_KEY_ENCRYPTION_KEY not set or invalid (need 64-char hex). "
    "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
)


def _resolve_key(key_hex: str | None) -> bytes:
    """Resolve and validate the AES key, falling back to the env var when not passed."""
    resolved = key_hex if key_hex is not None else os.getenv("SESSION_KEY_ENCRYPTION_KEY", "")
    if len(resolved) != 64:
        raise ValueError(_KEY_ERROR)
    return bytes.fromhex(resolved)


def encrypt_secret(plaintext: str, key_hex: str | None = None) -> str:
    """AES-EAX encrypt plaintext, returning base64(nonce[16] + tag[16] + ciphertext)."""
    key = _resolve_key(key_hex)
    nonce = get_random_bytes(16)
    cipher = AES.new(key, AES.MODE_EAX, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode())
    return base64.b64encode(nonce + tag + ciphertext).decode()


def decrypt_secret(encrypted_b64: str, key_hex: str | None = None) -> str:
    """Inverse of encrypt_secret: decode base64(nonce[16] + tag[16] + ciphertext) and decrypt."""
    key = _resolve_key(key_hex)
    raw = base64.b64decode(encrypted_b64)
    nonce, tag, ciphertext = raw[:16], raw[16:32], raw[32:]
    cipher = AES.new(key, AES.MODE_EAX, nonce=nonce)
    return cipher.decrypt_and_verify(ciphertext, tag).decode()
