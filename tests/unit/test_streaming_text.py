"""全境界で分割されたaliasをstreamから復元できることを検証する。"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from securitymasker.streaming.text_replacer import StreamingRestorer

REPL = {
    "SM_ORG_7F3A91": "株式会社極秘技研",
    "sm-host-7f3a91.example.invalid": "prod-db01.internal.example",
    "SM_PERSON_2B891C": "山田太郎",
}


def _stream(text: str, chunks: list[str]) -> str:
    r = StreamingRestorer(REPL)
    out = [r.feed(c) for c in chunks]
    out.append(r.flush())
    return "".join(out)


def _split_at(text: str, i: int) -> list[str]:
    return [text[:i], text[i:]]


def test_no_split_replaces_all() -> None:
    text = "接続先は sm-host-7f3a91.example.invalid、担当は SM_PERSON_2B891C です"
    assert _stream(text, [text]) == "接続先は prod-db01.internal.example、担当は 山田太郎 です"


def test_alias_split_at_every_boundary() -> None:
    alias = "sm-host-7f3a91.example.invalid"
    text = f"connect {alias} now"
    expected = "connect prod-db01.internal.example now"
    for i in range(len(text) + 1):
        assert _stream(text, _split_at(text, i)) == expected, f"split at {i}"


def test_consecutive_aliases() -> None:
    text = "SM_ORG_7F3A91SM_PERSON_2B891C"
    expected = "株式会社極秘技研山田太郎"
    for i in range(len(text) + 1):
        assert _stream(text, _split_at(text, i)) == expected


def test_alias_prefix_only_at_stream_end_is_emitted_verbatim() -> None:
    # A dangling partial that never completes must be flushed as-is (not dropped).
    r = StreamingRestorer(REPL)
    out = r.feed("value SM_ORG_7F") + r.flush()
    assert out == "value SM_ORG_7F"


def test_char_by_char_streaming() -> None:
    text = "x SM_ORG_7F3A91 y sm-host-7f3a91.example.invalid z"
    expected = "x 株式会社極秘技研 y prod-db01.internal.example z"
    assert _stream(text, list(text)) == expected


def test_multibyte_japanese_not_broken() -> None:
    text = "日本語のSM_PERSON_2B891Cさん、住所は東京"
    expected = "日本語の山田太郎さん、住所は東京"
    for i in range(len(text) + 1):
        assert _stream(text, _split_at(text, i)) == expected


def test_empty_vocabulary_is_passthrough() -> None:
    r = StreamingRestorer({})
    assert r.feed("anything SM_ORG_7F3A91") + r.flush() == "anything SM_ORG_7F3A91"


@given(st.lists(st.integers(min_value=1, max_value=6), min_size=1, max_size=40))
def test_property_arbitrary_chunking_roundtrip(sizes: list[int]) -> None:
    text = "pre SM_ORG_7F3A91 mid sm-host-7f3a91.example.invalid end SM_PERSON_2B891C!"
    expected = "pre 株式会社極秘技研 mid prod-db01.internal.example end 山田太郎!"
    chunks: list[str] = []
    pos = 0
    for s in sizes:
        chunks.append(text[pos : pos + s])
        pos += s
    chunks.append(text[pos:])
    assert _stream(text, chunks) == expected
