"""store共通の``MaskingSession`` JSON codec。外側の暗号化は各storeが担う。"""

from __future__ import annotations

import base64
import json
from datetime import datetime

from securitymasker.models import AliasMapping, MaskingSession


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value)


def serialize_session(session: MaskingSession) -> str:
    mappings = [
        {
            "entity_type": mapping.entity_type,
            "alias": mapping.alias,
            "enc": _b64(mapping.encrypted_original),
            "fp": mapping.original_fingerprint,
            "profile": mapping.replacement_profile,
            "policy": mapping.restore_policy,
            "created_at": mapping.created_at.isoformat(),
            "last_used_at": mapping.last_used_at.isoformat(),
        }
        for mapping in session.mappings_by_fingerprint.values()
    ]
    return json.dumps(
        {
            "session_id": session.session_id,
            "index_key": _b64(session.session_index_key),
            "aead_key": _b64(session.aead_key),
            "tenant_id": session.tenant_id,
            "user_id": session.user_id,
            "client_type": session.client_type,
            "created_at": session.created_at.isoformat(),
            "last_used_at": session.last_used_at.isoformat(),
            "expires_at": session.expires_at.isoformat(),
            "mappings": mappings,
        },
        ensure_ascii=False,
    )


def deserialize_session(text: str) -> MaskingSession:
    doc = json.loads(text)
    session = MaskingSession(
        session_id=doc["session_id"],
        session_index_key=_unb64(doc["index_key"]),
        aead_key=_unb64(doc["aead_key"]),
        tenant_id=doc.get("tenant_id"),
        user_id=doc.get("user_id"),
        client_type=doc.get("client_type", "unknown"),
        created_at=datetime.fromisoformat(doc["created_at"]),
        last_used_at=datetime.fromisoformat(doc["last_used_at"]),
        expires_at=datetime.fromisoformat(doc["expires_at"]),
    )
    for item in doc.get("mappings", []):
        mapping = AliasMapping(
            entity_type=item["entity_type"],
            alias=item["alias"],
            encrypted_original=_unb64(item["enc"]),
            original_fingerprint=item["fp"],
            replacement_profile=item["profile"],
            restore_policy=item["policy"],
            created_at=datetime.fromisoformat(item["created_at"]),
            last_used_at=datetime.fromisoformat(item["last_used_at"]),
        )
        session.mappings_by_fingerprint[mapping.original_fingerprint] = mapping
        session.mappings_by_alias[mapping.alias] = mapping
    return session
