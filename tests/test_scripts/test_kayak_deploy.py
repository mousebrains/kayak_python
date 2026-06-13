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
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "kayak-deploy.sh"


def _run(args: list[str], env_overrides: dict[str, str], timeout: int = 600):
    env = {**os.environ, **env_overrides}
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
    fixture = _REPO / "tests" / "fixtures" / "dataset"
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
    tmp_path: Path, engine_repo, dataset_repo
) -> None:
    """Full activation path with stubbed systemctl + config installer: a first
    release activates; a second release with a failing health check must roll
    back the symlink, the DB, AND the runtime config to the first release
    (PR #190 third-round P1 — config rollback)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = _REPO / "tests" / "fixtures" / "dataset"

    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)

    root = tmp_path / "opt"
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
            "KAYAK_HOST_ENV": str(host_env),
            "KAYAK_RUNTIME_CONFIG": str(runtime_config),
            "KAYAK_CONFIG_INSTALLER": str(installer),
            "KAYAK_SYSTEMCTL": str(systemctl),
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
    # DB still queryable and consumers restarted, maintenance cleared.
    import sqlite3

    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM reach").fetchone()[0]
    assert n > 0
    assert not (root / "maintenance").exists()
    assert "start kayak-pipeline.timer" in systemctl_log.read_text()


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


@pytest.mark.slow
def test_activation_prunes_old_releases(tmp_path: Path, engine_repo, dataset_repo) -> None:
    """Old releases are pruned after a successful activation, but current and
    previous are always kept (PR #190 live review P2 — unbounded venv growth)."""
    ds_repo, ds_sha = dataset_repo
    eng_repo, engine_sha = engine_repo
    fixture_ds = _REPO / "tests" / "fixtures" / "dataset"
    db = tmp_path / "kayak.db"
    _init_db(db, fixture_ds)
    root = tmp_path / "opt"
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
