"""context分類：bodyをdetector policy用のtyped spanへ分割する（§17）。"""

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
