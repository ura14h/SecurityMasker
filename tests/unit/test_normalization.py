"""Normalization + offset-map tests (§30.1: Unicode NFC/NFKC, width, JP space)."""

from __future__ import annotations

from securitymasker.normalization import normalize, normalize_value


def test_nfkc_folds_fullwidth_digits_and_ascii() -> None:
    n = normalize("ＡＢＣ１２３", "nfkc")
    assert n.normalized == "ABC123"


def test_offset_map_expands_to_original_span() -> None:
    # Full-width digits are 1 codepoint each in the original; span maps back exactly.
    n = normalize("ＡＢＣ", "nfkc")
    assert n.normalized == "ABC"
    assert n.to_original_span(0, 1) == (0, 1)
    assert n.original_slice(1, 2) == "Ｂ"
    assert n.original_slice(0, 3) == "ＡＢＣ"


def test_offset_map_with_multichar_expansion() -> None:
    # '㍿' (SQUARE KABUSHIKI GAISHA) NFKC-expands to '株式会社' (4 chars from 1).
    n = normalize("㍿A", "nfkc")
    assert n.normalized.startswith("株式会社")
    # Any sub-span of the expansion maps back to the single original code point.
    assert n.to_original_span(0, 1) == (0, 1)
    assert n.to_original_span(1, 3) == (0, 1)
    # The trailing 'A' maps to original index 1.
    assert n.original_slice(len("株式会社"), len("株式会社") + 1) == "A"


def test_ideographic_space_folds_under_nfkc() -> None:
    # Full-width (ideographic) space U+3000 -> normal space under NFKC.
    assert normalize_value("山田　太郎", "nfkc") == "山田 太郎"
    # Under NFC it is preserved as a distinct surface form.
    assert normalize_value("山田　太郎", "nfc") == "山田　太郎"


def test_normalized_slice_preserves_original_surface() -> None:
    text = "会社は㍿です"
    n = normalize(text, "nfkc")
    idx = n.normalized.index("株式会社")
    # Even though detection sees '株式会社', restoration recovers the original '㍿'.
    assert n.original_slice(idx, idx + 4) == "㍿"


def test_combining_voiced_mark_composes_across_code_points() -> None:
    # か + U+3099 (combining voiced mark) must normalize to が (doc/06 P0-7);
    # per-code-point normalization would leave them separate and evade detection.
    text = "がぎ"  # -> がぎ
    n = normalize(text, "nfkc")
    assert n.normalized == "がぎ"
    # The composed が maps back to BOTH original code points (base + mark), so a
    # replacement covers the whole chunk, not just the base.
    assert n.original_slice(0, 1) == "が"


def test_decomposed_and_precomposed_normalize_equal() -> None:
    precomposed = "が"          # single code point U+304C
    decomposed = "が"     # base + combining mark
    assert normalize(precomposed, "nfkc").normalized == normalize(decomposed, "nfkc").normalized
