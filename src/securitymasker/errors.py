"""Exception hierarchy. Default posture is fail-closed (§26, §40-4).

Any of these raised during request processing must prevent the original data from
reaching the upstream LLM. Messages must never carry original secret values (§25);
they carry only safe, actionable info (entity type, request id).
"""

from __future__ import annotations


class SecurityMaskerError(Exception):
    """Base class for all SecurityMasker errors."""


class ConfigError(SecurityMaskerError):
    """Invalid configuration or dictionary (detected at load/validation time)."""


class DetectionError(SecurityMaskerError):
    """A detector failed. Fail-closed: do not forward the original request."""


class MaskingError(SecurityMaskerError):
    """A value could not be safely masked (e.g. no safe replacement form)."""


class LeakageError(SecurityMaskerError):
    """Pre-send re-scan found a registered secret still present (§18 step 11)."""

    def __init__(self, entity_type: str, request_id: str | None = None) -> None:
        self.entity_type = entity_type
        self.request_id = request_id
        super().__init__(
            "SecurityMasker blocked this request because a sensitive value could "
            f"not be safely replaced. Entity type: {entity_type}."
            + (f" Request ID: {request_id}." if request_id else "")
        )


class RestoreError(SecurityMaskerError):
    """An alias could not be safely restored (e.g. mutated by the model, §24)."""


class CryptoError(SecurityMaskerError):
    """Encryption/decryption or authentication (AES-GCM tag) failure (§8)."""


class SessionError(SecurityMaskerError):
    """Session store failure or missing/expired session."""


class AliasCollisionError(SecurityMaskerError):
    """Alias collision that could not be resolved by lengthening (§7)."""
