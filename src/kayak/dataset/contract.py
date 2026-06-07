"""Dataset contract — the versioned manifest layer on top of the file/column
descriptor in :mod:`kayak.dataset.layout` (S6.2).

``dataset.yaml`` at the dataset root declares which contract the dataset was
authored against. The engine declares a supported contract range and refuses a
dataset it can't read: a dataset with no ``dataset.yaml`` is **contract 0**
(legacy) and is rejected by commands that require contract 1+. This module is
the single source of truth for the version range and the ``dataset.yaml`` field
contract, shared by ``validate-dataset`` (and later ``init-dataset`` / sync), so
they don't each re-spell the rules.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml

# The contract version this engine authors. Bump (and widen MAX_CONTRACT) when
# the dataset shape changes in a way that needs an upgrade transform.
CONTRACT_VERSION = 1
# Inclusive range of dataset contract versions this engine can read.
MIN_CONTRACT = 1
MAX_CONTRACT = 1

DATASET_YAML = "dataset.yaml"

# Sidecar recording purged stable ids per id-bearing table (S6.3). Kept so a
# deleted row's id is never reused and the id counter stays above it — the
# value-domain + cross-checks live in ``cli.validate_dataset``; this module owns
# only the strict parse (shared loader, dup-key rejection).
RETIRED_IDS_YAML = "retired_ids.yaml"

STATUSES: tuple[str, ...] = ("scaffold", "publishable")

# The complete key set for contract 1. The manifest is the contract gate, so —
# like the rest of the validator (an unexpected CSV is an error; a dataset is a
# "complete projection") — an unknown key is rejected rather than silently
# tolerated, catching a stray ``licence:``/``Status:`` typo. A future contract
# version relaxes this deliberately by widening the set.
KNOWN_KEYS: frozenset[str] = frozenset(
    {"contract_version", "dataset_id", "name", "status", "license", "engine_test_ref", "provenance"}
)

# A full SHA-1 git commit: 40 lowercase hex. Format-only here; whether the
# commit actually exists in the approved engine repo is an S7 paired-release
# concern (it needs a network/git lookup), not a dataset-integrity check.
_ENGINE_REF_RE = re.compile(r"^[0-9a-f]{40}$")


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    PyYAML's default last-wins on a duplicated key would let a manifest like
    ``contract_version: 99`` / ``contract_version: 1`` slip a bad value past the
    contract gate. Reject it as malformed — mirrors the JSON sidecar's
    ``_no_dup_pairs`` strictness in ``cli.validate_dataset``.
    """


def _no_duplicate_mapping(loader: _StrictSafeLoader, node: yaml.MappingNode) -> dict[str, Any]:
    # dict[Any, Any]: YAML may hand back non-str keys (e.g. ``5: x``) which the
    # callers reject downstream as unknown/not-id-bearing; the str-keyed contract
    # is the return annotation, not an in-loop invariant.
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        # PyYAML's default constructor guards key hashability before the dict
        # insert; this override must too. Without it an unhashable key (a YAML
        # complex key like ``? [1, 2]``) raises a raw TypeError from ``key in
        # mapping`` that escapes the loaders' YAMLError handling and crashes the
        # caller — violating validate_dataset's crash-safe contract. Re-raise as
        # a ConstructorError so it surfaces as the loaders' "invalid YAML".
        if not isinstance(key, Hashable):
            raise yaml.constructor.ConstructorError(
                None, None, f"unhashable key {key!r}", key_node.start_mark
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_mapping
)


def supported_range_str() -> str:
    """Human-readable supported-contract range for error messages."""
    if MIN_CONTRACT == MAX_CONTRACT:
        return str(MIN_CONTRACT)
    return f"{MIN_CONTRACT}-{MAX_CONTRACT}"


def load_dataset_meta(dataset_dir: Path) -> dict[str, Any] | None:
    """Parse ``dataset.yaml`` from *dataset_dir*.

    Returns the parsed mapping, or ``None`` when the file is absent (the caller
    maps absence to "contract 0"). Raises ``ValueError`` on an unreadable or
    malformed file, or a non-mapping top-level value — those are corruption,
    distinct from absence. Mirrors ``config_data._load_yaml``.
    """
    path = dataset_dir / DATASET_YAML
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{DATASET_YAML}: unreadable ({e})") from e
    try:
        # _StrictSafeLoader is a SafeLoader subclass (safe), with duplicate-key
        # rejection added — yaml.load with it is equivalent to safe_load + strict.
        meta = yaml.load(raw, Loader=_StrictSafeLoader)
    except yaml.YAMLError as e:
        raise ValueError(f"{DATASET_YAML}: invalid YAML ({e})") from e
    if not isinstance(meta, dict):
        raise ValueError(f"{DATASET_YAML}: top-level value must be a mapping")
    return meta


