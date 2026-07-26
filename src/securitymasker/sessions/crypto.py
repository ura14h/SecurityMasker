"""session単位の暗号処理。

- Session keys come from a CSPRNG (``secrets.token_bytes``), never derived from
  the session id.
- Fingerprints use HMAC-SHA256 keyed by the session's index key, with the entity
  type and replacement profile mixed in, so the same surface value in different
  sessions (or as different entity types) yields different fingerprints and never
  a plain SHA-256 that could be dictionary-attacked.
- Originals are sealed with AES-256-GCM (authenticated). The fingerprint is bound
  in as additional authenticated data (AAD) so a ciphertext cannot be moved to a
  different mapping without failing the tag check.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from securitymasker.errors import CryptoError

_KEY_BYTES = 32
_NONCE_BYTES = 12


def generate_session_keys() -> tuple[bytes, bytes]:
    """独立した256 bit CSPRNG鍵``(session_index_key, aead_key)``を返す。"""
    return secrets.token_bytes(_KEY_BYTES), secrets.token_bytes(_KEY_BYTES)


def fingerprint(
    session_index_key: bytes,
    value: str,
    entity_type: str,
    replacement_profile: str,
) -> str:
    """表層形または正規化値のsession単位で決定論的なfingerprint。

    Domain-separated by entity type and profile so the same string under two
    entity types maps to two aliases. Returns a hex digest.
    """
    msg = b"\x00".join(
        (value.encode("utf-8"), entity_type.encode("utf-8"), replacement_profile.encode("utf-8"))
    )
    return hmac.new(session_index_key, msg, hashlib.sha256).hexdigest()


def encrypt(aead_key: bytes, plaintext: str, aad: bytes = b"") -> bytes:
    """AES-256-GCMでsealする。出力は``nonce(12) || ciphertext||tag``。"""
    try:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ct = AESGCM(aead_key).encrypt(nonce, plaintext.encode("utf-8"), aad)
        return nonce + ct
    except Exception as exc:  # noqa: BLE001 - fail closed on any crypto error
        raise CryptoError("encryption failed") from exc


def decrypt(aead_key: bytes, blob: bytes, aad: bytes = b"") -> str:
    """AES-256-GCMでopenする。改竄またはkey／AAD不一致時は``CryptoError``。"""
    if len(blob) < _NONCE_BYTES + 16:
        raise CryptoError("ciphertext too short")
    nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    try:
        return AESGCM(aead_key).decrypt(nonce, ct, aad).decode("utf-8")
    except InvalidTag as exc:
        raise CryptoError("authentication tag mismatch (tampering or wrong key)") from exc
    except Exception as exc:  # noqa: BLE001
        raise CryptoError("decryption failed") from exc
