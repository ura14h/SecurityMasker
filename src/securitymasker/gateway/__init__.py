"""Purpose-built transparent masking proxy for Codex / Claude Code (ADR-0006).

Replaces the LiteLLM integration. The proxy terminates the client request, masks
it, forwards it transparently upstream (the client's own credentials are passed
through, never stored/logged, §25), and restores the response — owning both
directions so streaming restoration works, which LiteLLM's callbacks could not do.
The masking core (engine/detectors/sessions/crypto/aliases/streaming/protocols) is
reused unchanged.
"""
