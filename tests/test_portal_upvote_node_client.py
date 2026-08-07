import base64
import json
import time

import pytest
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from portal_upvote import config as _cfg
from portal_upvote.node_client import _decrypt_user_session, _load_session, restore_from_env

VALID_KEY_HEX = "a" * 64


def _old_style_encrypt(plaintext: str, key_hex: str) -> str:
    """Build ciphertext exactly the way the pre-refactor inline AES code did,
    independent of wallet_crypto.encrypt_secret, to prove backward compatibility
    with values already stored in the production DB / session file."""
    nonce = get_random_bytes(16)
    cipher = AES.new(bytes.fromhex(key_hex), AES.MODE_EAX, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode())
    return base64.b64encode(nonce + tag + ciphertext).decode()


def test_decrypt_user_session_backward_compatible_with_old_inline_aes_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: hand-construct ciphertext the OLD way (not via wallet_crypto.encrypt_secret)
    monkeypatch.setenv("SESSION_KEY_ENCRYPTION_KEY", VALID_KEY_HEX)
    plaintext = "0xdeadbeef-old-session-private-key"
    encrypted_key = _old_style_encrypt(plaintext, VALID_KEY_HEX)
    user = {"encrypted_key": encrypted_key, "session_config": '{"foo":"bar"}', "address": "0xAbC123"}

    # Act
    session_priv, session_config, address = _decrypt_user_session(user)

    # Assert: still decrypts correctly after the refactor to wallet_crypto
    assert session_priv == plaintext
    assert session_config == '{"foo":"bar"}'
    assert address == "0xAbC123"


def test_decrypt_user_session_raises_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_KEY_ENCRYPTION_KEY", raising=False)
    user = {"encrypted_key": "irrelevant", "session_config": "{}", "address": "0xAbC"}

    with pytest.raises(RuntimeError, match="SESSION_KEY_ENCRYPTION_KEY not set or invalid"):
        _decrypt_user_session(user)


def test_decrypt_user_session_raises_value_error_on_tampered_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The MAC-verification failure must bubble up unmodified (not miscategorized
    # as a "key not set" RuntimeError) — mirrors original code's behavior.
    monkeypatch.setenv("SESSION_KEY_ENCRYPTION_KEY", VALID_KEY_HEX)
    encrypted_key = _old_style_encrypt("valid-plaintext", VALID_KEY_HEX)
    raw = bytearray(base64.b64decode(encrypted_key))
    raw[32] ^= 0xFF  # flip a ciphertext byte
    tampered = base64.b64encode(bytes(raw)).decode()
    user = {"encrypted_key": tampered, "session_config": "{}", "address": "0xAbC"}

    with pytest.raises(ValueError):
        _decrypt_user_session(user)


def test_load_session_backward_compatible_with_old_inline_aes_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "object"
) -> None:
    # Arrange
    monkeypatch.setenv("SESSION_KEY_ENCRYPTION_KEY", VALID_KEY_HEX)
    session_file = tmp_path / ".upvote_session"
    plaintext = "0xowner-session-private-key"
    encrypted_key = _old_style_encrypt(plaintext, VALID_KEY_HEX)
    session_file.write_text(
        json.dumps(
            {
                "encrypted_key": encrypted_key,
                "session_config": "{}",
                "agw_address": "0xOwnerAddr",
            }
        )
    )
    monkeypatch.setattr(_cfg, "SESSION_FILE", str(session_file))

    # Act
    session_priv, session_config, agw_address = _load_session()

    # Assert
    assert session_priv == plaintext
    assert agw_address == "0xOwnerAddr"


def test_load_session_raises_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_KEY_ENCRYPTION_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SESSION_KEY_ENCRYPTION_KEY not set or invalid"):
        _load_session()


def test_load_session_raises_when_no_session_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "object"
) -> None:
    monkeypatch.setenv("SESSION_KEY_ENCRYPTION_KEY", VALID_KEY_HEX)
    monkeypatch.setattr(_cfg, "SESSION_FILE", str(tmp_path / "does_not_exist.json"))

    with pytest.raises(RuntimeError, match="No session file found"):
        _load_session()


# ── restore_from_env ─────────────────────────────────────────────────
# This is the exact function that regressed silently during the
# wallet_crypto refactor (a dropped `import base64` broke it while a broad
# `except Exception` swallowed the resulting NameError) — it had zero test
# coverage before, which is why the bug wasn't caught immediately.

def test_restore_from_env_writes_session_file_from_valid_b64(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "object"
) -> None:
    session_file = tmp_path / ".upvote_session"
    monkeypatch.setattr(_cfg, "SESSION_FILE", str(session_file))
    payload = {
        "encrypted_key": "irrelevant-for-this-test",
        "session_config": "{}",
        "agw_address": "0xOwnerAddr",
        "expires_at": time.time() + 3600,
    }
    b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    monkeypatch.setenv("UPVOTE_SESSION_B64", b64)

    result = restore_from_env()

    assert result is True
    assert session_file.exists()
    written = json.loads(session_file.read_text())
    assert written["agw_address"] == "0xOwnerAddr"


def test_restore_from_env_returns_false_when_b64_expired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "object"
) -> None:
    session_file = tmp_path / ".upvote_session"
    monkeypatch.setattr(_cfg, "SESSION_FILE", str(session_file))
    payload = {"encrypted_key": "x", "session_config": "{}", "expires_at": time.time() - 3600}
    b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    monkeypatch.setenv("UPVOTE_SESSION_B64", b64)

    result = restore_from_env()

    assert result is False
    assert not session_file.exists()


def test_restore_from_env_returns_false_when_no_env_var_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "object"
) -> None:
    monkeypatch.setattr(_cfg, "SESSION_FILE", str(tmp_path / "does_not_exist.json"))
    monkeypatch.delenv("UPVOTE_SESSION_B64", raising=False)

    assert restore_from_env() is False


def test_restore_from_env_returns_true_when_valid_session_file_already_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: "object"
) -> None:
    session_file = tmp_path / ".upvote_session"
    session_file.write_text(json.dumps({"expires_at": time.time() + 3600}))
    monkeypatch.setattr(_cfg, "SESSION_FILE", str(session_file))

    assert restore_from_env() is True
