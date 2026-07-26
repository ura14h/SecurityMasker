"""URLとfile pathを構文的に壊さずマスクできることを検証する。

The alias must stay syntactically valid and structurally equivalent — a parser, a
shell, or a build tool must still be able to consume it — while carrying none of
the original identity. Values that cannot be rebuilt safely must BLOCK, never be
half-masked. Synthetic values only.
"""

from __future__ import annotations

import json
import ntpath
import posixpath
import shlex
from urllib.parse import parse_qsl, urlsplit

import pytest
from hypothesis import given
from hypothesis import strategies as st

from securitymasker.aliases.structure import file_path_alias, url_alias
from securitymasker.errors import MaskingError

TOKEN = "0123456789abcdef"

URLS = [
    "https://taro:pw@db01.corp.example:8443/orders/2024?owner=taro&x=1#notes",
    "http://internal.example.com/a/b/c.tar.gz",
    "https://[2001:db8::1]:9000/x?q=1",
    "https://example.com/",
    "https://example.com",
    "https://example.com/path/?flag",
    "ftp://files.example.org/pub/data.csv",
    "https://例え.example/ドキュメント/秘密.pdf",
]


@pytest.mark.parametrize("original", URLS)
def test_url_alias_is_still_a_valid_url(original) -> None:
    alias = url_alias(TOKEN, original)
    src, out = urlsplit(original), urlsplit(alias)
    assert out.scheme == src.scheme                     # scheme preserved
    assert out.port == src.port                         # port preserved
    assert bool(out.fragment) == bool(src.fragment)     # fragment presence
    assert bool(out.username) == bool(src.username)     # userinfo presence


@pytest.mark.parametrize("original", URLS)
def test_url_alias_preserves_path_depth_and_query_keys(original) -> None:
    alias = url_alias(TOKEN, original)
    src, out = urlsplit(original), urlsplit(alias)
    assert out.path.count("/") == src.path.count("/")   # depth + trailing slash
    if src.query and "=" in src.query:
        assert [k for k, _ in parse_qsl(out.query, keep_blank_values=True)] == \
               [k for k, _ in parse_qsl(src.query, keep_blank_values=True)]


@pytest.mark.parametrize("original", URLS)
def test_url_alias_leaks_no_original_component(original) -> None:
    alias = url_alias(TOKEN, original)
    src = urlsplit(original)
    if src.hostname:
        assert src.hostname not in alias
    if src.username:
        assert src.username not in alias
    # Only meaningful (>=3 char) segments: a 1-2 char name would collide with hex
    # sub-tokens by chance and prove nothing. Extensions are structure, so the
    # stem is what must not survive.
    for segment in src.path.split("/"):
        stem = segment.split(".")[0]
        if len(stem) >= 3:
            assert stem not in alias


def test_url_alias_preserves_extension() -> None:
    alias = url_alias(TOKEN, "http://h.example/a/report.tar.gz")
    assert alias.endswith(".tar.gz")


@pytest.mark.parametrize("bad", ["not a url", "/just/a/path", "", "   "])
def test_unrebuildable_url_blocks(bad) -> None:
    with pytest.raises(MaskingError):
        url_alias(TOKEN, bad)


@pytest.mark.parametrize("opaque", ["mailto:taro@example.com", "data:text/plain,secret",
                                    "javascript:alert(1)"])
def test_opaque_scheme_urls_block(opaque) -> None:
    # No hierarchical structure to preserve -> block instead of mangling.
    with pytest.raises(MaskingError):
        url_alias(TOKEN, opaque)


def test_url_alias_survives_json_and_shell() -> None:
    alias = url_alias(TOKEN, "https://u:p@h.example:443/a/b?k=v#f")
    assert json.loads(json.dumps({"url": alias}))["url"] == alias
    assert shlex.split(f"curl {shlex.quote(alias)}")[1] == alias


# --- file paths -----------------------------------------------------------------

PATHS_POSIX = ["/var/secrets/prod/api.key", "docs/秘密/メモ.md", "./rel/x.py",
               "/a/b/", "../up/one.txt", "/single", "name.txt"]
PATHS_WINDOWS = ["C:\\Users\\taro\\secret.txt", "D:\\data\\x\\y.csv",
                 "\\\\server\\share\\file.docx"]


@pytest.mark.parametrize("original", PATHS_POSIX)
def test_posix_path_shape_is_preserved(original) -> None:
    alias = file_path_alias(TOKEN, original)
    assert alias.startswith("/") == original.startswith("/")     # absoluteness
    assert alias.endswith("/") == original.endswith("/")         # trailing sep
    assert alias.count("/") == original.count("/")               # depth
    assert posixpath.splitext(alias)[1] == posixpath.splitext(original)[1]
    assert "\\" not in alias                                     # separator style


@pytest.mark.parametrize("original", PATHS_WINDOWS)
def test_windows_path_shape_is_preserved(original) -> None:
    alias = file_path_alias(TOKEN, original)
    assert alias.count("\\") == original.count("\\")
    assert ntpath.splitext(alias)[1] == ntpath.splitext(original)[1]
    if original.startswith("\\\\"):
        # UNC stays UNC, but server/share are identity and MUST be replaced.
        assert alias.startswith("\\\\")
        assert "server" not in alias and "share" not in alias
    else:
        # A drive letter is structure (it selects a volume), so it is preserved.
        assert ntpath.splitdrive(alias)[0] == ntpath.splitdrive(original)[0]


@pytest.mark.parametrize("original", PATHS_POSIX + PATHS_WINDOWS)
def test_path_alias_leaks_no_segment(original) -> None:
    alias = file_path_alias(TOKEN, original)
    # Drive letters are deliberately preserved structure; check the rest.
    body = original[2:] if ntpath.splitdrive(original)[0] and original[1:2] == ":" else original
    for sep in ("/", "\\"):
        for segment in body.split(sep):
            stem = segment.split(".")[0]
            # >=3 chars only: shorter names collide with hex sub-tokens by chance.
            if len(stem) >= 3 and stem not in (".", ".."):
                assert stem not in alias


def test_empty_path_blocks() -> None:
    with pytest.raises(MaskingError):
        file_path_alias(TOKEN, "")


def test_path_alias_survives_shell_quoting() -> None:
    alias = file_path_alias(TOKEN, "/var/データ/secret file.txt")
    assert shlex.split(f"cat {shlex.quote(alias)}")[1] == alias


# --- property tests --------------------------------------------------------------


@given(st.lists(st.text(alphabet=st.characters(blacklist_characters="/\\\x00"),
                        min_size=1, max_size=12), min_size=1, max_size=6))
def test_any_posix_path_keeps_depth_and_is_relative_or_absolute(segments) -> None:
    original = "/" + "/".join(segments)
    alias = file_path_alias(TOKEN, original)
    assert alias.startswith("/")
    assert alias.count("/") == original.count("/")


@given(st.integers(min_value=0, max_value=6))
def test_url_path_depth_is_preserved_for_any_depth(depth) -> None:
    original = "https://h.example" + "".join(f"/seg{i}" for i in range(depth))
    alias = url_alias(TOKEN, original)
    assert urlsplit(alias).path.count("/") == urlsplit(original).path.count("/")


def test_same_original_same_alias_and_different_originals_differ() -> None:
    a1 = url_alias(TOKEN, "https://h.example/a")
    a2 = url_alias(TOKEN, "https://h.example/a")
    assert a1 == a2                                   # deterministic
    assert url_alias("ffff0000", "https://h.example/a") != a1   # token-dependent
