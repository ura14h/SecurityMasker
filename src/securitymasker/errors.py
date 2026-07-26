"""例外階層。既定動作はfail-closed（§26、§40-4）。

Any of these raised during request processing must prevent the original data from
reaching the upstream LLM. Messages must never carry original secret values (§25);
they carry only safe, actionable info (entity type, request id).
"""

from __future__ import annotations


class SecurityMaskerError(Exception):
    """すべてのSecurityMasker errorの基底class。"""


class ConfigError(SecurityMaskerError):
    """load／validation時に検出した不正な設定またはdictionary。"""


class DetectionError(SecurityMaskerError):
    """detectorの失敗。元のrequestを転送せずfail-closedにする。"""


class MaskingError(SecurityMaskerError):
    """安全にマスクできない値（安全な置換形式がない場合など）。"""


class LeakageError(SecurityMaskerError):
    """送信前の再scanで登録済みsecretの残存を検出した（§18 step 11）。"""

    def __init__(self, entity_type: str, request_id: str | None = None) -> None:
        self.entity_type = entity_type
        self.request_id = request_id
        super().__init__(
            "SecurityMasker blocked this request because a sensitive value could "
            f"not be safely replaced. Entity type: {entity_type}."
            + (f" Request ID: {request_id}." if request_id else "")
        )


class RestoreError(SecurityMaskerError):
    """modelによる変形などでaliasを安全に復元できない（§24）。"""


class CryptoError(SecurityMaskerError):
    """暗号化／復号または認証（AES-GCM tag）の失敗（§8）。"""


class SessionError(SecurityMaskerError):
    """session storeの失敗、またはsessionの欠落／期限切れ。"""


class AliasCollisionError(SecurityMaskerError):
    """token延長でも解消できなかったalias collision（§7）。"""
