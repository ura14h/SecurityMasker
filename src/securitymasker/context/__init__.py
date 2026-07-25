"""Context classification: split a body into typed spans for detector policy (§17)."""

from securitymasker.context.segmenter import (
    MAX_CLAIMS,
    MAX_SEGMENTS,
    Segment,
    SegmentationLimitError,
    coalesce_for_detection,
    is_code_like,
    segment,
)

__all__ = [
    "MAX_CLAIMS",
    "MAX_SEGMENTS",
    "Segment",
    "SegmentationLimitError",
    "coalesce_for_detection",
    "is_code_like",
    "segment",
]
