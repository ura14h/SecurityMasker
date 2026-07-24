"""LiteLLM integration adapter (thin, version-pinned).

This is the *only* module that imports LiteLLM. It maps LiteLLM's callback /
guardrail hooks onto the SecurityMasker core. Keeping the coupling here means a
LiteLLM hook rename or signature change is a one-file fix (AGENTS.md §2 rule 5).

Verified against **litellm==1.93.0**. The exact hook signatures are pinned by
``tests/unit/test_litellm_hook_contract.py``; if that test fails after a LiteLLM
upgrade, review the changes here before bumping the pin in pyproject.toml.

Phase 0 status: this callback is a **no-op pass-through**. It proves the hooks
load and keep their contract. Phase 1+ wires in the masking engine at each hook.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail

# Hook names we depend on, kept as a module constant so the contract test and the
# implementation cannot drift apart.
REQUIRED_HOOKS: tuple[str, ...] = (
    "async_pre_call_hook",
    "async_post_call_success_hook",
    "async_post_call_streaming_iterator_hook",
    "async_post_call_failure_hook",
)


class SecurityMaskerCallback(CustomGuardrail):
    """LiteLLM guardrail entry point for SecurityMasker.

    Registered in the proxy config under ``guardrails`` (see
    ``config/litellm.example.yaml``). When SecurityMasker is not registered, the
    proxy behaves as vanilla LiteLLM (acceptance criterion §38-17).
    """

    def __init__(self, **kwargs: Any) -> None:
        # ``guardrail_name`` / ``event_hook`` are injected by the proxy loader.
        super().__init__(**kwargs)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: Any,
    ) -> Exception | str | dict[str, Any] | None:
        """Mask the outbound request just before the LLM call.

        Phase 0: pass-through. Phase 1+: run the detection/masking pipeline on the
        request structure (``doc/00-First-Order.md`` §18) and fail closed on error.
        """
        return None

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: Any,
        response: Any,
    ) -> Any:
        """Restore aliases in a non-streaming response (§19). Phase 0: pass-through."""
        return response

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,
        response: Any,
        request_data: dict[str, Any],
    ) -> AsyncGenerator[Any, None]:
        """Real-time alias restoration over the SSE stream (§20-21).

        Phase 0: transparently re-yield every chunk unchanged.
        """
        async for chunk in response:
            yield chunk

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> None:
        """Ensure failures never leak original secrets (§25, §26). Phase 0: no-op."""
        return None
