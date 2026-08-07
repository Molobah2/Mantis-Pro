import base64

import pytest
from Crypto.Cipher import AES

from wallet_crypto import decrypt_secret, encrypt_secret

VALID_KEY_HEX = "0" * 63 + "1"  # 64 hex chars
OTHER_KEY_HEX = "0" * 63 + "2"  # different 64 hex chars


def test_round_trip_encrypt_then_decrypt_returns_original_plaintext() -> None:
    # Arrange
    plaintext = "super-secret-session-private-key"

    # Act
    encrypted = encrypt_secret(plaintext, key_hex=VALID_KEY_HEX)
    decrypted = decrypt_secret(encrypted, key_hex=VALID_KEY_HEX)

    # Assert
    assert decrypted == plaintext


def test_round_trip_with_empty_string_plaintext() -> None:
    # Arrange / Act
    encrypted = encrypt_secret("", key_hex=VALID_KEY_HEX)
    decrypted = decrypt_secret(encrypted, key_hex=VALID_KEY_HEX)

    # Assert
    assert decrypted == ""


def test_decrypt_with_wrong_key_raises_instead_of_returning_garbage() -> None:
    # Arrange
    encrypted = encrypt_secret("top-secret", key_hex=VALID_KEY_HEX)

    # Act / Assert
    with pytest.raises(ValueError):
        decrypt_secret(encrypted, key_hex=OTHER_KEY_HEX)


@pytest.mark.parametrize("bad_key", ["", "abc", "z" * 64, "0" * 63, "0" * 65])
def test_encrypt_raises_value_error_on_invalid_key_length(bad_key: str) -> None:
    with pytest.raises(ValueError):
        encrypt_secret("plaintext", key_hex=bad_key)


@pytest.mark.parametrize("bad_key", ["", "abc", "z" * 64, "0" * 63, "0" * 65])
def test_decrypt_raises_value_error_on_invalid_key_length(bad_key: str) -> None:
    encrypted = encrypt_secret("plaintext", key_hex=VALID_KEY_HEX)
    with pytest.raises(ValueError):
        decrypt_secret(encrypted, key_hex=bad_key)


def test_decrypt_tampered_ciphertext_raises_rather_than_returning_wrong_plaintext() -> None:
    # Arrange
    encrypted = encrypt_secret("do-not-tamper-with-me", key_hex=VALID_KEY_HEX)
    raw = bytearray(base64.b64decode(encrypted))
    # Flip a byte in the ciphertext portion (after nonce[16] + tag[16])
    raw[32] ^= 0xFF
    tampered = base64.b64encode(bytes(raw)).decode()

    # Act / Assert
    with pytest.raises(ValueError):
        decrypt_secret(tampered, key_hex=VALID_KEY_HEX)


def test_encrypt_reads_key_from_env_var_when_not_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv("SESSION_KEY_ENCRYPTION_KEY", VALID_KEY_HEX)

    # Act
    encrypted = encrypt_secret("env-key-plaintext")
    decrypted = decrypt_secret(encrypted)

    # Assert
    assert decrypted == "env-key-plaintext"


def test_encrypt_raises_value_error_when_env_var_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.delenv("SESSION_KEY_ENCRYPTION_KEY", raising=False)

    # Act / Assert
    with pytest.raises(ValueError):
        encrypt_secret("plaintext")


def test_encrypted_output_format_matches_nonce_tag_ciphertext_layout() -> None:
    # Arrange / Act
    encrypted = encrypt_secret("format-check", key_hex=VALID_KEY_HEX)
    raw = base64.b64decode(encrypted)

    # Assert: nonce[16] + tag[16] + ciphertext, and it decrypts manually via raw AES-EAX
    nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
    cipher = AES.new(bytes.fromhex(VALID_KEY_HEX), AES.MODE_EAX, nonce=nonce)
    assert cipher.decrypt_and_verify(ct, tag).decode() == "format-check"
