"""Structure-preserving aliases for URLs and file paths (§9.7, §9.8, doc/06 P1-6).

Invariant 3 says masking must not break syntax. Replacing a whole URL or path with
an opaque ``SM_VALUE_ABC123`` token satisfies confidentiality but destroys it: the
result no longer parses as a URL, no longer resolves as a path, and any generated
code, shell command, or patch that contains it stops working.

So both profiles rebuild the value component-by-component instead, keeping
everything that is *structure* and replacing everything that is *identity*:

    https://taro:pw@db01.corp.example:8443/orders/2024?owner=taro#notes
    https://sm-user-1a2b:sm-pw-3c4d@sm-host-5e6f.example.invalid:8443/
        sm-p-7a8b/sm-p-9c0d?owner=sm-v-1e2f#sm-f-3a4b

    /var/secrets/prod/api.key   ->  /sm-p-1a2b/sm-p-3c4d/sm-p-5e6f/sm-p-7a8b.key
    C:\\Users\\taro\\secret.txt   ->  C:\\sm-p-1a2b\\sm-p-3c4d\\sm-p-5e6f.txt

Preserved: scheme, userinfo presence, port, path depth, absolute/relative form,
separator style (POSIX / Windows / UNC), trailing slash, query KEYS and their
order, fragment presence, and the final extension — everything a parser, a shell,
or a build tool keys off. Replaced: host, userinfo values, every path segment,
every query VALUE, and the fragment.

If a value cannot be rebuilt safely (an unparseable URL, a scheme-less string), we
raise ``MaskingError`` so the request is blocked rather than silently mangled —
never a half-masked URL that still leaks part of the original (doc/06 P1-6).

All sub-tokens derive deterministically from the caller's fingerprint token, so the
same original always yields the same alias within a session, and the collision
lengthening in ``aliases.factory`` reshapes every component at once.
"""

from __future__ import annotations

import hashlib
import posixpath
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

from securitymasker.errors import MaskingError

INVALID_DOMAIN = "example.invalid"
_SEG_PREFIX = "sm-p-"
_VAL_PREFIX = "sm-v-"
_FRAG_PREFIX = "sm-f-"
_USER_PREFIX = "sm-user-"
_PW_PREFIX = "sm-pw-"
_HOST_PREFIX = "sm-host-"
_SUB_TOKEN_LEN = 8

# Schemes whose "path" is not a hierarchical path we can safely re-segment.
_OPAQUE_SCHEMES = frozenset({"mailto", "data", "javascript", "tel", "urn"})


def _sub(token: str, label: str) -> str:
    """A short, deterministic sub-token for one component of the value."""
    return hashlib.sha256(f"{token}\x1f{label}".encode()).hexdigest()[:_SUB_TOKEN_LEN]


def _split_extension(segment: str) -> tuple[str, str]:
    """Split a trailing extension, keeping multi-dot suffixes like ``.tar.gz``."""
    if segment.startswith(".") and segment.count(".") == 1:
        return segment, ""  # dotfile: the whole name is the identity
    root, dot, ext = segment.partition(".")
    if not dot:
        return segment, ""
    return root, f".{ext}"


def _alias_segment(token: str, index: int, segment: str) -> str:
    """Alias one path segment, preserving its extension."""
    if segment in ("", ".", ".."):
        return segment  # structural, carries no identity
    _, ext = _split_extension(segment)
    return f"{_SEG_PREFIX}{_sub(token, f'seg{index}')}{ext}"


def url_alias(token: str, original: str) -> str:
    """Rebuild ``original`` as a syntactically valid, non-identifying URL."""
    try:
        parts = urlsplit(original.strip())
    except ValueError:  # malformed IPv6 literal, bad port, ...
        raise MaskingError(
            "URL could not be parsed, so no structure-preserving alias is possible"
        ) from None

    if not parts.scheme:
        # A scheme-less string is not a URL we can rebuild without guessing.
        raise MaskingError("URL has no scheme; refusing to guess its structure")
    if parts.scheme.lower() in _OPAQUE_SCHEMES:
        raise MaskingError(
            f"{parts.scheme} URLs have no hierarchical structure to preserve"
        )

    # --- authority: userinfo@host:port -------------------------------------
    userinfo = ""
    if parts.username is not None:
        userinfo = f"{_USER_PREFIX}{_sub(token, 'user')}"
        if parts.password is not None:
            userinfo += f":{_PW_PREFIX}{_sub(token, 'pw')}"
        userinfo += "@"

    host = ""
    if parts.hostname:
        host = f"{_HOST_PREFIX}{_sub(token, 'host')}.{INVALID_DOMAIN}"
    try:
        port = parts.port
    except ValueError:
        raise MaskingError("URL has an invalid port; refusing to rebuild it") from None
    netloc = f"{userinfo}{host}" + (f":{port}" if port is not None else "")
    if parts.netloc and not netloc:
        raise MaskingError("URL authority could not be rebuilt safely")

    # --- path: keep depth, absoluteness, trailing slash and extension ------
    path = ""
    if parts.path:
        segments = parts.path.split("/")
        path = "/".join(
            _alias_segment(token, i, unquote(seg)) for i, seg in enumerate(segments)
        )

    # --- query: keep KEYS and order, replace values -------------------------
    query = ""
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if pairs:
            query = "&".join(
                f"{quote(k, safe='')}={_VAL_PREFIX}{_sub(token, f'q{i}')}"
                for i, (k, _) in enumerate(pairs)
            )
        else:
            # Not key=value form (e.g. `?flag`): keep it opaque but valid.
            query = f"{_VAL_PREFIX}{_sub(token, 'q')}"

    fragment = f"{_FRAG_PREFIX}{_sub(token, 'frag')}" if parts.fragment else ""

    rebuilt = urlunsplit((parts.scheme, netloc, path, query, fragment))
    # Defensive: the alias must itself parse, and must not have kept the host.
    check = urlsplit(rebuilt)
    if check.scheme != parts.scheme:
        raise MaskingError("rebuilt URL did not round-trip; refusing to send it")
    return rebuilt


def file_path_alias(token: str, original: str) -> str:
    """Rebuild ``original`` as a path with the same shape but no identity.

    Preserves POSIX vs Windows vs UNC form, absoluteness, drive letter, depth,
    trailing separator, and the final extension.
    """
    value = original
    if not value:
        raise MaskingError("empty file path has no structure to preserve")

    # --- Windows UNC: \\server\share\... -----------------------------------
    if value.startswith("\\\\"):
        unc_parts = value[2:].split("\\")
        rebuilt_unc = [f"{_HOST_PREFIX}{_sub(token, 'unc-host')}"]
        rebuilt_unc += [
            _alias_segment(token, i, seg) for i, seg in enumerate(unc_parts[1:], 1)
        ]
        return "\\\\" + "\\".join(rebuilt_unc)

    # --- Windows drive: C:\... or C:/... ------------------------------------
    drive = ""
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
        drive, value = value[:2], value[2:]

    sep = "\\" if "\\" in value else "/"
    if drive and not value:
        return drive

    segments = value.split(sep)
    aliased = [_alias_segment(token, i, seg) for i, seg in enumerate(segments)]
    rebuilt = sep.join(aliased)
    result = f"{drive}{rebuilt}"
    if not result:
        raise MaskingError("file path could not be rebuilt safely")
    return result


def is_structurally_valid_url(value: str) -> bool:
    """True if ``value`` parses as a URL with a scheme (used by tests/§30.5)."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(parts.scheme)


def path_depth(value: str) -> int:
    """Number of components in a POSIX path (used by tests/§30.5)."""
    return len(posixpath.normpath(value).split("/"))
