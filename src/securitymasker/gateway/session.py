"""request headerとbodyからsessionを解決する。

一つのhandler呼出しでrequestのマスクとresponseの復元を行うため、同じturnでは一つの
sessionを共有する。turnをまたぐaliasの一貫性には安定したIDが必要なので、毎回変わる
``previous_response_id``より安定したheaderを優先する。

    1. 明示的な``X-SecurityMasker-Session-ID``
    2. Claude Codeのstable ``x-claude-code-session-id`` header
    3. 安定した``session-id``または``thread-id`` header
    4. ``previous_response_id``による既存sessionの検索
    5. 新しい一時ID

単一利用者のlocal runtimeなので、このmoduleは「どのsessionか」だけを解決する。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SESSION_HEADER = "x-securitymasker-session-id"


@dataclass(frozen=True)
class ResolvedSession:
    session_id: str
    stable: bool  # Falseなら永続的な識別子を取得できなかった一時session
    # 過去responseを参照した場合、callerがstoreのbindingから元sessionを検索する。
    previous_response_id: str | None = None


def resolve_session(
    headers: Mapping[str, str], body: dict[str, Any] | None = None
) -> ResolvedSession:
    h = {k.lower(): v for k, v in headers.items()}

    explicit = h.get(SESSION_HEADER)
    if explicit:
        return ResolvedSession(explicit, stable=True)

    claude_session = h.get("x-claude-code-session-id")
    if claude_session:
        return ResolvedSession(
            f"x-claude-code-session-id:{claude_session}", stable=True
        )

    for header in ("session-id", "thread-id"):
        value = h.get(header)
        if value:
            return ResolvedSession(f"{header}:{value}", stable=True)

    if body is not None:
        prev = body.get("previous_response_id")
        if isinstance(prev, str) and prev:
            # NOT stable on its own: the id changes every turn, so using it as the
            # session key would fork the alias table each turn. It is only a lookup
            # handle — the caller resolves it against the store's response bindings
            # and, on a hit, continues the ORIGINAL session.
            return ResolvedSession(
                f"eph:{uuid.uuid4()}", stable=False, previous_response_id=prev
            )

    return ResolvedSession(f"eph:{uuid.uuid4()}", stable=False)
