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
import re
import shutil
import sys
from pathlib import Path

import yaml

from kayak.dataset import contract, layout
from kayak.resources import resource_dir

_ZERO_REF = "0" * 40
_DEFAULT_LICENSE = "CC-BY-NC-4.0"

# --ci defaults + input grammars. The repo default is a deliberate placeholder
# (NOT a real owner) so an unedited workflow can't silently target someone's repo.
_CI_ENGINE_REPO_PLACEHOLDER = "your-org/kayak_python"
_CI_ENGINE_SECRET_DEFAULT = "KAYAK_ENGINE_DEPLOY_KEY"
_CI_SITE_URL_PLACEHOLDER = "https://levels.example.org"
# OWNER/REPO; an Actions secret name (letters/digits/underscore, not leading
# digit — GitHub's rule); an http(s) URL. These are interpolated into the emitted
# YAML, so reject anything that could malform/inject it.
_REPO_RE = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_SECRET_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_URL_RE = re.compile(r"\Ahttps?://[^\s\"'<>{}]+\Z")

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
    p.add_argument(
        "--ci",
        action="store_true",
        help="Also emit .github/workflows/validate.yml (a starting point to review, not turnkey)",
    )
    p.add_argument(
        "--engine-repo",
        default=_CI_ENGINE_REPO_PLACEHOLDER,
        help="OWNER/REPO of the engine the --ci workflow validates against "
        f"(default {_CI_ENGINE_REPO_PLACEHOLDER!r}, a placeholder to edit)",
    )
    p.add_argument(
        "--engine-secret",
        default=_CI_ENGINE_SECRET_DEFAULT,
        help=f"Actions secret holding the engine read deploy key (default {_CI_ENGINE_SECRET_DEFAULT})",
    )
    p.add_argument(
        "--site-url",
        default=_CI_SITE_URL_PLACEHOLDER,
        help=f"SITE_URL for the --ci build smoke (default {_CI_SITE_URL_PLACEHOLDER})",
    )
    p.set_defaults(func=_main)


def _slug(text: str) -> str:
    """A lowercase ASCII ``[a-z0-9_]`` slug for a default ``dataset_id``.

    ``str.isalnum()`` is true for Unicode letters too, so guard with ``isascii``
    — otherwise a dir named ``café`` would seed a non-ASCII ``dataset_id``.
    """
    out = "".join(c if (c.isascii() and c.isalnum()) else "_" for c in text.strip().lower())
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
    # gauge_source.csv from this. A fresh scaffold has no sources yet.
    _write(
        dest / "sources.yaml",
        "# Authoritative source registry (dataset-separation S1). Edit this, then run\n"
        "# `levels generate-sources <dir>` to (re)write source.csv, fetch_url.csv,\n"
        "# gauge_source.csv. A fresh scaffold has no sources yet.\n"
        "fetch_urls: []\n"
        "sources: []\n",
    )
    # Rewrite the three generator-owned CSVs THROUGH the generator so the scaffold
    # is byte-identical to its own `generate-sources` output (the emitted --ci
    # workflow runs `generate-sources --check`). The generic header-only path above
    # keeps optional columns the generator drops for an empty registry (e.g.
    # fetch_url.unknown_station_policy), which would otherwise fail that gate.
    from kayak.cli import generate_sources

    generate_sources.generate(dest)
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
        "# smoke the load on a throwaway DB — a scaffold needs --allow-scaffold:\n"
        "DATASET_DIR=. levels init-db && DATASET_DIR=. levels sync-metadata --allow-scaffold\n"
        "```\n\n"
        "Flip `status: scaffold` to `publishable` once `site/{privacy,disclaimer,contact}.md`\n"
        "carry real content (then production sync/build no longer needs `--allow-scaffold`).\n"
        "See the engine's `docs/new-region-runbook.md`.\n",
    )


