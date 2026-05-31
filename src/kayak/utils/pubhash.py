"""Base-62 ([0-9a-zA-Z]) encoding of the stable row id as the public URL handle.

In the metadata-single-source (v2) model the numeric ``id`` is the **stable,
author-assigned** key (the next id per table comes from ``data/db/id_counters.csv``,
not from this module), and the public URL handle is simply its base-62 encoding:
``encode(id)`` builds a link, ``decode(handle)`` recovers the id for a
``WHERE id = ?`` lookup. There is **no separate stored hash** — because the id
never changes, neither does the handle, and it is decoupled from the mutable
``name`` (a rename does not touch it).

The alphabet is **case-sensitive** (``aB`` ≠ ``ab``); URL query strings and
SQLite's default ``BINARY`` collation both preserve case. It is 1-based, so the
bare string ``"0"`` — falsy in PHP, which would break the URL handlers — is never
produced by ``encode``. ``decode`` is intentionally lenient (``decode("01") ==
decode("1")``); generated handles are always canonical (from ``encode``), so a
later phase that round-trips a *user-supplied* handle should re-``encode`` and
compare to reject non-canonical aliases.
"""

from __future__ import annotations

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BASE = len(_ALPHABET)  # 62
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def encode(n: int) -> str:
    """Encode a positive id as a base-62 handle (no leading zeros)."""
    if n < 1:
        raise ValueError(f"id handles are 1-based; got {n}")
    out: list[str] = []
    while n:
        n, r = divmod(n, _BASE)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def decode(s: str) -> int:
    """Decode a base-62 ``[0-9a-zA-Z]`` handle to its id.

    Raises ``ValueError`` on an empty string or a character outside the alphabet.
    Lenient on leading zeros (``"01"`` → 1); callers round-tripping untrusted
    handles should canonicalize via ``encode(decode(s)) == s``.
    """
    if not s:
        raise ValueError("cannot decode an empty handle")
    n = 0
    for c in s:
        try:
            n = n * _BASE + _INDEX[c]
        except KeyError:
            raise ValueError(f"invalid base-62 char {c!r} in {s!r}") from None
    return n
