"""protocol adapter（OpenAI Responses、Anthropic Messages）と共有walker。

Protocol-specific parsing is separated from the masking core (§3, §40-6): adapters
decide *which* fields carry user data; the engine decides *how* to mask them.
"""
