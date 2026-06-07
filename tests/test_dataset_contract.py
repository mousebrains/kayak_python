"""Unit tests for kayak.dataset.contract — the dataset.yaml contract (S6.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kayak.dataset import contract


def _valid_meta() -> dict:
    return {
        "contract_version": contract.CONTRACT_VERSION,
        "dataset_id": "wkcc",
        "name": "WKCC River Levels",
        "status": "publishable",
        "license": "CC-BY-NC-4.0",
        "engine_test_ref": "a" * 40,
    }


def _write_dataset_yaml(d: Path, **overrides: object) -> None:
    """Write a valid dataset.yaml into *d*, applying field overrides."""
    (d / contract.DATASET_YAML).write_text(yaml.safe_dump(_valid_meta() | overrides))


class TestLoadDatasetMeta:
    def test_absent_returns_none(self, tmp_path: Path) -> None:
        assert contract.load_dataset_meta(tmp_path) is None

    def test_valid_returns_mapping(self, tmp_path: Path) -> None:
        (tmp_path / contract.DATASET_YAML).write_text("contract_version: 1\nname: x\n")
        meta = contract.load_dataset_meta(tmp_path)
        assert meta == {"contract_version": 1, "name": "x"}

    def test_malformed_raises(self, tmp_path: Path) -> None:
        (tmp_path / contract.DATASET_YAML).write_text("a: : b\n")
        with pytest.raises(ValueError, match="invalid YAML"):
            contract.load_dataset_meta(tmp_path)

    def test_non_mapping_raises(self, tmp_path: Path) -> None:
        (tmp_path / contract.DATASET_YAML).write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            contract.load_dataset_meta(tmp_path)

    def test_duplicate_key_raises(self, tmp_path: Path) -> None:
        # PyYAML's default last-wins would let a bad value hide behind a good one
        # in the contract gate; the strict loader rejects it.
        (tmp_path / contract.DATASET_YAML).write_text(
            "contract_version: 99\ncontract_version: 1\nname: x\n"
        )
        with pytest.raises(ValueError, match="duplicate key"):
            contract.load_dataset_meta(tmp_path)

    def test_unhashable_key_raises(self, tmp_path: Path) -> None:
        # A YAML complex key (`? [1, 2]`) is unhashable — the strict loader must
        # reject it as malformed, not let a raw TypeError escape and crash.
        (tmp_path / contract.DATASET_YAML).write_text("? [1, 2]\n: foo\n")
        with pytest.raises(ValueError, match="invalid YAML"):
            contract.load_dataset_meta(tmp_path)


class TestValidateDatasetMeta:
    def test_valid(self) -> None:
        assert contract.validate_dataset_meta(_valid_meta()) == []

    def test_contract_version_non_int(self) -> None:
        m = _valid_meta() | {"contract_version": "1"}
        assert any(
            "contract_version must be an integer" in e for e in contract.validate_dataset_meta(m)
        )

    def test_contract_version_bool_rejected(self) -> None:
        m = _valid_meta() | {"contract_version": True}
        assert any(
            "contract_version must be an integer" in e for e in contract.validate_dataset_meta(m)
        )

    def test_contract_version_out_of_range(self) -> None:
        m = _valid_meta() | {"contract_version": contract.MAX_CONTRACT + 1}
        assert any(
            "outside this engine's supported range" in e for e in contract.validate_dataset_meta(m)
        )

    @pytest.mark.parametrize("field", ["dataset_id", "name", "license"])
    def test_required_string_fields(self, field: str) -> None:
        m = _valid_meta() | {field: "  "}
        assert any(
            f"{field} must be a non-empty string" in e for e in contract.validate_dataset_meta(m)
        )

    def test_bad_status(self) -> None:
        m = _valid_meta() | {"status": "draft"}
        assert any("status must be one of" in e for e in contract.validate_dataset_meta(m))

    def test_unknown_key_rejected(self) -> None:
        # A stray/typo'd key (e.g. `licence` left beside a corrected `license`)
        # is an error, matching the validator's complete-projection ethos.
        m = _valid_meta() | {"licence": "L"}
        assert any("unknown key(s): ['licence']" in e for e in contract.validate_dataset_meta(m))

    def test_provenance_key_allowed(self) -> None:
        m = _valid_meta() | {"provenance": "PROVENANCE.json"}
        assert contract.validate_dataset_meta(m) == []

    @pytest.mark.parametrize("ref", ["nothex", "A" * 40, "a" * 39, "a" * 41, 123])
    def test_bad_engine_test_ref(self, ref: object) -> None:
        m = _valid_meta() | {"engine_test_ref": ref}
        assert any("engine_test_ref must be" in e for e in contract.validate_dataset_meta(m))


class TestLoadRetiredIds:
    def test_absent_returns_none(self, tmp_path: Path) -> None:
        assert contract.load_retired_ids(tmp_path) is None

    def test_empty_mapping_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("{}\n")
        assert contract.load_retired_ids(tmp_path) == {}

    def test_valid_returns_mapping(self, tmp_path: Path) -> None:
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("reach:\n  - 7\n  - 9\n")
        assert contract.load_retired_ids(tmp_path) == {"reach": [7, 9]}

    def test_zero_byte_file_raises(self, tmp_path: Path) -> None:
        # A present-but-empty file parses to None, which the non-mapping guard
        # rejects — the no-retirements convention is `{}`, not a 0-byte file.
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("")
        with pytest.raises(ValueError, match="must be a mapping"):
            contract.load_retired_ids(tmp_path)

    def test_non_mapping_raises(self, tmp_path: Path) -> None:
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            contract.load_retired_ids(tmp_path)

    def test_malformed_raises(self, tmp_path: Path) -> None:
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("a: : b\n")
        with pytest.raises(ValueError, match="invalid YAML"):
            contract.load_retired_ids(tmp_path)

    def test_duplicate_table_key_raises(self, tmp_path: Path) -> None:
        # The strict loader rejects a repeated table key (PyYAML last-wins would
        # silently drop the first list of retired ids).
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("reach:\n  - 7\nreach:\n  - 9\n")
        with pytest.raises(ValueError, match="duplicate key"):
            contract.load_retired_ids(tmp_path)

    def test_unhashable_key_raises(self, tmp_path: Path) -> None:
        (tmp_path / contract.RETIRED_IDS_YAML).write_text("? [1, 2]\n: foo\n")
        with pytest.raises(ValueError, match="invalid YAML"):
            contract.load_retired_ids(tmp_path)


class TestCheckContract:
    """The integrity gate (used by validate-dataset) — no scaffold opinion."""

    def test_missing_is_contract_zero(self, tmp_path: Path) -> None:
        errs = contract.check_contract(tmp_path)
        assert any("contract 0" in e and "requires contract" in e for e in errs)

    def test_valid_publishable(self, tmp_path: Path) -> None:
        _write_dataset_yaml(tmp_path)
        assert contract.check_contract(tmp_path) == []

    def test_scaffold_passes_integrity(self, tmp_path: Path) -> None:
        # A scaffold dataset is still a *valid* dataset to validate.
        _write_dataset_yaml(tmp_path, status="scaffold")
        assert contract.check_contract(tmp_path) == []

    def test_malformed(self, tmp_path: Path) -> None:
        (tmp_path / contract.DATASET_YAML).write_text("a: : b\n")
        assert any("invalid YAML" in e for e in contract.check_contract(tmp_path))


class TestGateForUse:
    """The fail-closed production gate (used by sync-metadata)."""

    def test_publishable_passes(self, tmp_path: Path) -> None:
        _write_dataset_yaml(tmp_path)
        assert contract.gate_for_use(tmp_path, allow_scaffold=False) == []

    def test_scaffold_rejected_without_flag(self, tmp_path: Path) -> None:
        _write_dataset_yaml(tmp_path, status="scaffold")
        errs = contract.gate_for_use(tmp_path, allow_scaffold=False)
        assert any("scaffold" in e for e in errs)

    def test_scaffold_allowed_with_flag(self, tmp_path: Path) -> None:
        _write_dataset_yaml(tmp_path, status="scaffold")
        assert contract.gate_for_use(tmp_path, allow_scaffold=True) == []

    def test_contract_zero_rejected_even_with_allow_scaffold(self, tmp_path: Path) -> None:
        # --allow-scaffold doesn't rescue a missing/invalid manifest.
        errs = contract.gate_for_use(tmp_path, allow_scaffold=True)
        assert any("contract 0" in e for e in errs)

    def test_out_of_range_rejected(self, tmp_path: Path) -> None:
        _write_dataset_yaml(tmp_path, contract_version=contract.MAX_CONTRACT + 1)
        errs = contract.gate_for_use(tmp_path, allow_scaffold=False)
        assert any("outside this engine's supported range" in e for e in errs)


def test_supported_range_str() -> None:
    # Single-version range prints as a bare number, not a range expression.
    assert contract.supported_range_str() == (
        str(contract.MIN_CONTRACT)
        if contract.MIN_CONTRACT == contract.MAX_CONTRACT
        else f"{contract.MIN_CONTRACT}-{contract.MAX_CONTRACT}"
    )
