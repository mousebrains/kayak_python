"""Base-62 ([0-9a-zA-Z]) sequence encoding for the stable public ``hash`` handle.

``source`` / ``gauge`` / ``reach`` carry an immutable ``hash`` — a per-table
base-62 counter (``1, 2, …, 9, a, …, z, A, …, Z, 10, …``) that survives the
*ephemeral* numeric row id, so bookmarked URLs and custom-page lists keep working
across a metadata rebuild (the rebuild reassigns ids freely; the hash, stored in
the CSV, never changes). Assignment is monotonic: a new row's hash is the table's
current max, decoded, ``+ 1``, re-encoded.

The sequence starts at 1, so the bare string ``"0"`` is never produced — it is
falsy as a PHP string and would break the URL handlers. The alphabet is
**case-sensitive** (``aB`` ≠ ``ab``); URL query strings preserve case and SQLite's
default ``BINARY`` collation compares case-sensitively, so the ``hash`` column /
unique index MUST NOT use ``COLLATE NOCASE``.
"""

from __future__ import annotations

from collections.abc import Iterable

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_BASE = len(_ALPHABET)  # 62
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def encode(n: int) -> str:
    """Encode a positive integer as a base-62 string (no leading zeros)."""
    if n < 1:
        raise ValueError(f"hash sequence is 1-based; got {n}")
    out: list[str] = []
    while n:
        n, r = divmod(n, _BASE)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def decode(s: str) -> int:
    """Decode a base-62 ``[0-9a-zA-Z]`` string to an integer.

    Raises ``ValueError`` on an empty string or a character outside the alphabet.
    """
    if not s:
        raise ValueError("cannot decode an empty hash")
    n = 0
    for c in s:
        try:
            n = n * _BASE + _INDEX[c]
        except KeyError:
            raise ValueError(f"invalid base-62 char {c!r} in {s!r}") from None
    return n


def next_hash(existing: Iterable[str]) -> str:
    """The next hash after the max of *existing* (max decoded + 1; 1 if empty)."""
    hi = max((decode(h) for h in existing if h), default=0)
    return encode(hi + 1)
