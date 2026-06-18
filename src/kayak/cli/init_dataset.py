"""``levels init-dataset <dir>`` — scaffold a new dataset (or copy the example).

A second club can stand up its own dataset without editing a tracked engine
file: ``init-dataset DIR`` writes an empty, **contract-1** dataset (status
``scaffold``) through the same :mod:`kayak.dataset.layout` / :mod:`kayak.dataset
.contract` descriptors the validator reads, so the result passes
``levels validate-dataset`` by construction. ``--example`` instead copies the
packaged publishable example dataset verbatim.

The command **self-validates**: it runs ``validate-dataset`` on its own output
and, if that ever reports a problem, removes what it created and exits non-zero
— a fresh scaffold must never be born invalid.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

import yaml

from kayak.dataset import contract, layout
from kayak.resources import resource_dir

_ZERO_REF = "0" * 40
_DEFAULT_LICENSE = "CC-BY-NC-4.0"

# Site prose required for a *publishable* dataset (kayak.cli.validate_dataset).
# A scaffold may omit site/ entirely, but we write clean placeholders so the
# later `status: publishable` flip just works.
_SITE_PAGES: tuple[str, ...] = ("privacy", "disclaimer", "contact")


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``init-dataset`` subcommand."""
    p = subparsers.add_parser(
        "init-dataset",
        help="Scaffold a new dataset directory (or --example to copy the packaged example)",
    )
    p.add_argument("dir", help="Destination directory (created if absent; must be empty)")
    p.add_argument(
        "--name",
        default=None,
        help="Dataset display name (default: the directory's name). Ignored with --example.",
    )
    p.add_argument(
        "--id",
        dest="dataset_id",
        default=None,
        help="Stable dataset_id (default: a slug of the directory name). Ignored with --example.",
    )
    p.add_argument(
        "--license",
        default=_DEFAULT_LICENSE,
        help=f"SPDX license id for dataset.yaml (default {_DEFAULT_LICENSE}). Ignored with --example.",
    )
    p.add_argument(
        "--example",
        action="store_true",
        help="Copy the packaged publishable example dataset verbatim instead of scaffolding",
    )
    p.set_defaults(func=_main)


def _slug(text: str) -> str:
    """A lowercase ``[a-z0-9_]`` slug for a default ``dataset_id`` from a dir name."""
    out = "".join(c if c.isalnum() else "_" for c in text.strip().lower())
    out = "_".join(part for part in out.split("_") if part)
    return out or "dataset"


def _yaml_kv(key: str, value: object) -> str:
    """One ``key: value`` line, with PyYAML doing the scalar quoting.

    Routing each field through ``safe_dump`` quotes an arbitrary ``--name`` /
    ``--license`` (a colon, a leading digit, the all-zero ref) correctly, so a
    hand-built manifest can't emit YAML that re-parses to the wrong type.
    """
    dumped = yaml.safe_dump(
        {key: value}, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    return dumped.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_header_only_csvs(dest: Path) -> None:
    """The 15 contract CSVs, header-only — a complete projection of empty tables."""
    for table in layout.CONTRACT_CSVS:
        with (dest / f"{table}.csv").open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerow(layout.ordered_columns(table))


def _write_id_counters(dest: Path) -> None:
    """One ``table,next_id`` row per id-bearing table; next_id=1 (no rows yet)."""
    with (dest / layout.ID_COUNTERS_CSV).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["table", "next_id"])
        for table in sorted(layout.id_bearing_tables()):
            w.writerow([table, 1])


def _dataset_yaml(name: str, dataset_id: str, license_id: str) -> str:
    return (
        "\n".join(
            [
                "# Dataset contract manifest (S6.2). See kayak.dataset.contract.",
                _yaml_kv("contract_version", contract.CONTRACT_VERSION),
                _yaml_kv("dataset_id", dataset_id),
                _yaml_kv("name", name),
                _yaml_kv("status", "scaffold"),
                _yaml_kv("license", license_id),
                "# TODO: pin to the 40-lowercase-hex engine commit you validate against (S7).",
                "# The all-zero placeholder passes the format check; replace before going live.",
                _yaml_kv("engine_test_ref", _ZERO_REF),
            ]
        )
        + "\n"
    )


