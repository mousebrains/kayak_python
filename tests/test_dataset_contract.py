"""Unit tests for kayak.dataset.contract — the dataset.yaml contract (S6.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

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

    @pytest.mark.parametrize("ref", ["nothex", "A" * 40, "a" * 39, "a" * 41, 123])
    def test_bad_engine_test_ref(self, ref: object) -> None:
        m = _valid_meta() | {"engine_test_ref": ref}
        assert any("engine_test_ref must be" in e for e in contract.validate_dataset_meta(m))


def test_supported_range_str() -> None:
    # Single-version range prints as a bare number, not a range expression.
    assert contract.supported_range_str() == (
        str(contract.MIN_CONTRACT)
        if contract.MIN_CONTRACT == contract.MAX_CONTRACT
        else f"{contract.MIN_CONTRACT}-{contract.MAX_CONTRACT}"
    )