def _copy_example(dest: Path) -> None:
    """Copy the packaged publishable example dataset into *dest* verbatim."""
    shutil.copytree(resource_dir("data", "example_dataset"), dest, dirs_exist_ok=True)


# Dataset-repo CI workflow, modeled on kayak_data's validate.yml. The
# engine-coupled core — trusted-base-pin read, pin-only-bump discipline, and the
# validate → generate-sources --check → sync → build sequence — is preserved
# verbatim; only the club-specific bits are sentinels substituted by `_emit_ci`.
# (kayak_data's `history/` archive step is dropped: a fresh dataset has none.)
_CI_WORKFLOW_TEMPLATE = """\
# Validate this dataset against the engine pinned by dataset.yaml's
# engine_test_ref. GENERATED by `levels init-dataset --ci` as a STARTING POINT —
# review it (and set the engine repo / deploy-key secret / SITE_URL below) before
# relying on it as a required gate.
#
# The pin is read from the BASE commit, never the PR working tree, so a PR can't
# weaken its own validator by editing engine_test_ref. The engine is fetched with
# a read-only deploy key (the @@ENGINE_SECRET@@ Actions secret). Uses
# `pull_request` (never pull_request_target): a fork PR simply lacks the secret
# and fails the engine checkout — no secret exposure.
name: validate

on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout dataset
        uses: actions/checkout@v6
        with:
          fetch-depth: 0 # the PR base commit must be present for the trusted-pin read

      - name: Resolve trusted engine pin (from the BASE commit, not the PR tree)
        id: pin
        run: |
          BASE_SHA="${{ github.event.pull_request.base.sha || github.sha }}"
          REF=$(git show "$BASE_SHA:dataset.yaml" \\
            | sed -nE 's/^engine_test_ref:[^0-9a-f]*([0-9a-f]{40}).*/\\1/p')
          if [ -z "$REF" ]; then
            echo "::error::could not read a 40-hex engine_test_ref from $BASE_SHA:dataset.yaml"
            exit 1
          fi
          CANDIDATE=$(sed -nE 's/^engine_test_ref:[^0-9a-f]*([0-9a-f]{40}).*/\\1/p' dataset.yaml)
          if [ -z "$CANDIDATE" ]; then
            echo "::error::could not read a 40-hex engine_test_ref from the PR dataset.yaml"
            exit 1
          fi
          echo "trusted_ref=$REF" >> "$GITHUB_OUTPUT"
          echo "candidate_ref=$CANDIDATE" >> "$GITHUB_OUTPUT"
          echo "Trusted base engine pin: $REF"
          echo "Candidate engine pin: $CANDIDATE"

      - name: Enforce pin-only engine bump discipline
        id: pin_policy
        env:
          EVENT_NAME: ${{ github.event_name }}
          BASE_SHA: ${{ github.event.pull_request.base.sha || github.sha }}
          TRUSTED_REF: ${{ steps.pin.outputs.trusted_ref }}
          CANDIDATE_REF: ${{ steps.pin.outputs.candidate_ref }}
        run: |
          python3 - <<'PY'
          import os
          import pathlib
          import subprocess
          import sys

          event_name = os.environ["EVENT_NAME"]
          base_sha = os.environ["BASE_SHA"]
          trusted_ref = os.environ["TRUSTED_REF"]
          candidate_ref = os.environ["CANDIDATE_REF"]
          validation_ref = trusted_ref

          def dataset_yaml_at(ref: str) -> dict[str, str]:
              text = subprocess.check_output(
                  ["git", "show", f"{ref}:dataset.yaml"],
                  text=True,
              )
              return parse_dataset_yaml(text)

          def parse_dataset_yaml(text: str) -> dict[str, str]:
              data: dict[str, str] = {}
              for line in text.splitlines():
                  stripped = line.strip()
                  if not stripped or stripped.startswith("#") or ":" not in stripped:
                      continue
                  key, value = stripped.split(":", 1)
                  data[key.strip()] = value.strip().strip("\\"'")
              return data

          if event_name == "pull_request" and candidate_ref != trusted_ref:
              changed = subprocess.check_output(
                  ["git", "diff", "--name-only", f"{base_sha}...HEAD"],
                  text=True,
              ).splitlines()
              extra = [path for path in changed if path != "dataset.yaml"]
              if extra:
                  print(
                      "::error::engine_test_ref changes must be pin-only. "
                      "Move these data/workflow changes to a follow-up PR: "
                      + ", ".join(extra)
                  )
                  sys.exit(1)

              base_meta = dataset_yaml_at(base_sha)
              head_meta = parse_dataset_yaml(pathlib.Path("dataset.yaml").read_text())
              base_meta.pop("engine_test_ref", None)
              head_meta.pop("engine_test_ref", None)
              if head_meta != base_meta:
                  print(
                      "::error::engine_test_ref changes may only alter the pin "
                      "and comments in dataset.yaml"
                  )
                  sys.exit(1)

              validation_ref = candidate_ref
              print(
                  "Pin-only PR detected; validating unchanged dataset against "
                  f"candidate engine {candidate_ref}"
              )
          else:
              print(f"Validating against trusted engine {trusted_ref}")

          with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
              out.write(f"validation_ref={validation_ref}\\n")
          PY

      - name: Checkout engine at the pinned commit (read-only deploy key)
        uses: actions/checkout@v6
        with:
          repository: @@ENGINE_REPO@@
          ref: ${{ steps.pin_policy.outputs.validation_ref }}
          ssh-key: ${{ secrets.@@ENGINE_SECRET@@ }}
          path: engine

      - uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"

      - name: Install engine (base deps, hash-locked)
        working-directory: engine
        run: uv sync --locked

      - name: validate-dataset (full contract integrity)
        run: engine/.venv/bin/levels validate-dataset "$GITHUB_WORKSPACE"

      # S1: sources.yaml is the human-edited authority for source.csv +
      # fetch_url.csv; --check regenerates them and byte-compares the committed
      # files, so a hand-edit to either CSV (or a drifted registry) fails here.
      - name: generate-sources --check (sources.yaml vs committed CSVs)
        run: engine/.venv/bin/levels generate-sources "$GITHUB_WORKSPACE" --check

      - name: sync + build smoke (clean apply, idempotent no-op, build)
        env:
          DATABASE_URL: sqlite:///${{ runner.temp }}/k.db
          DATASET_DIR: ${{ github.workspace }}
          OUTPUT_DIR: ${{ runner.temp }}/out
          SITE_URL: @@SITE_URL@@
        run: |
          # --allow-scaffold so the smoke passes while the dataset is still
          # status: scaffold (a throwaway CI DB — the documented escape hatch);
          # it is a no-op once the dataset is publishable.
          L=engine/.venv/bin/levels
          "$L" init-db
          "$L" sync-metadata --allow-scaffold
          "$L" sync-metadata --allow-scaffold
          "$L" build
"""


