"""Context classification: split a body into typed spans for detector policy (§17)."""

from securitymasker.context.segmenter import Segment, is_code_like, segment

__all__ = ["Segment", "is_code_like", "segment"]
