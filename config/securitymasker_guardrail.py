"""LiteLLM guardrail loader shim (place next to your litellm config).

LiteLLM's config loader resolves a guardrail's dotted path as a *file relative to
the config directory*, not as an installed module (see docs/compatibility.md). So
this one-line shim sits beside the config and re-exports the real implementation
from the installed ``securitymasker`` package. Reference it in the config as
``securitymasker_guardrail.SecurityMaskerCallback``.
"""

from securitymasker.integrations.litellm import SecurityMaskerCallback

__all__ = ["SecurityMaskerCallback"]
