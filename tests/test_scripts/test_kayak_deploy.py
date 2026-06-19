"""kayak-deploy staging-path tests (S7 / Batch 4B, decision D2).

The activation phase needs systemd/sqlite on a real host (the clean-VM
rehearsal covers it); everything before it — ref validation,
protected-branch reachability, wheel build, dataset snapshot, contract
validation, digest manifest — is exercised here with ``--stage-only``
against local repositories, no system paths touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from kayak.resources import resource_dir

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "kayak-deploy.sh"


@pytest.fixture
def deploy_root() -> Iterator[Path]:
    """KAYAK_DEPLOY_ROOT on REAL DISK. The activation tests stage release venvs +
    the pre-activation DB backup, which overflow a tmpfs ``/tmp`` — the default
    pytest basetemp on the prod host (a 964 MB tmpfs). /var/tmp is real disk by
    FHS; override the base with ``KAYAK_TEST_DEPLOY_TMPDIR``. (The deployer's own
    scratch already defaults to real disk; this keeps the *test's* release tree
    off tmpfs so the slow suite is runnable on a prod-shaped host — codex live
    review.)"""
    base = os.environ.get("KAYAK_TEST_DEPLOY_TMPDIR", "/var/tmp")
    d = Path(tempfile.mkdtemp(prefix="kayak-deploy-test-", dir=base))
    try:
        yield d / "opt"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# kayak.config (BaseSettings, case-insensitive) reads these env vars at import.
# Each test here builds and imports a STAGED engine in a subprocess, so any of
# these inherited from THIS pytest process would leak into that engine's config —
# and under ``pytest -n`` a concurrent test's value poisons it (a
# ``DATASET_DIR``/``METADATA_DIR`` mismatch even raises ``ValueError`` at import,
# crashing every deploy test). The deployer resolves the config the staged engine
# needs from its own files, so scrub the lot for a hermetic subprocess. Mirrors
# kayak.config's env inputs (field names + the explicit aliases).
_KAYAK_CONFIG_ENV = frozenset(
    {
        "DATABASE_URL",
        "OUTPUT_DIR",
        "DATASET_DIR",
        "METADATA_DIR",
        "MAP_LAYERS_DIR",
        "OSMB_DIR",
        "GAUGE_METADATA_CACHE",
        "SITE_URL",
        "MAINTAINER_EMAIL",
        "SQLITE_PATH",
        "KAYAK_CONFIG_PATH",
    }
)


def _run(args: list[str], env_overrides: dict[str, str | None], timeout: int = 600):
    # Start from a hermetic base: the inherited env MINUS kayak's config vars
    # (see _KAYAK_CONFIG_ENV) so a concurrent test can't poison the staged
    # engine. A None override still REMOVES a key (vs "" which stays "set").
    base = {k: v for k, v in os.environ.items() if k.upper() not in _KAYAK_CONFIG_ENV}
    env = {k: v for k, v in {**base, **env_overrides}.items() if v is not None}
    return subprocess.run(
        ["bash", str(_SCRIPT), *args], env=env, capture_output=True, text=True, timeout=timeout
    )


def _write_conf(tmp_path: Path, engine_repo: str, dataset_repo: str, **extra: str) -> Path:
    conf = tmp_path / "deploy.env"
    lines = [f"ENGINE_REPO={engine_repo}", f"DATASET_REPO={dataset_repo}"]
    lines += [f"{k}={v}" for k, v in extra.items()]
    conf.write_text("\n".join(lines) + "\n")
    return conf


def test_rejects_short_or_nonhex_refs(tmp_path: Path) -> None:
    conf = _write_conf(tmp_path, "/nonexistent", "/nonexistent")
    for bad in ["abc123", "main", "v1.2.0", "g" * 40]:
        proc = _run(
            ["--engine-ref", bad, "--dataset-ref", "0" * 40],
            {"KAYAK_DEPLOY_CONF": str(conf), "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt")},
        )
        assert proc.returncode == 2, (bad, proc.stderr)
        assert "40-hex" in proc.stderr


def test_requires_conf_repos(tmp_path: Path) -> None:
    proc = _run(
        ["--engine-ref", "0" * 40, "--dataset-ref", "0" * 40],
        {
            "KAYAK_DEPLOY_CONF": str(tmp_path / "absent.env"),
            "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt"),
        },
    )
    assert proc.returncode != 0
    assert "ENGINE_REPO" in proc.stderr


@pytest.fixture(scope="module")
def engine_repo(tmp_path_factory) -> tuple[Path, str]:
    """A fresh single-commit repo holding THIS repo's tracked tree, branch
    ``test-main``.

    CI's checkout is detached AND shallow — `--abbrev-ref HEAD` is unusable
    and `git push` from a shallow repo is refused — so neither the real
    branch nor the real history can back the fixture. The staging test
    exercises the deployer's mechanics (clone, wheel build, snapshot,
    digests), not provenance, so a tree-identical fresh commit is enough:
    `git archive HEAD` works regardless of shallow/detached state.
    """
    root = tmp_path_factory.mktemp("enginerepo")
    repo = root / "engine"
    repo.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(_REPO), "archive", "--format=tar", "HEAD"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["tar", "-x", "-C", str(repo)], input=archive.stdout, check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "init", "-q", "-b", "test-main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "engine tree at test HEAD"],
        check=True,
        env=env,
    )
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, sha


@pytest.fixture(scope="module")
def dataset_repo(tmp_path_factory) -> tuple[Path, str]:
    """A local git repo holding the committed fixture dataset."""
    root = tmp_path_factory.mktemp("dsrepo")
    repo = root / "kayak_data"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    # Copy the fixture dataset content as the repo's working tree.
    fixture = resource_dir("data", "example_dataset")
    subprocess.run(["cp", "-R", f"{fixture}/.", str(repo)], check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "fixture dataset"], check=True, env=env
    )
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, sha


@pytest.mark.slow
def test_stage_only_builds_verified_release(tmp_path: Path, engine_repo, dataset_repo) -> None:
    """End-to-end staging: wheel from THIS engine repo's HEAD + the fixture
    dataset -> venv, contract validation, runtime config, digest manifest."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
    )
    root = tmp_path / "opt"
    proc = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--stage-only"],
        {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            # Hermetic: don't let the operator's ~/.config/kayak/.env leak in.
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            # On a real host /etc/kayak/env supplies this; the fixture dataset
            # is publishable, so emit-config requires it.
            "SITE_URL": "https://levels.example.org",
            # Operational token — must NOT survive into the release copy.
            "NTFY_TOPIC": "kayak-test-secret-topic",
            # Credential (SecretStr, unwrapped by emit-config when readable) —
            # a root-run staging emit must never persist it into the retained
            # normalized config (PR #190 third-round P1).
            "TURNSTILE_SECRET": "kayak-test-turnstile-secret",
        },
    )
    assert proc.returncode == 0, proc.stderr
    release_dir = Path(proc.stdout.strip().splitlines()[-1])
    assert release_dir.is_dir()

    manifest = json.loads((release_dir / "release.json").read_text())
    assert manifest["engine_sha"] == engine_sha
    assert manifest["dataset_sha"] == ds_sha
    assert manifest["deployer_version"] == 1
    assert len(manifest["wheel_sha256"]) == 64
    # PR #190 review: hash-locked deps + the emitted runtime config are part
    # of the verified release identity.
    assert len(manifest["requirements_lock_sha256"]) == 64
    assert len(manifest["runtime_config_sha256"]) == 64
    assert manifest["release_id"] == release_dir.name

    # The staged release is self-contained: venv with the engine, dataset
    # snapshot that passes the contract gate, non-secret runtime config.
    assert (release_dir / "venv/bin/levels").exists()
    assert (release_dir / "dataset/dataset.yaml").is_file()
    assert (release_dir / "runtime-config.json").is_file()
    assert (release_dir / manifest["wheel"]).is_file()

    # No 'current' symlink: stage-only must not activate.
    assert not (root / "current").exists()

    # Identity stability (PR #190 re-review P1): identical inputs must mint
    # the SAME release id — the config digest is computed over a normalized
    # view excluding staging-local paths.
    proc_same = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--stage-only"],
        {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "SITE_URL": "https://levels.example.org",
            "NTFY_TOPIC": "kayak-test-secret-topic",
        },
    )
    assert proc_same.returncode == 0, proc_same.stderr
    assert Path(proc_same.stdout.strip().splitlines()[-1]) == release_dir
    assert "already staged" in proc_same.stdout
    # Reuse is READ-ONLY (codex live review P2): the same-refs stage re-verified
    # the existing release's dataset (diff vs the tar) instead of rm-ing +
    # re-extracting it, so the (possibly live) release's dataset is untouched.
    assert (release_dir / "dataset/dataset.yaml").is_file()

    # The release-retained config carries no staging-scratch paths and no
    # operational tokens (PR #190 re-review: ntfy/hc URLs must not widen
    # their lifetime into retained release dirs).
    stored = json.loads((release_dir / "runtime-config.json").read_text())
    flat = json.dumps(stored)
    assert "/tmp" not in flat.replace(str(release_dir), "")
    assert "ntfy_topic" not in stored
    assert "kayak-test-secret-topic" not in flat
    assert not any(k.startswith("hc_") for k in stored)
    assert "dataset_dir" not in stored  # path-local fields excluded
    # No credential-shaped field nor its value (secret/password/token filter).
    assert "turnstile_secret" not in stored
    assert "kayak-test-turnstile-secret" not in flat

    # PR #190 review P1: a non-secret runtime-config input change (e.g.
    # SITE_URL in /etc/kayak/env) must produce a DIFFERENT release — never
    # reuse of a stale runtime-config/docroot.
    proc2 = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--stage-only"],
        {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "SITE_URL": "https://other.example.org",
        },
    )
    assert proc2.returncode == 0, proc2.stderr
    release_dir2 = Path(proc2.stdout.strip().splitlines()[-1])
    assert release_dir2 != release_dir, "config change must mint a new release id"

    # A corrupted retained artifact in an existing release must fail closed on
    # reuse (PR #190 4th-round P2) — not silently re-activate from it.
    (release_dir / "requirements-prod.lock").write_text("tampered\n")
    proc3 = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--stage-only"],
        {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "SITE_URL": "https://levels.example.org",
        },
    )
    assert proc3.returncode != 0
    assert "VERIFY FAILED" in proc3.stderr