def _scaffold(dest: Path, *, name: str, dataset_id: str, license_id: str) -> None:
    """Write an empty, valid, contract-1 ``scaffold`` dataset into *dest*."""
    _write(dest / contract.DATASET_YAML, _dataset_yaml(name, dataset_id, license_id))
    _write_header_only_csvs(dest)
    _write_id_counters(dest)
    # Sidecars — both JSON keyed by reach id ({} when empty); retired-ids the
    # literal empty mapping (a 0-byte file parses to None and is rejected).
    _write(dest / layout.GEOM_JSON, "{}\n")
    _write(dest / layout.GRADIENT_JSON, "{}\n")
    _write(
        dest / contract.RETIRED_IDS_YAML,
        "# Purged stable ids, per id-bearing table — kept reserved so they are never\n"
        "# reused and the id counter stays above them. See kayak.dataset.contract.\n"
        "# Empty mapping = nothing retired.\n"
        "{}\n",
    )
    # The source registry: generate-sources (re)writes source/fetch_url/
    # gauge_source.csv from this. A fresh scaffold has none yet; the three CSVs
    # above are already written header-only, so validation passes without it.
    _write(
        dest / "sources.yaml",
        "# Authoritative source registry (dataset-separation S1). Edit this, then run\n"
        "# `levels generate-sources <dir>` to (re)write source.csv, fetch_url.csv,\n"
        "# gauge_source.csv. A fresh scaffold has no sources yet.\n"
        "fetch_urls: []\n"
        "sources: []\n",
    )
    # Publishable-required prose, as clean-markdown placeholders, so flipping
    # status to publishable later just works.
    for page in _SITE_PAGES:
        _write(
            dest / "site" / f"{page}.md",
            f"# {page.capitalize()}\n\n"
            f"TODO: replace this placeholder with your site's {page} content before\n"
            "setting `status: publishable` in dataset.yaml.\n",
        )
    _write(
        dest / "PROVENANCE.json",
        '{\n  "note": "TODO: record where this dataset\'s reach geometry, gradients, '
        'and facts came from, and their license.",\n  "reaches": []\n}\n',
    )
    _write(
        dest / "README.md",
        f"# {name}\n\n"
        "A kayak **dataset** (river levels metadata), scaffolded by `levels init-dataset`.\n\n"
        "This is the single authority for this club's metadata; the engine is a separate\n"
        "package. Edit the CSVs / `sources.yaml`, then:\n\n"
        "```\n"
        "levels validate-dataset .          # checks every contract invariant\n"
        "levels generate-sources .          # (re)writes source/fetch_url/gauge_source.csv\n"
        "DATASET_DIR=. levels init-db && DATASET_DIR=. levels sync-metadata\n"
        "```\n\n"
        "Flip `status: scaffold` to `publishable` once `site/{privacy,disclaimer,contact}.md`\n"
        "carry real content. See the engine's `docs/new-region-runbook.md`.\n",
    )


def _copy_example(dest: Path) -> None:
    """Copy the packaged publishable example dataset into *dest* verbatim."""
    shutil.copytree(resource_dir("data", "example_dataset"), dest, dirs_exist_ok=True)


def _cleanup(dest: Path, created_root: bool) -> None:
    """Undo a failed init — remove the tree we created (or just its contents)."""
    if created_root:
        shutil.rmtree(dest, ignore_errors=True)
        return
    for child in dest.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _main(args: argparse.Namespace) -> int:
    dest = Path(args.dir)
    if dest.exists():
        if not dest.is_dir():
            print(f"init-dataset: not a directory: {dest}", file=sys.stderr)
            return 2
        if any(dest.iterdir()):
            print(f"init-dataset: destination is not empty: {dest}", file=sys.stderr)
            return 2
    created_root = not dest.exists()
    dest.mkdir(parents=True, exist_ok=True)

    try:
        if args.example:
            _copy_example(dest)
        else:
            _scaffold(
                dest,
                name=args.name or dest.resolve().name,
                dataset_id=args.dataset_id or _slug(dest.resolve().name),
                license_id=args.license,
            )
    except OSError as e:
        _cleanup(dest, created_root)
        print(f"init-dataset: failed to write {dest}: {e}", file=sys.stderr)
        return 1

    # Self-validate: a fresh init must never be born invalid. Imported here to
    # keep init-dataset's import graph (and --help) free of the validator.
    from kayak.cli.validate_dataset import validate_dataset

    problems = validate_dataset(dest)
    if problems:
        _cleanup(dest, created_root)
        print(
            "init-dataset: BUG — generated dataset failed validation (nothing left "
            "behind). Please report:",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    kind = "example dataset" if args.example else "scaffold dataset"
    print(f"init-dataset: wrote a valid {kind} to {dest}")
    if not args.example:
        print(
            "  next: edit sources.yaml + the CSVs, then `levels validate-dataset` (see README.md)"
        )
    return 0
