"""Tests for the base-62 public-hash sequence helper."""

from __future__ import annotations

import pytest

from kayak.utils.pubhash import decode, encode, next_hash


def test_encode_known_values():
    assert encode(1) == "1"
    assert encode(9) == "9"
    assert encode(10) == "a"
    assert encode(35) == "z"
    assert encode(36) == "A"
    assert encode(61) == "Z"
    assert encode(62) == "10"
    assert encode(318) == "58"  # ~ a source-table count


def test_case_sensitive_alphabet():
    # lower and upper are distinct values — the whole point of base-62.
    assert encode(10) == "a" and encode(36) == "A"
    assert decode("a") == 10 and decode("A") == 36
    assert decode("aB") != decode("ab")


def test_encode_never_emits_bare_zero():
    vals = {encode(n) for n in range(1, 4000)}
    assert "0" not in vals


def test_encode_rejects_non_positive():
    with pytest.raises(ValueError):
        encode(0)
    with pytest.raises(ValueError):
        encode(-1)


def test_decode_round_trips():
    for n in (1, 9, 10, 35, 36, 61, 62, 318, 3843, 3844, 999999):
        assert decode(encode(n)) == n


def test_decode_rejects_bad_input():
    with pytest.raises(ValueError):
        decode("")
    with pytest.raises(ValueError):
        decode("-")  # outside [0-9a-zA-Z]
    with pytest.raises(ValueError):
        decode("a b")


def test_next_hash_is_monotonic_max_plus_one():
    assert next_hash([]) == "1"
    assert next_hash(["1", "2", "9"]) == "a"
    assert next_hash(["z"]) == "A"  # base-62: after z comes A
    assert next_hash(["Z"]) == "10"
    assert next_hash(["a", "Z", "5"]) == "10"
    # tolerates blanks / out-of-order
    assert next_hash(["", "1", "", "3"]) == "4"
