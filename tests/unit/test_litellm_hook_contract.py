"""Phase 0 compatibility guard for the LiteLLM integration contract.

Pins the exact hook signatures SecurityMasker depends on against the installed
LiteLLM (1.93.0). If a LiteLLM upgrade renames a hook or reorders its parameters,
these tests fail loudly *before* the change can silently break masking — at which
point ``src/securitymasker/integrations/litellm.py`` and ``docs/compatibility.md``
must be reviewed together. See ``doc/00-First-Order.md`` §4 and §37 (Phase 0).
"""

from __future__ import annotations

import inspect
from importlib.metadata import version

import pytest
from litellm.integrations.custom_guardrail import CustomGuardrail

from securitymasker.integrations.litellm import REQUIRED_HOOKS, SecurityMaskerCallback

# Parameter *names* (order-sensitive) recorded from litellm==1.93.0 source.
EXPECTED_PARAMS: dict[str, list[str]] = {
    "async_pre_call_hook": ["self", "user_api_key_dict", "cache", "data", "call_type"],
    "async_post_call_success_hook": ["self", "data", "user_api_key_dict", "response"],
    "async_post_call_streaming_iterator_hook": [
        "self",
        "user_api_key_dict",
        "response",
        "request_data",
    ],
    "async_post_call_failure_hook": [
        "self",
        "request_data",
        "original_exception",
        "user_api_key_dict",
        "traceback_str",
    ],
}


def test_pinned_litellm_version() -> None:
    assert version("litellm") == "1.93.0", (
        "LiteLLM version drift: update docs/compatibility.md and re-verify hooks."
    )


@pytest.mark.parametrize("hook", sorted(EXPECTED_PARAMS))
def test_custom_guardrail_hook_signature_is_stable(hook: str) -> None:
    fn = getattr(CustomGuardrail, hook, None)
    assert fn is not None, f"LiteLLM removed hook {hook!r} from CustomGuardrail"
    params = list(inspect.signature(fn).parameters)
    assert params == EXPECTED_PARAMS[hook], (
        f"LiteLLM hook signature changed for {hook}: {params}"
    )


def test_required_hooks_declared() -> None:
    assert set(REQUIRED_HOOKS) <= set(EXPECTED_PARAMS)


@pytest.mark.parametrize("hook", sorted(EXPECTED_PARAMS))
def test_callback_overrides_are_coroutine_and_match(hook: str) -> None:
    fn = getattr(SecurityMaskerCallback, hook)
    assert inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn), (
        f"{hook} must be async on SecurityMaskerCallback"
    )
    params = list(inspect.signature(fn).parameters)
    assert params == EXPECTED_PARAMS[hook], (
        f"SecurityMaskerCallback.{hook} params drifted from LiteLLM: {params}"
    )


def test_callback_is_registerable_guardrail() -> None:
    cb = SecurityMaskerCallback(guardrail_name="securitymasker")
    assert isinstance(cb, CustomGuardrail)


@pytest.mark.asyncio
async def test_noop_callback_is_transparent() -> None:
    """Phase 0: the callback must not alter requests or responses yet."""
    cb = SecurityMaskerCallback(guardrail_name="securitymasker")

    assert await cb.async_pre_call_hook(None, None, {"k": "v"}, "acompletion") is None

    sentinel = {"response": 1}
    assert await cb.async_post_call_success_hook({}, None, sentinel) is sentinel

    assert await cb.async_post_call_failure_hook({}, ValueError("x"), None) is None

    async def upstream():
        for chunk in ("a", "b", "c"):
            yield chunk

    out = [
        c
        async for c in cb.async_post_call_streaming_iterator_hook(None, upstream(), {})
    ]
    assert out == ["a", "b", "c"]
