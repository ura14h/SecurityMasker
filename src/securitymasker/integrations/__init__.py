"""Client integration helpers for Codex and Claude Code (ADR-0006).

Config/env helpers that point Codex and Claude Code at the SecurityMasker proxy
(``securitymasker.gateway``). No LiteLLM: the proxy is a purpose-built transparent
masking gateway.
"""