def test_app_env_keys_read_as_data_not_sourced(tmp_path: Path, engine_repo, dataset_repo) -> None:
    """The app user's ``~/.config/kayak/.env`` is read as DATA, never shell-
    ``source``d by the root orchestrator (codex/claude live review P1/F3): a line
    that would run a command if sourced must NOT execute, only the allowlisted
    data keys are picked up, and a non-allowlisted key can't override a deploy
    control."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    conf = _write_conf(
        tmp_path, str(eng_repo), str(ds_repo), ENGINE_BRANCH="test-main", DATASET_BRANCH="main"
    )
    marker = tmp_path / "PWNED"
    app_env = tmp_path / "app.env"
    # SITE_URL only here (NOT in the process env); a line that would touch the
    # marker if the file were sourced; and a deploy control the app user must not
    # be able to override.
    app_env.write_text(
        "SITE_URL=https://fromapp.example.org\n"
        f"EVIL=$(touch {marker})\n"
        "ENGINE_REPO=/should/not/override\n"
    )
    proc = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--stage-only"],
        {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt"),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "KAYAK_APP_ENV": str(app_env),  # set directly (no getent on macOS/CI)
            # SITE_URL deliberately absent from the env — must come from app_env.
            # Hermetic: a dev box's ~/.config/kayak/.env leaks METADATA_DIR into
            # os.environ (config.py's import-time load_dotenv), which would clash
            # with the deployer's DATASET_DIR override. Remove it entirely.
            "METADATA_DIR": None,
        },
    )
    # Stage succeeded → ENGINE_REPO was NOT overridden (else the clone would
    # fail), and the app-owned line did NOT execute.
    assert proc.returncode == 0, proc.stderr
    assert not marker.exists(), "a line in the app-owned .env executed as the orchestrator"
    # SITE_URL was read as data → it reached the staged (normalized) config.
    release_dir = Path(proc.stdout.strip().splitlines()[-1])
    stored = json.loads((release_dir / "runtime-config.json").read_text())
    assert "fromapp.example.org" in json.dumps(stored)


def test_unreachable_ref_rejected(tmp_path: Path, engine_repo, dataset_repo) -> None:
    """A SHA not on the protected branch fails validation (the trust anchor)."""
    ds_repo, _ds_sha = dataset_repo
    eng_repo, _engine_sha = engine_repo
    bogus = "0123456789abcdef0123456789abcdef01234567"
    conf = _write_conf(
        tmp_path, str(eng_repo), str(ds_repo), ENGINE_BRANCH="test-main", DATASET_BRANCH="main"
    )
    proc = _run(
        ["--engine-ref", bogus, "--dataset-ref", bogus, "--stage-only"],
        {"KAYAK_DEPLOY_CONF": str(conf), "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt")},
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr or "not reachable" in proc.stderr


def _init_db(db: Path, dataset_dir: Path) -> None:
    """Create + populate a DB the activation path can migrate/sync/build."""
    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO / "src"),
        "DATABASE_URL": f"sqlite:///{db}",
        "DATASET_DIR": str(dataset_dir),
        "HOME": str(db.parent),
        "SUDO_USER": "",
    }
    for cmd in (["init-db"], ["sync-metadata"]):
        subprocess.run(
            [sys.executable, "-m", "kayak.cli.main", *cmd],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.mark.slow
def test_activation_rolls_back_db_symlink_and_config_on_failed_health(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """Full activation path with stubbed systemctl + config installer: a first
    release activates; a second release with a failing health check must roll
    back the symlink, the DB, AND the runtime config to the first release
    (PR #190 third-round P1 — config rollback)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")

    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)

    root = deploy_root  # real disk: the DB backup + venvs overflow a tmpfs /tmp
    docroot = tmp_path / "docroot"  # the shared docroot cache (KAYAK_DOCROOT)
    runtime_config = tmp_path / "runtime-config.json"
    systemctl_log = tmp_path / "systemctl.log"
    runuser_log = tmp_path / "runuser.log"

    # Config installer stub: the real one merges root-only secrets + installs
    # 0640 root:www-data; here it just records the emitted (dry-run) config so
    # we can assert what PHP would read.
    installer = tmp_path / "install-config.sh"
    installer.write_text(f'#!/bin/sh\ncat > "{runtime_config}"\n')
    installer.chmod(0o755)
    systemctl = tmp_path / "systemctl.sh"
    # Record every call; `is-active` reports inactive (exit 1) so the quiesce
    # loop drains immediately; `show -p ExecStart` reports the consumer running
    # from $ROOT/current so the cutover-verification gate passes.
    systemctl.write_text(
        f'#!/bin/sh\necho "$@" >> "{systemctl_log}"\n'
        'case "$1" in\n'
        "  is-active) exit 1 ;;\n"
        f'  show) echo "{{ path={root}/current/venv/bin/levels }}" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    systemctl.chmod(0o755)
    # runuser shim: exercises the PRIVILEGED branch of run_app without real
    # root (PR #190 4th-round P1). Invoked `shim -u USER -- cmd...`; records the
    # command and runs it as the current user (KAYAK_APP_USER is set to us).
    runuser = tmp_path / "runuser.sh"
    # Drop the leading `-u USER --`, record the command, run it as us.
    runuser.write_text(f'#!/bin/sh\nshift 3\necho "$@" >> "{runuser_log}"\nexec "$@"\n')
    runuser.chmod(0o755)
    me = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()

    host_env = tmp_path / "host.env"

    def activate(site_url: str, *, health_url: str | None) -> subprocess.CompletedProcess[str]:
        conf = _write_conf(
            tmp_path,
            str(eng_repo),
            str(ds_repo),
            ENGINE_BRANCH="test-main",
            DATASET_BRANCH="main",
            SERVING_CUTOVER="yes",
        )
        # SITE_URL + SQLITE_PATH come from the host-env FILE, not injected into
        # the subprocess env — this exercises the real KAYAK_HOST_ENV sourcing
        # path the live host depends on (PR #190 live review P1), which earlier
        # tests bypassed by injecting them directly.
        host_env.write_text(f"SITE_URL={site_url}\nSQLITE_PATH={db}\n")
        env = {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "KAYAK_DOCROOT": str(docroot),
            "KAYAK_HOST_ENV": str(host_env),
            "KAYAK_RUNTIME_CONFIG": str(runtime_config),
            "KAYAK_CONFIG_INSTALLER": str(installer),
            "KAYAK_SYSTEMCTL": str(systemctl),
            **_serving_knobs(tmp_path, docroot),
            # Force the privileged branch with a same-user runuser shim so the
            # app-user DB boundary (backup/restore/build via run_app) is
            # actually exercised — it is a pass-through otherwise.
            "KAYAK_PRIVILEGED": "yes",
            "KAYAK_APP_USER": me,
            "KAYAK_RUNUSER": str(runuser),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "KAYAK_UNITS": "kayak-pipeline.timer",
        }
        if health_url is not None:
            env["HEALTH_URL"] = health_url
        return _run(["--engine-ref", engine_sha, "--dataset-ref", ds_sha], env, timeout=900)

    # First activation succeeds (no health probe).
    first = activate("https://first.example.org", health_url=None)
    assert first.returncode == 0, first.stderr
    assert (root / "current").is_symlink()
    release1 = (root / "current").resolve()
    assert "first.example.org" in runtime_config.read_text()
    assert not (root / "maintenance").exists()
    # The DB backup, migrate, sync, import, and build all crossed the app-user
    # boundary (routed through the runuser shim).
    rlog = runuser_log.read_text()
    assert ".backup" in rlog
    for cmd in ("migrate", "sync-metadata", "import-metadata", "build"):
        assert cmd in rlog, cmd
    # Fail-closed default: a deploy WITHOUT --allow-deletes must not pass the
    # flag to sync-metadata, so the sync refuses any delete-containing diff.
    sync_lines = [ln for ln in rlog.splitlines() if "sync-metadata" in ln]
    assert sync_lines and all("--allow-deletes" not in ln for ln in sync_lines), sync_lines

    # #3: the docroot is the shared cache (KAYAK_DOCROOT), NOT inside the
    # release — the release tree holds venv+dataset only.
    assert any(docroot.rglob("*")), "build wrote the shared docroot cache"
    assert not (release1 / "docroot").exists(), "release must not carry a docroot"

    def _docroot_text() -> str:
        return "".join(p.read_text(errors="ignore") for p in docroot.rglob("*") if p.is_file())

    assert "first.example.org" in _docroot_text()

    # Second activation: a different SITE_URL mints a new release, but the
    # health probe fails (port 1 → connection refused) → rollback.
    second = activate("https://second.example.org", health_url="http://127.0.0.1:1/")
    assert second.returncode != 0
    # Symlink, config, and DB are all back to release 1.
    assert (root / "current").resolve() == release1
    cfg = runtime_config.read_text()
    assert "first.example.org" in cfg
    assert "second.example.org" not in cfg
    # The rollback DB restore also crossed the app-user boundary.
    assert ".restore" in runuser_log.read_text()
    # #3: the shared docroot is OUTSIDE the release, so the symlink swap doesn't
    # restore it — rollback rebuilds it from the previous release's venv. The
    # failed release's content is gone; release 1's content is back.
    assert "rebuilding" in second.stderr
    dt = _docroot_text()
    assert "second.example.org" not in dt
    assert "first.example.org" in dt
    # DB still queryable and consumers restarted, maintenance cleared.
    import sqlite3

    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM reach").fetchone()[0]
    assert n > 0
    assert not (root / "maintenance").exists()
    assert "start kayak-pipeline.timer" in systemctl_log.read_text()


@pytest.mark.slow
def test_allow_deletes_flag_reaches_sync_metadata(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """``--allow-deletes`` is plumbed through to the single ``sync-metadata``
    invocation. Paired with the fail-closed assertion in the activation test
    above (a default run carries NO ``--allow-deletes``), this pins both
    directions of the gate so a refactor can't silently drop or invert it — the
    catastrophic-on-prod case, since the deployer's whole job here is this
    passthrough. The fixture diff is a no-op vs the just-synced DB, so the flag
    is inert; we assert plumbing, not an actual deletion."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")

    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)

    root = deploy_root
    docroot = tmp_path / "docroot"
    runtime_config = tmp_path / "runtime-config.json"
    runuser_log = tmp_path / "runuser.log"

    installer = tmp_path / "install-config.sh"
    installer.write_text(f'#!/bin/sh\ncat > "{runtime_config}"\n')
    installer.chmod(0o755)
    systemctl = tmp_path / "systemctl.sh"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  is-active) exit 1 ;;\n"
        f'  show) echo "{{ path={root}/current/venv/bin/levels }}" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    systemctl.chmod(0o755)
    # Logging runuser shim (records full argv) so the sync call is readable back.
    runuser = tmp_path / "runuser.sh"
    runuser.write_text(f'#!/bin/sh\nshift 3\necho "$@" >> "{runuser_log}"\nexec "$@"\n')
    runuser.chmod(0o755)
    me = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()

    host_env = tmp_path / "host.env"
    host_env.write_text(f"SITE_URL=https://deletes.example.org\nSQLITE_PATH={db}\n")
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
        SERVING_CUTOVER="yes",
    )
    env = {
        "KAYAK_DEPLOY_CONF": str(conf),
        "KAYAK_DEPLOY_ROOT": str(root),
        "KAYAK_DOCROOT": str(docroot),
        "KAYAK_HOST_ENV": str(host_env),
        "KAYAK_RUNTIME_CONFIG": str(runtime_config),
        "KAYAK_CONFIG_INSTALLER": str(installer),
        "KAYAK_SYSTEMCTL": str(systemctl),
        **_serving_knobs(tmp_path, docroot),
        "KAYAK_PRIVILEGED": "yes",
        "KAYAK_APP_USER": me,
        "KAYAK_RUNUSER": str(runuser),
        "HOME": str(tmp_path),
        "SUDO_USER": "",
        "KAYAK_UNITS": "kayak-pipeline.timer",
    }
    proc = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--allow-deletes"],
        env,
        timeout=900,
    )
    assert proc.returncode == 0, proc.stderr
    sync_lines = [ln for ln in runuser_log.read_text().splitlines() if "sync-metadata" in ln]
    assert len(sync_lines) == 1, sync_lines
    assert "--allow-deletes" in sync_lines[0], sync_lines[0]


def _activation_stubs(tmp_path: Path, root: Path, runtime_config: Path) -> dict[str, str]:
    """Recording systemctl + config-installer + same-user runuser stubs."""
    installer = tmp_path / "install-config.sh"
    installer.write_text(f'#!/bin/sh\ncat > "{runtime_config}"\n')
    installer.chmod(0o755)
    systemctl = tmp_path / "systemctl.sh"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  is-active) exit 1 ;;\n"
        f'  show) echo "{{ path={root}/current/venv/bin/levels }}" ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    systemctl.chmod(0o755)
    runuser = tmp_path / "runuser.sh"
    runuser.write_text('#!/bin/sh\nshift 3\nexec "$@"\n')
    runuser.chmod(0o755)
    me = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()
    return {
        "installer": str(installer),
        "systemctl": str(systemctl),
        "runuser": str(runuser),
        "me": me,
    }


def _serving_knobs(tmp_path: Path, docroot: Path) -> dict[str, str]:
    """Good nginx/FPM serving fixtures + the knobs the gate now REQUIRES under
    SERVING_CUTOVER=yes (fail-closed, PR #195 review #1). nginx roots only the
    docroot + the ACME root; the FPM open_basedir leads with the docroot."""
    nginx = tmp_path / "levels-common.conf"
    nginx.write_text(f"    root {docroot};\n    root /var/www/certbot;\n")
    fpm = tmp_path / "kayak-pool.conf"
    fpm.write_text(f"php_admin_value[open_basedir] = {docroot}:{tmp_path}/var:{tmp_path}/DB\n")
    return {"KAYAK_NGINX_DOCROOT_CONF": str(nginx), "KAYAK_FPM_POOL": str(fpm)}


@pytest.mark.slow
def test_activation_prunes_old_releases(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """Old releases are pruned after a successful activation, but current and
    previous are always kept (PR #190 live review P2 — unbounded venv growth)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")
    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)
    root = deploy_root  # real disk: 3 release venvs + backups overflow a tmpfs /tmp
    runtime_config = tmp_path / "runtime-config.json"
    host_env = tmp_path / "host.env"
    stubs = _activation_stubs(tmp_path, root, runtime_config)
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
        SERVING_CUTOVER="yes",
    )

    def activate(site_url: str) -> Path:
        host_env.write_text(f"SITE_URL={site_url}\nSQLITE_PATH={db}\n")
        env = {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "KAYAK_DOCROOT": str(tmp_path / "docroot"),
            "KAYAK_HOST_ENV": str(host_env),
            "KAYAK_RUNTIME_CONFIG": str(runtime_config),
            "KAYAK_CONFIG_INSTALLER": stubs["installer"],
            "KAYAK_SYSTEMCTL": stubs["systemctl"],
            "KAYAK_PRIVILEGED": "yes",
            "KAYAK_APP_USER": stubs["me"],
            "KAYAK_RUNUSER": stubs["runuser"],
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "KAYAK_UNITS": "kayak-pipeline.timer",
            **_serving_knobs(tmp_path, tmp_path / "docroot"),
            # Keep ONLY current + previous, so the 3rd activation prunes the 1st.
            "KAYAK_KEEP_RELEASES": "0",
        }
        p = _run(["--engine-ref", engine_sha, "--dataset-ref", ds_sha], env, timeout=900)
        assert p.returncode == 0, p.stderr
        return (root / "current").resolve()

    r1 = activate("https://one.example.org")
    r2 = activate("https://two.example.org")
    r3 = activate("https://three.example.org")
    assert r1 != r2 != r3

    remaining = {d.name for d in (root / "releases").iterdir()}
    # current (r3) + previous (r2) kept; the oldest (r1) pruned.
    assert r3.name in remaining
    assert r2.name in remaining
    assert r1.name not in remaining, "oldest release should be pruned with KEEP=0"


@pytest.mark.slow
def test_rollback_rebuilds_docroot_when_build_fails_midway(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """PR #192 review #1: ``DOCROOT_BUILT`` is armed BEFORE the build, so a build
    that mutates the shared docroot in place and THEN exits non-zero still drives
    the rollback rebuild. Otherwise the failed release's partial docroot is what
    nginx serves (the flag-after-build bug)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")
    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)
    root = deploy_root
    docroot = tmp_path / "docroot"
    runtime_config = tmp_path / "runtime-config.json"
    # Reuse the installer + systemctl stubs; swap in a runuser shim that fails the
    # FORWARD build exactly once, after a partial in-place write, so we exercise
    # the "build started then failed" rollback path. The once-guard lets the
    # rollback rebuild (the second build) run for real.
    base = _activation_stubs(tmp_path, root, runtime_config)
    fail_sentinel = tmp_path / "FAIL_BUILD"
    once_guard = tmp_path / "BUILD_FAILED_ONCE"
    runuser = tmp_path / "runuser-failbuild.sh"
    runuser.write_text(
        "#!/bin/sh\n"
        "shift 3\n"
        'case "$*" in\n'
        "  *' build')\n"
        f'    if [ -f "{fail_sentinel}" ] && [ ! -f "{once_guard}" ]; then\n'
        f'      touch "{once_guard}"\n'
        '      for a in "$@"; do\n'
        '        case "$a" in OUTPUT_DIR=*) out="${a#OUTPUT_DIR=}" ;; esac\n'
        "      done\n"
        '      mkdir -p "$out"\n'
        "      printf 'https://second.example.org/partial\\n' > \"$out/partial-FAIL.html\"\n"
        "      exit 1\n"
        "    fi\n"
        "    ;;\n"
        "esac\n"
        'exec "$@"\n'
    )
    runuser.chmod(0o755)
    host_env = tmp_path / "host.env"
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
        SERVING_CUTOVER="yes",
    )

    def activate(site_url: str) -> subprocess.CompletedProcess[str]:
        host_env.write_text(f"SITE_URL={site_url}\nSQLITE_PATH={db}\n")
        env = {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "KAYAK_DOCROOT": str(docroot),
            "KAYAK_HOST_ENV": str(host_env),
            "KAYAK_RUNTIME_CONFIG": str(runtime_config),
            "KAYAK_CONFIG_INSTALLER": base["installer"],
            "KAYAK_SYSTEMCTL": base["systemctl"],
            "KAYAK_PRIVILEGED": "yes",
            "KAYAK_APP_USER": base["me"],
            "KAYAK_RUNUSER": str(runuser),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "KAYAK_UNITS": "kayak-pipeline.timer",
            **_serving_knobs(tmp_path, docroot),
        }
        return _run(["--engine-ref", engine_sha, "--dataset-ref", ds_sha], env, timeout=900)

    def _docroot_text() -> str:
        return "".join(p.read_text(errors="ignore") for p in docroot.rglob("*") if p.is_file())

    # First activation succeeds (sentinel not yet armed) and seeds the docroot.
    first = activate("https://first.example.org")
    assert first.returncode == 0, first.stderr
    assert "first.example.org" in _docroot_text()

    # Arm the next build to mutate the docroot then fail.
    fail_sentinel.write_text("x")
    second = activate("https://second.example.org")
    _ctx = f"\n--- STDOUT ---\n{second.stdout}\n--- STDERR ---\n{second.stderr}"
    assert second.returncode != 0, _ctx
    # The build failed mid-way, but the rebuild still ran → DOCROOT_BUILT was 1
    # (the fix); the bug would skip it.
    assert "rebuilding" in second.stderr, _ctx
    dt = _docroot_text()
    assert "first.example.org" in dt
    assert not (docroot / "partial-FAIL.html").exists(), (
        "the rollback rebuild's orphan sweep must remove the failed build's partial file"
    )
    assert "second.example.org" not in dt


@pytest.mark.slow
def test_rollback_rebuilds_docroot_with_absolute_current_symlink(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """PR #192 review #3: rollback normalizes an ABSOLUTE ``current`` symlink
    target (a manual-recovery shape) so the docroot rebuild reads the previous
    release from its real path, not ``$ROOT//opt/...``."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")
    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)
    root = deploy_root
    docroot = tmp_path / "docroot"
    runtime_config = tmp_path / "runtime-config.json"
    base = _activation_stubs(tmp_path, root, runtime_config)
    host_env = tmp_path / "host.env"
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
        SERVING_CUTOVER="yes",
    )

    def activate(site_url: str, *, health_url: str | None) -> subprocess.CompletedProcess[str]:
        host_env.write_text(f"SITE_URL={site_url}\nSQLITE_PATH={db}\n")
        env = {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "KAYAK_DOCROOT": str(docroot),
            "KAYAK_HOST_ENV": str(host_env),
            "KAYAK_RUNTIME_CONFIG": str(runtime_config),
            "KAYAK_CONFIG_INSTALLER": base["installer"],
            "KAYAK_SYSTEMCTL": base["systemctl"],
            "KAYAK_PRIVILEGED": "yes",
            "KAYAK_APP_USER": base["me"],
            "KAYAK_RUNUSER": base["runuser"],
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "KAYAK_UNITS": "kayak-pipeline.timer",
            **_serving_knobs(tmp_path, docroot),
        }
        if health_url is not None:
            env["HEALTH_URL"] = health_url
        return _run(["--engine-ref", engine_sha, "--dataset-ref", ds_sha], env, timeout=900)

    first = activate("https://first.example.org", health_url=None)
    assert first.returncode == 0, first.stderr
    release1 = (root / "current").resolve()

    # Simulate a manual recovery that left `current` pointing at an ABSOLUTE path
    # (the deployer itself always writes a relative `releases/<id>`).
    (root / "current").unlink()
    (root / "current").symlink_to(release1)
    assert os.path.isabs(os.readlink(root / "current"))

    second = activate("https://second.example.org", health_url="http://127.0.0.1:1/")
    assert second.returncode != 0
    assert (root / "current").resolve() == release1
    assert "rebuilding" in second.stderr
    # The rebuild must SUCCEED — the failure warning ("docroot rebuild from …
    # failed") would mean PREV_DIR doubled the root prefix (the bug).
    assert "docroot rebuild from" not in second.stderr
    dt = "".join(p.read_text(errors="ignore") for p in docroot.rglob("*") if p.is_file())
    assert "first.example.org" in dt
    assert "second.example.org" not in dt


@pytest.mark.slow
def test_serving_path_gate_refuses_half_cutover(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """The SERVING_CUTOVER gate refuses a half-cutover before any mutation: a
    consumer OUTPUT_DIR that isn't KAYAK_DOCROOT, nginx not rooting at it, a
    clobbered certbot ACME root, or an FPM open_basedir missing it (the gate
    deferred from PR #190/#192, + PR #194 review #2 certbot check)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")
    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)
    root = deploy_root
    docroot = tmp_path / "docroot"
    runtime_config = tmp_path / "runtime-config.json"
    runuser_log = tmp_path / "runuser.log"
    me = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()

    installer = tmp_path / "install-config.sh"
    installer.write_text(f'#!/bin/sh\ncat > "{runtime_config}"\n')
    installer.chmod(0o755)
    runuser = tmp_path / "runuser.sh"
    runuser.write_text(f'#!/bin/sh\nshift 3\necho "$@" >> "{runuser_log}"\nexec "$@"\n')
    runuser.chmod(0o755)

    # systemctl stub: ExecStart → contents of execfile (the run-from path under
    # test); Environment → contents of envfile (the OUTPUT_DIR under test);
    # is-active → inactive so the drain loop exits at once.
    execfile = tmp_path / "stub-exec.txt"
    execfile.write_text(f"{root}/current/venv/bin/levels pipeline")
    envfile = tmp_path / "stub-env.txt"
    envfile.write_text(f"OUTPUT_DIR={docroot} DATASET_DIR={root}/current/dataset")
    systemctl = tmp_path / "systemctl.sh"
    systemctl.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = show ]; then\n'
        f'  if [ "$3" = ExecStart ]; then cat "{execfile}";\n'
        f'  elif [ "$3" = Environment ]; then cat "{envfile}"; fi\n'
        "  exit 0\n"
        "fi\n"
        'case "$1" in is-active) exit 1 ;; *) exit 0 ;; esac\n'
    )
    systemctl.chmod(0o755)

    nginx_conf = tmp_path / "levels-common.conf"
    fpm_pool = tmp_path / "kayak.conf"

    def good_nginx() -> None:
        nginx_conf.write_text(f"    root {docroot};\n    root /var/www/certbot;\n")

    def good_fpm() -> None:
        fpm_pool.write_text(f"php_admin_value[open_basedir] = {docroot}:/home/x/var:/home/x/DB\n")

    good_nginx()
    good_fpm()
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
        SERVING_CUTOVER="yes",
    )
    host_env = tmp_path / "host.env"
    host_env.write_text(f"SITE_URL=https://x.example.org\nSQLITE_PATH={db}\n")

    # A stub the gate can be pointed at via KAYAK_ENGINE_BIN to make
    # `render-units --list-units` produce nothing (the fail-closed path).
    engine_noop = tmp_path / "engine-noop.sh"
    engine_noop.write_text("#!/bin/sh\nexit 0\n")
    engine_noop.chmod(0o755)

    def activate(
        *, with_knobs: bool = True, engine_bin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            "KAYAK_DOCROOT": str(docroot),
            "KAYAK_HOST_ENV": str(host_env),
            "KAYAK_RUNTIME_CONFIG": str(runtime_config),
            "KAYAK_CONFIG_INSTALLER": str(installer),
            "KAYAK_SYSTEMCTL": str(systemctl),
            "KAYAK_PRIVILEGED": "yes",
            "KAYAK_APP_USER": me,
            "KAYAK_RUNUSER": str(runuser),
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            "KAYAK_UNITS": "kayak-pipeline.timer",
        }
        if with_knobs:
            env["KAYAK_NGINX_DOCROOT_CONF"] = str(nginx_conf)
            env["KAYAK_FPM_POOL"] = str(fpm_pool)
        if engine_bin is not None:
            env["KAYAK_ENGINE_BIN"] = engine_bin
        return _run(["--engine-ref", engine_sha, "--dataset-ref", ds_sha], env, timeout=900)

    # 1) Everything points at the docroot → activation succeeds (and stages the
    #    release, which the gate-refusal cases below reuse — fast).
    ok = activate()
    assert ok.returncode == 0, ok.stderr

    # 2) A consumer building into the wrong tree → refuse.
    envfile.write_text(f"OUTPUT_DIR=/wrong/tree DATASET_DIR={root}/current/dataset")
    bad = activate()
    assert bad.returncode != 0
    assert "OUTPUT_DIR != KAYAK_DOCROOT" in bad.stderr
    envfile.write_text(f"OUTPUT_DIR={docroot} DATASET_DIR={root}/current/dataset")

    # 2b) An engine unit still running from the OLD checkout (not $ROOT/current) →
    #     refuse. The verified set is sourced from `render-units --list-units`, so
    #     this exercises that enumeration too (an empty list would skip everything).
    execfile.write_text("/home/pat/.venv/bin/levels pipeline")
    bad = activate()
    assert bad.returncode != 0
    assert "does not run from" in bad.stderr
    execfile.write_text(f"{root}/current/venv/bin/levels pipeline")

    # 2c) `render-units --list-units` produces nothing (e.g. an engine ref too old
    #     to support it) → fail-closed refuse, not a fail-open skip (PR #196 review #2).
    bad = activate(engine_bin=str(engine_noop))
    assert bad.returncode != 0
    assert "could not enumerate the engine units" in bad.stderr

    # 3) nginx still rooting the legacy docroot → refuse.
    nginx_conf.write_text("    root /home/pat/public_html;\n    root /var/www/certbot;\n")
    bad = activate()
    assert bad.returncode != 0 and "nginx does not root at" in bad.stderr
    good_nginx()

    # 4) certbot ACME root clobbered by a blanket root-substitution → refuse.
    nginx_conf.write_text(f"    root {docroot};\n")
    bad = activate()
    assert bad.returncode != 0 and "certbot ACME root is missing" in bad.stderr
    good_nginx()

    # 5) FPM open_basedir not leading with the docroot → refuse.
    fpm_pool.write_text("php_admin_value[open_basedir] = /home/pat/public_html:/home/x/DB\n")
    bad = activate()
    assert bad.returncode != 0 and "open_basedir does not lead with" in bad.stderr
    good_fpm()

    # 6) A leftover legacy root alongside the docroot + certbot → refuse: presence
    #    isn't enough, every root must be the docroot or the ACME root, else nginx
    #    serves the LAST one (PR #195 review #2).
    nginx_conf.write_text(
        f"    root {docroot};\n    root /home/pat/public_html;\n    root /var/www/certbot;\n"
    )
    bad = activate()
    assert bad.returncode != 0 and "unexpected nginx root" in bad.stderr
    good_nginx()

    # 7) SERVING_CUTOVER=yes but the serving knobs unset → fail-closed, not skipped
    #    (PR #195 review #1 — warn-skip would let an nginx half-cutover through).
    bad = activate(with_knobs=False)
    assert bad.returncode != 0
    assert "KAYAK_NGINX_DOCROOT_CONF" in bad.stderr

    # 8) A `;`-commented open_basedir must not satisfy the anchored check → refuse
    #    (PR #195 review #3).
    fpm_pool.write_text(f"; php_admin_value[open_basedir] = {docroot}:/home/x/DB\n")
    bad = activate()
    assert bad.returncode != 0 and "open_basedir does not lead with" in bad.stderr
    good_fpm()

    # None of the refusals mutated the DB (the gate is pre-backup).
    assert ".backup" not in runuser_log.read_text() or runuser_log.read_text().count(".backup") == 1


@pytest.mark.slow
def test_quiesce_timeout_backs_out_maintenance(
    tmp_path: Path, deploy_root: Path, engine_repo, dataset_repo
) -> None:
    """A consumer that won't drain leaves NOTHING mutated, so the timeout must
    back out maintenance + restart consumers rather than `exit 1` into a stuck
    down state (PR #192 review — the quiesce-timeout sibling of the errtrace gap)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = resource_dir("data", "example_dataset")
    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)
    root = deploy_root
    runtime_config = tmp_path / "runtime-config.json"
    runuser_log = tmp_path / "runuser.log"
    systemctl_log = tmp_path / "systemctl.log"
    me = subprocess.run(["id", "-un"], capture_output=True, text=True, check=True).stdout.strip()

    installer = tmp_path / "install-config.sh"
    installer.write_text(f'#!/bin/sh\ncat > "{runtime_config}"\n')
    installer.chmod(0o755)
    runuser = tmp_path / "runuser.sh"
    runuser.write_text(f'#!/bin/sh\nshift 3\necho "$@" >> "{runuser_log}"\nexec "$@"\n')
    runuser.chmod(0o755)
    # is-active reports ACTIVE (exit 0) forever → the drain loop never converges.
    systemctl = tmp_path / "systemctl.sh"
    systemctl.write_text(
        f'#!/bin/sh\necho "$@" >> "{systemctl_log}"\n'
        'if [ "$1" = show ]; then\n'
        f'  if [ "$3" = ExecStart ]; then echo "{root}/current/venv/bin/levels pipeline"; fi\n'
        "  exit 0\n"
        "fi\n"
        'case "$1" in is-active) exit 0 ;; *) exit 0 ;; esac\n'
    )
    systemctl.chmod(0o755)
    conf = _write_conf(
        tmp_path,
        str(eng_repo),
        str(ds_repo),
        ENGINE_BRANCH="test-main",
        DATASET_BRANCH="main",
        SERVING_CUTOVER="yes",
    )
    host_env = tmp_path / "host.env"
    host_env.write_text(f"SITE_URL=https://x.example.org\nSQLITE_PATH={db}\n")
    env = {
        "KAYAK_DEPLOY_CONF": str(conf),
        "KAYAK_DEPLOY_ROOT": str(root),
        "KAYAK_DOCROOT": str(tmp_path / "docroot"),
        "KAYAK_HOST_ENV": str(host_env),
        "KAYAK_RUNTIME_CONFIG": str(runtime_config),
        "KAYAK_CONFIG_INSTALLER": str(installer),
        "KAYAK_SYSTEMCTL": str(systemctl),
        "KAYAK_PRIVILEGED": "yes",
        "KAYAK_APP_USER": me,
        "KAYAK_RUNUSER": str(runuser),
        "HOME": str(tmp_path),
        "SUDO_USER": "",
        "KAYAK_UNITS": "kayak-pipeline.timer",
        **_serving_knobs(tmp_path, tmp_path / "docroot"),
        # Tiny drain bound so the loop times out in ~2 s, not 120 s.
        "KAYAK_DRAIN_TIMEOUT": "2",
        "KAYAK_DRAIN_INTERVAL": "1",
    }
    p = _run(["--engine-ref", engine_sha, "--dataset-ref", ds_sha], env, timeout=900)
    assert p.returncode != 0
    assert "still active after" in p.stderr
    # Backed out: maintenance cleared, consumers restarted, DB never touched.
    assert not (root / "maintenance").exists()
    assert "start kayak-pipeline.timer" in systemctl_log.read_text()
    assert not runuser_log.exists() or ".backup" not in runuser_log.read_text()
