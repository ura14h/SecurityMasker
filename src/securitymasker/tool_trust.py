"""tool argument復元のtrust policy（docs/security/threat-model.md）。

Response *text* is restored for display to the user, which is safe. Tool
*arguments*, by contrast, are handed to a tool for execution — if that tool is an
external MCP server or a provider-hosted tool, restoring the real secret into its
arguments leaks it into an untrusted execution environment. So tool-argument
restoration is gated: by default nothing is trusted (arguments keep their aliases),
and only tools on an explicit local allowlist get their arguments restored to real
values. When the tool name cannot be determined, we fail safe and do not restore.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolTrustPolicy:
    trusted_local_tools: frozenset[str] = frozenset()

    def restores_arguments(self, tool_name: str | None) -> bool:
        """明示的にallowlist登録したlocal toolだけTrue。それ以外は安全側に倒す。"""
        return bool(tool_name) and tool_name in self.trusted_local_tools