def _ci_param_errors(engine_repo: str, engine_secret: str, site_url: str) -> list[str]:
    """Reject a malformed --ci parameter before it is interpolated into the YAML."""
    errors: list[str] = []
    if not _REPO_RE.match(engine_repo):
        errors.append(f"--engine-repo must be OWNER/REPO (got {engine_repo!r})")
    if not _SECRET_RE.match(engine_secret):
        errors.append(
            f"--engine-secret must be a valid Actions secret name (got {engine_secret!r})"
        )
    if not _URL_RE.match(site_url):
        errors.append(f"--site-url must be an http(s) URL (got {site_url!r})")
    return errors


def _emit_ci(dest: Path, *, engine_repo: str, engine_secret: str, site_url: str) -> None:
    """Write ``.github/workflows/validate.yml`` (params already validated)."""
    text = (
        _CI_WORKFLOW_TEMPLATE.replace("@@ENGINE_REPO@@", engine_repo)
        .replace("@@ENGINE_SECRET@@", engine_secret)
        .replace("@@SITE_URL@@", site_url)
    )
    _write(dest / ".github" / "workflows" / "validate.yml", text)


def _topmost_created(dest: Path) -> Path | None:
    """The highest ancestor ``mkdir(parents=True)`` would newly create for *dest*
    (possibly *dest* itself) — what cleanup must remove to fully undo the init.

    ``None`` when *dest* already exists (then only its *contents* were written,
    so cleanup must keep the operator's directory and remove just what we put in
    it). Computed BEFORE the mkdir.
    """
    if dest.exists():
        return None
    top = dest
    while not top.parent.exists():
        top = top.parent
    return top


