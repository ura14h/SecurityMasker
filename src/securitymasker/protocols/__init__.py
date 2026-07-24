"""Protocol adapters (OpenAI Responses, Anthropic Messages) and shared walkers.

Protocol-specific parsing is separated from the masking core (§3, §40-6): adapters
decide *which* fields carry user data; the engine decides *how* to mask them.
"""