def load_retired_ids(dataset_dir: Path) -> dict[str, Any] | None:
    """Parse ``retired_ids.yaml`` from *dataset_dir* — exact parallel of
    :func:`load_dataset_meta`.

    Returns the parsed mapping, or ``None`` only when the file is **absent**.
    The no-retirements file is a literal empty mapping ``{}`` — *not* a 0-byte
    file: ``safe_load("")`` is ``None``, which the non-mapping guard below
    rejects as corruption. Raises ``ValueError`` on an unreadable/malformed file
    or a non-mapping top-level value. Uses the same ``_StrictSafeLoader`` so a
    duplicated *table* key can't silently drop retired ids (PyYAML last-wins).
    The id value-domain + cross-checks against the active rows live in
    ``cli.validate_dataset`` (they need that module's id machinery).
    """
    path = dataset_dir / RETIRED_IDS_YAML
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{RETIRED_IDS_YAML}: unreadable ({e})") from e
    try:
        meta = yaml.load(raw, Loader=_StrictSafeLoader)
    except yaml.YAMLError as e:
        raise ValueError(f"{RETIRED_IDS_YAML}: invalid YAML ({e})") from e
    if not isinstance(meta, dict):
        raise ValueError(f"{RETIRED_IDS_YAML}: top-level value must be a mapping")
    return meta


def validate_dataset_meta(meta: dict[str, Any]) -> list[str]:
    """Field-contract checks for a parsed ``dataset.yaml`` mapping.

    Returns a list of human-readable problems; empty means the manifest is
    valid. ``engine_test_ref`` is format-checked only (see ``_ENGINE_REF_RE``).
    """
    errors: list[str] = []

    unknown = set(meta) - KNOWN_KEYS
    if unknown:
        errors.append(f"{DATASET_YAML}: unknown key(s): {sorted(unknown)}")

    ver = meta.get("contract_version")
    # bool is an int subclass — reject `contract_version: true`.
    if not isinstance(ver, int) or isinstance(ver, bool):
        errors.append(f"{DATASET_YAML}: contract_version must be an integer (got {ver!r})")
    elif not MIN_CONTRACT <= ver <= MAX_CONTRACT:
        errors.append(
            f"{DATASET_YAML}: contract_version {ver} is outside this engine's "
            f"supported range ({supported_range_str()})"
        )

    for field in ("dataset_id", "name", "license"):
        val = meta.get(field)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{DATASET_YAML}: {field} must be a non-empty string")

    status = meta.get("status")
    if status not in STATUSES:
        errors.append(f"{DATASET_YAML}: status must be one of {STATUSES} (got {status!r})")

    ref = meta.get("engine_test_ref")
    if not isinstance(ref, str) or not _ENGINE_REF_RE.match(ref):
        errors.append(
            f"{DATASET_YAML}: engine_test_ref must be a 40-character lowercase-hex "
            f"commit (got {ref!r})"
        )

    return errors


def _contract_problems(dataset_dir: Path) -> tuple[list[str], dict[str, Any] | None]:
    """Load + field-check ``dataset.yaml`` once, returning (errors, meta).

    ``meta`` is the parsed mapping when the file is present and parseable, else
    ``None``. A clean result (empty errors) always carries a non-``None`` ``meta``
    with a ``status`` in :data:`STATUSES`. Backs both :func:`check_contract` and
    :func:`gate_for_use` so the manifest is read only once.
    """
    try:
        meta = load_dataset_meta(dataset_dir)
    except ValueError as e:
        return [str(e)], None
    if meta is None:
        return [
            f"missing {DATASET_YAML}: this is contract 0, but this engine "
            f"requires contract {supported_range_str()}. Add a "
            f"{DATASET_YAML} declaring contract_version "
            f"{CONTRACT_VERSION} (see docs/migrations.md)."
        ], None
    return validate_dataset_meta(meta), meta


def check_contract(dataset_dir: Path) -> list[str]:
    """Integrity gate: is *dataset_dir* a contract the engine can read?

    Returns human-readable problems (empty = OK). A missing ``dataset.yaml`` is
    "contract 0" and rejected; a present manifest is parsed (corruption → error)
    and field-checked. Used by ``levels validate-dataset`` — it has **no** opinion
    on ``status``, because a ``scaffold`` dataset is still a *valid* dataset to
    validate. Production commands that refuse a scaffold use :func:`gate_for_use`.
    """
    return _contract_problems(dataset_dir)[0]


def gate_for_use(dataset_dir: Path, *, allow_scaffold: bool) -> list[str]:
    """Fail-closed gate for a production command about to consume *dataset_dir*.

    Returns human-readable problems (empty = proceed). Rejects everything
    :func:`check_contract` does, **plus** a ``status: scaffold`` dataset unless
    *allow_scaffold* — a scaffold dataset is intentionally incomplete and must not
    reach a production DB/docroot. The ``--allow-scaffold`` escape hatch exists for
    fresh-init smoke tests on a throwaway DB.
    """
    errors, meta = _contract_problems(dataset_dir)
    if errors:
        return errors
    assert meta is not None  # a clean contract always yields a parsed manifest
    if meta.get("status") == "scaffold" and not allow_scaffold:
        return [
            f"{DATASET_YAML}: status is 'scaffold' — refusing to operate on a "
            "production DB; pass --allow-scaffold to override (e.g. a fresh-init "
            "smoke test on a throwaway DB)."
        ]
    return []
