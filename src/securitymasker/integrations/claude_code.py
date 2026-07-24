"""Claude Code integration helper (§7, §23).

Claude Code talks to the gateway via the Anthropic Messages API. Point
``ANTHROPIC_BASE_URL`` at the gateway and propagate the session header; provider
feature headers (``anthropic-beta`` …) are forwarded transparently by LiteLLM and
must not be stripped (§23). The ``securitymasker run claude`` wrapper generates the
session id.
"""

from __future__ import annotations

DEFAULT_BASE_URL = "http://127.0.0.1:4000"


def claude_code_env(base_url: str = DEFAULT_BASE_URL) -> dict[str, str]:
    """Return the environment variables to launch Claude Code against the gateway."""
    return {
        "ANTHROPIC_BASE_URL": base_url,
        # Claude Code forwards custom headers; the wrapper sets the session id.
        "ANTHROPIC_CUSTOM_HEADERS": "X-SecurityMasker-Session-ID:${SECURITYMASKER_SESSION_ID}",
    }


def claude_code_shell_snippet(base_url: str = DEFAULT_BASE_URL) -> str:
    lines = [f'export {k}="{v}"' for k, v in claude_code_env(base_url).items()]
    return "\n".join(lines) + "\n"
