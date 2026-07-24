"""LiteLLM guardrail loader shim.

LiteLLM's config loader (``get_instance_fn``) resolves a guardrail's dotted path
as a *file relative to the config directory*, not as an installed module. So the
operator places this one-line shim next to their litellm config and references
``securitymasker_guardrail.SecurityMaskerCallback``; the real implementation lives
in the installed ``securitymasker`` package. See docs/compatibility.md.
"""

from securitymasker.integrations.litellm import SecurityMaskerCallback

__all__ = ["SecurityMaskerCallback"]
