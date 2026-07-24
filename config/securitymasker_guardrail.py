"""LiteLLM loader shim (place next to your litellm config).

LiteLLM resolves a callback/guardrail dotted path as a *file relative to the config
directory*, not an installed module (docs/compatibility.md). This shim re-exports
the real implementation from the installed ``securitymasker`` package.

- As a **callback** (``litellm_settings.callbacks:``) — recommended, this runs the
  full mask+restore lifecycle (pre_call, post_call, streaming) — LiteLLM uses the
  object directly, so reference the pre-built instance
  ``securitymasker_guardrail.securitymasker_callback``.
- As a **guardrail** (``guardrails:``) LiteLLM instantiates the class and only fires
  the mode-gated hook, so reference ``securitymasker_guardrail.SecurityMaskerCallback``.
"""

from securitymasker.integrations.litellm import SecurityMaskerCallback

securitymasker_callback = SecurityMaskerCallback()

__all__ = ["SecurityMaskerCallback", "securitymasker_callback"]
