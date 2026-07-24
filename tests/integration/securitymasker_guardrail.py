"""LiteLLM loader shim (place next to your litellm config).

LiteLLM resolves a callback/guardrail dotted path as a *file relative to the config
directory*, not an installed module (docs/compatibility.md). This shim re-exports
the real implementation from the installed ``securitymasker`` package.

- As a **guardrail** (``guardrails:``), LiteLLM instantiates the class, so reference
  ``securitymasker_guardrail.SecurityMaskerCallback``.
- As a **callback** (``litellm_settings.callbacks:``), LiteLLM uses the object
  directly, so reference the pre-built instance
  ``securitymasker_guardrail.securitymasker_callback``.
"""

from securitymasker.integrations.litellm import SecurityMaskerCallback

# Instance for the callbacks registration path (pre_call + post_call + streaming).
securitymasker_callback = SecurityMaskerCallback()

__all__ = ["SecurityMaskerCallback", "securitymasker_callback"]
