"""Tests for ``levels generate-sources`` (dataset-separation S1, expand phase).

The load-bearing invariant is the **byte round-trip**: ``generate-sources`` must
reproduce the committed ``source.csv`` + ``fetch_url.csv`` from the authoritative
``sources.yaml`` exactly, so the registry is a complete, drift-proof projection
of those two CSVs (if a source can't round-trip, the format is missing a field).
These exercise that against the committed fixture, the validation rules, and the
column-order preservation that keeps the round-trip stable against the prod
snapshot's physical-``PRAGMA``-order CSVs (``source.timezone`` is an
``ALTER``-added column, so it lands last on a migrated DB — not at its model
position).
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

import pytest

from kayak.cli import generate_sources as gs
from kayak.dataset import layout
from kayak.resources import resource_dir

FIXTURE = resource_dir("data", "example_dataset")


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    dst = tmp_path / "dataset"
    shutil.copytree(FIXTURE, dst)
    return dst


def _ns(dir: Path, *, check: bool = False, from_csv: bool = False) -> argparse.Namespace:
    return argparse.Namespace(dir=str(dir), check=check, from_csv=from_csv)


def _counters(dir: Path, **kw: int) -> None:
    lines = ["table,next_id", *(f"{t},{n}" for t, n in kw.items())]
    (dir / "id_counters.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _calc(dir: Path, *ids: int) -> None:
    lines = ["id,data_type,expression,time_expression,note,provenance_slug"]
    lines += [f"{i},flow,x,,," for i in ids]
    (dir / "calc_expression.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the round-trip invariant -------------------------------------------------


_CSVS = ("source.csv", "fetch_url.csv", "gauge_source.csv")


def test_generate_reproduces_fixture_byte_for_byte(dataset: Path) -> None:
    before = {n: (dataset / n).read_bytes() for n in _CSVS}
    gs.generate(dataset)
    after = {n: (dataset / n).read_bytes() for n in _CSVS}
    assert after == before


def test_check_passes_on_committed_fixture() -> None:
    # Read-only against the committed fixture: the registry generates exactly it.
    assert gs._main(_ns(FIXTURE, check=True)) == 0


def test_check_fails_on_hand_edited_csv(dataset: Path) -> None:
    # Editing a CSV cell without updating sources.yaml must trip --check (the CI
    # gate's whole purpose: the CSVs are generated artifacts, not hand-editable).
    src = dataset / "source.csv"
    src.write_text(src.read_text(encoding="utf-8").replace("USGS", "TYPO"), encoding="utf-8")
    assert gs._main(_ns(dataset, check=True)) == 1


def test_reverse_engineer_then_generate_round_trips(dataset: Path) -> None:
    original = {n: (dataset / n).read_bytes() for n in _CSVS}
    (dataset / "sources.yaml").unlink()
    gs.reverse_engineer(dataset)
    gs.generate(dataset)
    assert {n: (dataset / n).read_bytes() for n in _CSVS} == original


# --- column-order handling ----------------------------------------------------


def test_preserves_committed_pragma_column_order(tmp_path: Path) -> None:
    # A migrated-DB export puts the ALTER-added `timezone` LAST (not at its model
    # position). generate-sources must preserve that committed order so --check
    # never flags the benign, non-semantic difference.
    d = tmp_path / "ds"
    d.mkdir()
    pragma_source = (
        "id,name,agency,fetch_url_id,calc_expression_id,timezone\n"
        "1,STAW1,USBR,1,,America/Los_Angeles\n"
    )
    (d / "source.csv").write_text(pragma_source, encoding="utf-8")
    (d / "fetch_url.csv").write_text(
        "id,url,parser,hours,is_active\n1,https://example/x,nwps,,1\n", encoding="utf-8"
    )
    (d / "gauge_source.csv").write_text("gauge_id,source_id\n1,1\n", encoding="utf-8")
    (d / "gauge.csv").write_text("id\n1\n", encoding="utf-8")
    (d / "calc_expression.csv").write_text(
        "id,data_type,expression,time_expression,note,provenance_slug\n", encoding="utf-8"
    )
    _counters(d, source=2, fetch_url=2)
    gs.reverse_engineer(d)
    gs.generate(d)
    assert (d / "source.csv").read_text(encoding="utf-8") == pragma_source


def test_absent_csv_falls_back_to_model_order(tmp_path: Path) -> None:
    # A brand-new dataset (no committed CSV) gets the canonical model column order.
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=2, fetch_url=2)
    (d / "sources.yaml").write_text(
        "fetch_urls:\n"
        "- {id: 1, url: 'https://example/x', parser: nwps, enabled: true}\n"
        "sources:\n"
        "- {id: 1, name: STAW1, agency: USBR, gauge_id: 1, fetch_url_id: 1}\n",
        encoding="utf-8",
    )
    gs.generate(d)
    header = (d / "source.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(layout.ordered_columns("source"))


def test_drifted_header_is_rejected(tmp_path: Path) -> None:
    # A committed header whose column set diverges from the schema is corruption,
    # not an order to preserve — reject it rather than silently propagate.
    d = tmp_path / "ds"
    d.mkdir()
    (d / "source.csv").write_text("id,name,agency,STRAY\n1,X,USGS,z\n", encoding="utf-8")
    (d / "fetch_url.csv").write_text("id,url,parser,hours,is_active\n", encoding="utf-8")
    _counters(d, source=2, fetch_url=2)
    (d / "sources.yaml").write_text(
        "sources:\n- {id: 1, name: X, agency: USGS, gauge_id: 1}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not match the schema"):
        gs.generate(d)
    # ...and via --check it surfaces as a clean exit 1, not an uncaught traceback.
    assert gs._main(_ns(d, check=True)) == 1


# --- validation rules ---------------------------------------------------------


def _valid_meta() -> dict:
    # gauge_id is required on every source; vdir has no gauge.csv, so the gauge
    # *reference* check is skipped while the structural requirement still applies.
    return {
        "fetch_urls": [{"id": 1, "url": "https://example/x", "parser": "nwps", "enabled": True}],
        "sources": [
            {"id": 1, "name": "A", "agency": "USGS", "gauge_id": 1},
            {"id": 2, "name": "B", "agency": "NWS", "gauge_id": 2, "fetch_url_id": 1},
            {"id": 3, "name": "C", "agency": "Calculation", "gauge_id": 3, "calc_expression_id": 1},
        ],
    }


@pytest.fixture
def vdir(tmp_path: Path) -> Path:
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=99, fetch_url=99)
    _calc(d, 1)
    return d


def test_valid_registry_has_no_problems(vdir: Path) -> None:
    assert gs.validate_registry(_valid_meta(), vdir) == []


def test_duplicate_source_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["id"] = 1
    assert any("duplicate source id" in p for p in gs.validate_registry(meta, vdir))


def test_duplicate_fetch_url_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["fetch_urls"].append({"id": 1, "url": "https://example/y", "parser": "nwps"})
    assert any("duplicate fetch_url id" in p for p in gs.validate_registry(meta, vdir))


def test_unknown_parser(vdir: Path) -> None:
    meta = _valid_meta()
    meta["fetch_urls"][0]["parser"] = "bogus"
    assert any("unknown parser" in p for p in gs.validate_registry(meta, vdir))


def test_non_string_parser_rejected(vdir: Path) -> None:
    # A YAML container parser must fail closed structurally, not crash
    # _parser_problems' set-membership test with "unhashable type".
    meta = _valid_meta()
    meta["fetch_urls"][0]["parser"] = ["nwps"]
    assert any("parser must be a non-empty string" in p for p in gs.validate_registry(meta, vdir))


def test_both_refs_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["calc_expression_id"] = 1  # already has fetch_url_id
    assert any("at most one of" in p for p in gs.validate_registry(meta, vdir))


def test_dangling_fetch_url_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["fetch_url_id"] = 42
    assert any("fetch_url_id 42 not defined" in p for p in gs.validate_registry(meta, vdir))


def test_dangling_calc_expression_id(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][2]["calc_expression_id"] = 42
    assert any("not in calc_expression.csv" in p for p in gs.validate_registry(meta, vdir))


def test_missing_required_field(vdir: Path) -> None:
    meta = _valid_meta()
    del meta["sources"][0]["name"]
    del meta["fetch_urls"][0]["url"]
    problems = gs.validate_registry(meta, vdir)
    assert any("missing required field 'name'" in p for p in problems)
    assert any("missing required field 'url'" in p for p in problems)


def test_quoted_id_rejected(vdir: Path) -> None:
    # A YAML-quoted id ("1") must not alias the int id 1 (would collide in the CSV).
    meta = _valid_meta()
    meta["sources"][0]["id"] = "1"
    assert any("id must be an integer" in p for p in gs.validate_registry(meta, vdir))


def test_quoted_ref_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][1]["fetch_url_id"] = "1"
    assert any("fetch_url_id must be an integer" in p for p in gs.validate_registry(meta, vdir))


def test_bool_id_rejected(vdir: Path) -> None:
    # bool is an int subclass — guard against `id: true` slipping through.
    meta = _valid_meta()
    meta["fetch_urls"][0]["id"] = True
    assert any("id must be an integer" in p for p in gs.validate_registry(meta, vdir))


def test_non_bool_enabled_rejected(vdir: Path) -> None:
    # A quoted `enabled: "false"` is truthy in Python and would silently enable the
    # URL — require a real bool so the typing mistake fails closed.
    meta = _valid_meta()
    meta["fetch_urls"][0]["enabled"] = "false"
    assert any("enabled must be true or false" in p for p in gs.validate_registry(meta, vdir))


def test_list_hours_rejected(vdir: Path) -> None:
    # A YAML list `hours: [6, 12]` would render the cell "[6, 12]", which
    # _hour_allowed can't parse -> URL silently skipped on every constrained fetch.
    meta = _valid_meta()
    meta["fetch_urls"][0]["hours"] = [6, 12]
    assert any("hours must be a string" in p for p in gs.validate_registry(meta, vdir))


def test_bad_hours_rejected(vdir: Path) -> None:
    # Non-numeric token, out-of-range UTC hour, and a non-empty-but-tokenless spec
    # all fail closed — each would render a constraint that never matches.
    for h in ("6,noon", "24", "99", ","):
        meta = _valid_meta()
        meta["fetch_urls"][0]["hours"] = h
        problems = gs.validate_registry(meta, vdir)
        assert any("hours must be comma-separated UTC hours 0-23" in p for p in problems), (
            f"hours={h!r} should be rejected"
        )


def test_valid_hours_forms_accepted(vdir: Path) -> None:
    # The documented string form, a bare single int, the boundaries, and "" (always).
    for h in ("6,12,18", "0", "23", "6", 6, ""):
        meta = _valid_meta()
        meta["fetch_urls"][0]["hours"] = h
        assert gs.validate_registry(meta, vdir) == [], f"hours={h!r} should be valid"


def test_non_string_url_rejected(vdir: Path) -> None:
    # A list/number url renders a junk CSV cell that fetch can't GET.
    meta = _valid_meta()
    meta["fetch_urls"][0]["url"] = ["http://a", "http://b"]
    assert any("url must be a non-empty string" in p for p in gs.validate_registry(meta, vdir))


def test_non_string_name_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][0]["name"] = ["X"]
    assert any("name must be a non-empty string" in p for p in gs.validate_registry(meta, vdir))


def test_non_string_agency_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["sources"][0]["agency"] = ["USGS"]
    assert any("agency must be a string" in p for p in gs.validate_registry(meta, vdir))


def test_invalid_timezone_rejected(vdir: Path) -> None:
    # _localize does ZoneInfo(tz) at fetch time — a bogus IANA name would crash it.
    meta = _valid_meta()
    meta["sources"][0]["timezone"] = "Mars/Phobos"
    assert any("not a valid IANA timezone" in p for p in gs.validate_registry(meta, vdir))


def test_valid_timezone_accepted(vdir: Path) -> None:
    # Real registry tz values (and a blank = no timezone) validate.
    for tz in ("America/Los_Angeles", "America/Boise", "Etc/GMT+8", ""):
        meta = _valid_meta()
        meta["sources"][0]["timezone"] = tz
        assert gs.validate_registry(meta, vdir) == [], f"timezone={tz!r} should be valid"


def test_id_at_or_above_next_id(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=2, fetch_url=99)  # source next_id=2, so id 3 is stale
    _calc(d, 1)
    assert any("stale counter" in p for p in gs.validate_registry(_valid_meta(), d))


def test_non_list_section_rejected(vdir: Path) -> None:
    # `sources: {}` is falsy → previously coerced to [] and truncated the CSV.
    assert any(
        "sources: must be a list" in p
        for p in gs.validate_registry({"sources": {}, "fetch_urls": []}, vdir)
    )
    # A truthy malformed dict must not crash _source_structural with AttributeError.
    assert any(
        "sources: must be a list" in p
        for p in gs.validate_registry({"sources": {"id": 1, "name": "X"}}, vdir)
    )


def test_non_mapping_item_rejected(vdir: Path) -> None:
    problems = gs.validate_registry({"sources": ["bogus", {"id": 1, "name": "X"}]}, vdir)
    assert any("sources[0]: must be a mapping" in p for p in problems)


def test_non_list_section_does_not_truncate_csv(tmp_path: Path) -> None:
    # The destructive path: `generate` on a malformed registry must refuse, leaving
    # the committed source.csv intact (not rewritten to a header-only file).
    d = tmp_path / "ds"
    d.mkdir()
    source_csv = "id,name,agency,timezone,fetch_url_id,calc_expression_id\n1,FOO,USGS,,,\n"
    (d / "source.csv").write_text(source_csv, encoding="utf-8")
    (d / "fetch_url.csv").write_text("id,url,parser,hours,is_active\n", encoding="utf-8")
    _counters(d, source=9, fetch_url=9)
    (d / "sources.yaml").write_text("fetch_urls: []\nsources: {}\n", encoding="utf-8")
    assert gs._main(_ns(d)) == 1
    assert (d / "source.csv").read_text(encoding="utf-8") == source_csv  # untouched


def test_comma_hours_round_trips(tmp_path: Path) -> None:
    # A multi-hour fetch_url ("6,12,18") must survive CSV -> sources.yaml -> CSV;
    # reverse_engineer must not int()-cast it. (The documented --from-csv path.)
    d = tmp_path / "ds"
    d.mkdir()
    source_csv = "id,name,agency,timezone,fetch_url_id,calc_expression_id\n1,STAW1,USBR,,1,\n"
    fetch_csv = 'id,url,parser,hours,is_active\n1,https://example/x,nwps,"6,12,18",1\n'
    (d / "source.csv").write_text(source_csv, encoding="utf-8")
    (d / "fetch_url.csv").write_text(fetch_csv, encoding="utf-8")
    (d / "gauge_source.csv").write_text("gauge_id,source_id\n1,1\n", encoding="utf-8")
    (d / "gauge.csv").write_text("id\n1\n", encoding="utf-8")
    (d / "calc_expression.csv").write_text(
        "id,data_type,expression,time_expression,note,provenance_slug\n", encoding="utf-8"
    )
    _counters(d, source=2, fetch_url=2)
    gs.reverse_engineer(d)
    assert "hours: 6,12,18" in (d / "sources.yaml").read_text(encoding="utf-8")
    gs.generate(d)
    assert (d / "fetch_url.csv").read_text(encoding="utf-8") == fetch_csv


def _mini_for_reverse(d: Path, gauge_source_body: str) -> None:
    """A minimal dataset for reverse_engineer: one source (id 1) bound to gauge 1 +
    the given gauge_source.csv body. reverse_engineer requires the contract CSVs it
    resolves against (gauge.csv, calc_expression.csv) to be present, so include them."""
    d.mkdir()
    (d / "source.csv").write_text(
        "id,name,agency,timezone,fetch_url_id,calc_expression_id\n1,X,USGS,,,\n", encoding="utf-8"
    )
    (d / "fetch_url.csv").write_text("id,url,parser,hours,is_active\n", encoding="utf-8")
    (d / "gauge.csv").write_text("id\n1\n", encoding="utf-8")
    (d / "calc_expression.csv").write_text(
        "id,data_type,expression,time_expression,note,provenance_slug\n", encoding="utf-8"
    )
    (d / "gauge_source.csv").write_text(gauge_source_body, encoding="utf-8")


def test_reverse_engineer_rejects_orphan_source(tmp_path: Path) -> None:
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\n")  # source 1 has no row
    with pytest.raises(ValueError, match="no gauge_source row"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_dangling_source(tmp_path: Path) -> None:
    # A gauge_source row for a source not in source.csv is corruption (validate-
    # dataset flags it); the bootstrap must refuse, not silently drop it.
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\n1,1\n1,999\n")
    with pytest.raises(ValueError, match=r"references source ids not in source\.csv"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_duplicate_row(tmp_path: Path) -> None:
    # An exact-duplicate gauge_source row must not be silently de-duped on bootstrap.
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\n1,1\n1,1\n")
    with pytest.raises(ValueError, match="duplicate gauge_source rows"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_multi_gauge(tmp_path: Path) -> None:
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\n1,1\n2,1\n")
    with pytest.raises(ValueError, match="linked to multiple gauges"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_non_integer_gauge_source(tmp_path: Path) -> None:
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\nxyz,1\n")
    with pytest.raises(ValueError, match="non-integer id"):
        gs.reverse_engineer(tmp_path / "ds")


@pytest.mark.parametrize("body", ["gauge_id,source_id\n1,\n", "gauge_id,source_id\n,1\n"])
def test_reverse_engineer_rejects_blank_pk_cell(tmp_path: Path, body: str) -> None:
    # A partially-blank NOT-NULL PK row must be rejected, not silently skipped
    # (validate-dataset flags it as "empty value in NOT NULL column").
    _mini_for_reverse(tmp_path / "ds", body)
    with pytest.raises(ValueError, match="empty PK cell"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_dangling_gauge_ref(tmp_path: Path) -> None:
    # A gauge_source row pointing at a non-existent gauge is corruption; the validate
    # pass on the assembled registry must reject it (not write a dangling gauge_id).
    # _mini_for_reverse's gauge.csv has only gauge 1, so 99999999 is dangling.
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\n99999999,1\n")
    with pytest.raises(ValueError, match=r"gauge_id 99999999 not in gauge\.csv"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_missing_required_csv(tmp_path: Path) -> None:
    # gauge.csv (and the other contract CSVs) must be present so refs resolve — a
    # missing one is a clean error, not a FileNotFoundError traceback or a skipped check.
    _mini_for_reverse(tmp_path / "ds", "gauge_id,source_id\n1,1\n")
    (tmp_path / "ds" / "gauge.csv").unlink()
    with pytest.raises(ValueError, match=r"missing required file.*gauge\.csv"):
        gs.reverse_engineer(tmp_path / "ds")


def test_reverse_engineer_rejects_duplicate_source_id(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    _mini_for_reverse(d, "gauge_id,source_id\n1,1\n")
    (d / "source.csv").write_text(
        "id,name,agency,timezone,fetch_url_id,calc_expression_id\n1,A,USGS,,,\n1,B,NWS,,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate source id"):
        gs.reverse_engineer(d)


def test_check_missing_csv_reports_cleanly(tmp_path: Path) -> None:
    # --check with no committed CSV must exit 1 with a message, not traceback.
    d = tmp_path / "ds"
    d.mkdir()
    _counters(d, source=2, fetch_url=2)
    (d / "sources.yaml").write_text(
        "sources:\n- {id: 1, name: X, agency: USGS, gauge_id: 1}\n", encoding="utf-8"
    )
    assert gs._main(_ns(d, check=True)) == 1


# --- unknown_station_policy (S1-fetch-2 opt-in authoring) ----------------------


def test_policy_values_accepted(vdir: Path) -> None:
    # The canonical set plus blank (= default reject); blank is also the absent case.
    for p in ("ignore", "reject", ""):
        meta = _valid_meta()
        meta["fetch_urls"][0]["unknown_station_policy"] = p
        assert gs.validate_registry(meta, vdir) == [], f"policy={p!r} should be valid"


def test_bad_policy_value_rejected(vdir: Path) -> None:
    # A typo, or a non-canonical case/spelling, is caught at authoring time rather
    # than silently demoted to reject at fetch time.
    for p in ("ingore", "drop", "Ignore", "IGNORE"):
        meta = _valid_meta()
        meta["fetch_urls"][0]["unknown_station_policy"] = p
        problems = gs.validate_registry(meta, vdir)
        assert any("unknown_station_policy must be one of" in x for x in problems), (
            f"policy={p!r} should be rejected"
        )


def test_non_string_policy_rejected(vdir: Path) -> None:
    meta = _valid_meta()
    meta["fetch_urls"][0]["unknown_station_policy"] = ["ignore"]
    assert any(
        "unknown_station_policy must be a string" in p for p in gs.validate_registry(meta, vdir)
    )


def _policy_dataset(d: Path, *, policy_line: str) -> None:
    """Minimal two-URL dataset; the first fetch_url carries ``policy_line`` verbatim
    (e.g. ``unknown_station_policy: ignore, `` or empty for no opt-in)."""
    d.mkdir()
    _counters(d, source=3, fetch_url=3)
    (d / "gauge.csv").write_text("id\n1\n2\n", encoding="utf-8")
    (d / "calc_expression.csv").write_text(
        "id,data_type,expression,time_expression,note,provenance_slug\n", encoding="utf-8"
    )
    (d / "sources.yaml").write_text(
        "fetch_urls:\n"
        f"- {{id: 1, url: 'https://example/a', parser: usbr, {policy_line}enabled: true}}\n"
        "- {id: 2, url: 'https://example/b', parser: nwps, enabled: true}\n"
        "sources:\n"
        "- {id: 1, name: A, agency: USBR, gauge_id: 1, fetch_url_id: 1}\n"
        "- {id: 2, name: B, agency: NWS, gauge_id: 2, fetch_url_id: 2}\n",
        encoding="utf-8",
    )


def test_policy_column_omitted_when_no_optin(tmp_path: Path) -> None:
    # No URL opts in → fetch_url.csv carries no unknown_station_policy column at all
    # (the backward-compatible default that keeps older datasets byte-identical).
    d = tmp_path / "ds"
    _policy_dataset(d, policy_line="")
    gs.generate(d)
    header = (d / "fetch_url.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "unknown_station_policy" not in header


def test_policy_appends_column_and_round_trips(tmp_path: Path) -> None:
    # An opt-in appends the column (only the opting row populated), --check stays
    # clean on the freshly generated dataset, and reverse_engineer -> generate is
    # byte-identical (the policy survives the registry round-trip).
    d = tmp_path / "ds"
    _policy_dataset(d, policy_line="unknown_station_policy: ignore, ")
    gs.generate(d)

    rows = list(csv.DictReader((d / "fetch_url.csv").open(encoding="utf-8")))
    by_id = {r["id"]: r["unknown_station_policy"] for r in rows}
    assert by_id == {"1": "ignore", "2": ""}

    assert gs._main(_ns(d, check=True)) == 0

    before = (d / "fetch_url.csv").read_bytes()
    (d / "sources.yaml").unlink()
    gs.reverse_engineer(d)
    gs.generate(d)
    assert (d / "fetch_url.csv").read_bytes() == before
    assert "unknown_station_policy: ignore" in (d / "sources.yaml").read_text(encoding="utf-8")


def test_check_flags_optin_not_yet_regenerated(tmp_path: Path) -> None:
    # Adding the opt-in to sources.yaml without regenerating must trip --check: the
    # committed fetch_url.csv (no column) drifts from the registry (column needed).
    d = tmp_path / "ds"
    _policy_dataset(d, policy_line="")
    gs.generate(d)  # commit the no-column CSV
    # Now opt in, but DON'T regenerate.
    sy = d / "sources.yaml"
    sy.write_text(
        sy.read_text(encoding="utf-8").replace(
            "parser: usbr, enabled: true",
            "parser: usbr, unknown_station_policy: ignore, enabled: true",
        ),
        encoding="utf-8",
    )
    assert gs._main(_ns(d, check=True)) == 1


def test_opt_out_drops_the_column(tmp_path: Path) -> None:
    # Symmetric with opt-in: removing the only opt-in drops the column again (not a
    # vestigial empty column). --check flags the stale committed file until the
    # opt-out is regenerated (an opt-out IS a content change), then passes.
    d = tmp_path / "ds"
    _policy_dataset(d, policy_line="unknown_station_policy: ignore, ")
    gs.generate(d)
    assert "unknown_station_policy" in (d / "fetch_url.csv").read_text(encoding="utf-8")

    sy = d / "sources.yaml"
    sy.write_text(
        sy.read_text(encoding="utf-8").replace("unknown_station_policy: ignore, ", ""),
        encoding="utf-8",
    )
    # Stale committed file still has the column → --check trips...
    assert gs._main(_ns(d, check=True)) == 1
    gs.generate(d)
    header = (d / "fetch_url.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "unknown_station_policy" not in header  # ...column dropped on regenerate
    assert gs._main(_ns(d, check=True)) == 0
