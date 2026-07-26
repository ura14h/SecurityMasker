"""HMACのentity分離と暗号文改ざん検出を検証する。"""

from __future__ import annotations

import pytest

from securitymasker.errors import CryptoError
from securitymasker.sessions.crypto import (
    decrypt,
    encrypt,
    fingerprint,
    generate_session_keys,
)


def test_session_keys_are_random_and_independent() -> None:
    idx1, aead1 = generate_session_keys()
    idx2, aead2 = generate_session_keys()
    assert idx1 != idx2 and aead1 != aead2 and idx1 != aead1
    assert len(idx1) == 32 and len(aead1) == 32


def test_fingerprint_is_deterministic_per_key() -> None:
    key, _ = generate_session_keys()
    a = fingerprint(key, "山田太郎", "PERSON", "prose_identifier")
    b = fingerprint(key, "山田太郎", "PERSON", "prose_identifier")
    assert a == b


def test_fingerprint_separated_by_entity_type() -> None:
    key, _ = generate_session_keys()
    as_person = fingerprint(key, "さくら", "PERSON", "prose_identifier")
    as_project = fingerprint(key, "さくら", "PROJECT_NAME", "prose_identifier")
    assert as_person != as_project


def test_fingerprint_differs_across_sessions() -> None:
    k1, _ = generate_session_keys()
    k2, _ = generate_session_keys()
    assert fingerprint(k1, "x", "PERSON", "p") != fingerprint(k2, "x", "PERSON", "p")


def test_encrypt_decrypt_roundtrip() -> None:
    _, aead = generate_session_keys()
    blob = encrypt(aead, "秘密の値", aad=b"fp")
    assert decrypt(aead, blob, aad=b"fp") == "秘密の値"


def test_decrypt_detects_tampering() -> None:
    _, aead = generate_session_keys()
    blob = bytearray(encrypt(aead, "secret", aad=b"fp"))
    blob[-1] ^= 0x01  # flip a ciphertext/tag bit
    with pytest.raises(CryptoError):
        decrypt(aead, bytes(blob), aad=b"fp")


def test_decrypt_rejects_wrong_aad() -> None:
    _, aead = generate_session_keys()
    blob = encrypt(aead, "secret", aad=b"fp-a")
    with pytest.raises(CryptoError):
        decrypt(aead, blob, aad=b"fp-b")


def test_decrypt_rejects_wrong_key() -> None:
    _, aead1 = generate_session_keys()
    _, aead2 = generate_session_keys()
    blob = encrypt(aead1, "secret")
    with pytest.raises(CryptoError):
        decrypt(aead2, blob)