def _cleanup(dest: Path, created_top: Path | None) -> None:
    """Undo a failed init: remove the whole tree we created (the topmost dir
    ``mkdir`` made, so auto-created parents don't leak), or — when *dest*
    pre-existed — only its contents."""
    if created_top is not None:
        shutil.rmtree(created_top, ignore_errors=True)
        return
    for child in dest.iterdir():
        # rmtree refuses a symlink (NotADirectoryError, which ignore_errors would
        # silently swallow, leaving the link behind) — treat a symlink-to-dir as a
        # file and unlink it.
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _preflight(args: argparse.Namespace, dest: Path, name: str, dataset_id: str) -> int | None:
    """Refuse a bad destination or scaffold identity BEFORE creating anything.

    Returns an exit code to return immediately, or ``None`` to proceed. A
    non-empty / non-directory destination and an invalid ``--name``/``--id``/
    ``--license`` are argument errors (rc 2) — not the post-write "BUG" the
    self-validation guard reports. (``--example`` copies verbatim, so the
    identity flags don't apply to it.)
    """
    if dest.exists():
        if not dest.is_dir():
            print(f"init-dataset: not a directory: {dest}", file=sys.stderr)
            return 2
        if any(dest.iterdir()):
            print(f"init-dataset: destination is not empty: {dest}", file=sys.stderr)
            return 2
    errors: list[str] = []
    if not args.example:
        errors += contract.validate_dataset_meta(
            {
                "contract_version": contract.CONTRACT_VERSION,
                "dataset_id": dataset_id,
                "name": name,
                "status": "scaffold",
                "license": args.license,
                "engine_test_ref": _ZERO_REF,
            }
        )
    if args.ci:
        errors += _ci_param_errors(args.engine_repo, args.engine_secret, args.site_url)
    if errors:
        print("init-dataset: invalid argument(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2
    return None


def _main(args: argparse.Namespace) -> int:
    dest = Path(args.dir)
    name = args.name or dest.resolve().name
    dataset_id = args.dataset_id or _slug(dest.resolve().name)

    if args.example and (args.name is not None or args.dataset_id is not None):
        print(
            "init-dataset: note: --name/--id are ignored with --example "
            "(the example dataset is copied verbatim)",
            file=sys.stderr,
        )

    rc = _preflight(args, dest, name, dataset_id)
    if rc is not None:
        return rc

    created_top = _topmost_created(dest)
    dest.mkdir(parents=True, exist_ok=True)

    try:
        if args.example:
            _copy_example(dest)
        else:
            _scaffold(dest, name=name, dataset_id=dataset_id, license_id=args.license)
        if args.ci:
            _emit_ci(
                dest,
                engine_repo=args.engine_repo,
                engine_secret=args.engine_secret,
                site_url=args.site_url,
            )
    except OSError as e:
        _cleanup(dest, created_top)
        print(f"init-dataset: failed to write {dest}: {e}", file=sys.stderr)
        return 1

    # Self-validate: a fresh init must never be born invalid. Imported here to
    # keep init-dataset's import graph (and --help) free of the validator.
    from kayak.cli.validate_dataset import validate_dataset

    problems = validate_dataset(dest)
    if problems:
        _cleanup(dest, created_top)
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
