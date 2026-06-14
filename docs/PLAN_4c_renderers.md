# PLAN — Batch 4C: host-config renderers + the paired-release cutover

> Status: **drafting** (2026-06-14). Branch `b4c-renderers` off `main`.
> Prereqs done: PR #190 (deployer, merged), #191 (`levels audit-gauges`, merged),
> #192 (#3 docroot → `/var/cache/kayak/docroot`, merging 2026-06-14 AM).
> This plan finishes Batch 4 — it replaces the hand-crafted step 5 of
> `deploy/INSTALL-paired-release.md` (branch `b4c-paired-install`) with renderers
> driven by `kayak.host.HostConfig`, adds the deployer's serving-path verification,
> and flips the live host to `SERVING_CUTOVER=yes`.

## Goal

The deployer (`kayak-deploy.sh`) refuses to activate until the host is fully cut
over to the `/opt/kayak/current` paired-release layout: nginx `root`, FPM
`open_basedir`, and every `levels`-running consumer unit must point at
`current`/the shared docroot. Today those artifacts are **hand-crafted** (runbook
step 5). 4C makes them **rendered from `host.yaml`** so the cutover is
reproducible and verifiable, then performs the cutover on the live host.

## Consumer classification (verified against `systemd/` on `main`, 2026-06-14)

The cutover only re-points units that execute **engine code from the venv**. The
`levels audit-gauges` promotion (#191) moved audit-gauges from the
"host-level/source-script" column into the engine column — the runbook §5c
(which predates #191) lists only 5; it is now **6**.

**A. Engine consumers — re-point `ExecStart` → `/opt/kayak/current/venv/bin/levels`,
fix `DATASET_DIR`/`OUTPUT_DIR`/`WorkingDirectory`/`ReadWritePaths`:**
`kayak-pipeline`, `kayak-decimate`, `kayak-editor-retention`, `kayak-fetch-osmb`,
`kayak-status`, **`kayak-audit-gauges`** (new, via #191).

**B. Checkout-script consumers — run `/home/<svc>/kayak/{scripts,systemd}/*.sh`,
none of which invoke `levels`.** They keep running from the repo checkout (which
still exists on the live host), so the cutover does **not** re-point them. Two of
them touch the DB and so must still be in the deployer's quiesce set
(`KAYAK_UNITS`) even though they're not re-pointed (descriptions verified against
the live scripts — PR #193 review #1):
- `kayak-healthcheck` (`scripts/health-check.sh`) — reads the DB → **quiesce**.
- `kayak-config-drift` (`scripts/check-config-drift.sh`) — compares repo↔/etc, no
  DB content read.
- `kayak-recap` (`systemd/kayak-recap.sh`) — runs `${KAYAK_HOME}/.venv/bin/python3
  scripts/recap.py` (the **editable-install venv**, NOT a `levels` subcommand);
  journald only, no DB/dataset. Stays on the checkout — re-pointing buys nothing
  (running old code to render a journald email is harmless); no quiesce.
- `kayak-heartbeat` (`systemd/kayak-heartbeat.sh`) — `mail`/msmtp heartbeat (no
  curl); `stat()`s the DB file mtime (no content read, no lock) → no quiesce.
- `kayak-cert-expiry` (`scripts/check-cert-expiry.sh`), `kayak-cert-renewal-test`
  (`certbot`) — no DB.

> Note: recap uses the venv but isn't a class-A unit — it runs `python3
> scripts/recap.py`, not `levels`, and touches neither the migrated DB nor the
> release dataset, so §Goal's "every `levels`-running consumer points at current"
> doesn't reach it. The D-CONSUMER derivation keys re-pointing off "ExecStart runs
> `…/levels`", which correctly excludes recap.

**C. Pure host-level — exempt via `KAYAK_HOST_UNITS`:** `kayak-backup-{hourly,
weekly,offsite}` (shell), `kayak-notify-failure@` (template), `kayak-fail-test`.

> **Open decision D-CONSUMER:** the deployer's activation gate verifies every
> `KAYAK_UNITS` member's `ExecStart` references `$ROOT/current` *unless* it's in
> `KAYAK_HOST_UNITS`. The repo-shell-script consumers (B) reference neither `current`
> nor the venv — they reference `/home/<svc>/kayak`. So they must be listed in
> `KAYAK_HOST_UNITS` to pass the gate, **even though some read the DB** (so they
> must ALSO be quiesced). Today `KAYAK_UNITS` (quiesce) and `KAYAK_HOST_UNITS`
> (gate-exempt) are independent lists, which already supports "quiesce but don't
> verify". The renderer/runbook must emit a `KAYAK_HOST_UNITS` that includes the
> class-B units, and `KAYAK_UNITS` that includes the DB-touching ones. Plan: derive
> both lists from the installed `kayak-*.timer` set + this classification table,
> rather than hand-maintaining them (closes the "complete consumer enumeration"
> 4C item).

## `HostConfig` additions (increment 1)

New non-secret fields on `kayak.host.HostConfig` (all with current-WKCC defaults,
keep-current-then-flip — the live `host.yaml` flips them at cutover):

| field | default (current) | cutover value | used by |
|---|---|---|---|
| `service_user` | `pat` | `pat` | unit `User=`, ACL, `KAYAK_APP_USER` |
| `service_home` | `/home/pat` | `/home/pat` | `KAYAK_HOME`, DB/var/log dirs |
| `release_root` | `/opt/kayak` | `/opt/kayak` | venv/dataset paths, `$ROOT` |
| `fpm_pool_php` | `8.4` | `8.4` | FPM pool path |
| `server_names` | (per-vhost, see below) | — | nginx `server_name` |

`docroot` (existing field) is reused: it stays `/home/pat/public_html` until the
cutover `host.yaml` sets it to `/var/cache/kayak/docroot` (matching the deployer's
`KAYAK_DOCROOT`). Derived (not stored): venv = `{release_root}/current/venv`,
release dataset = `{release_root}/current/dataset`, FPM pool =
`/etc/php/{fpm_pool_php}/fpm/pool.d/kayak.conf`.

**Vhost `server_names` are NOT needed for the cutover** and are deferred. The
cutover's only host-specific *serving* delta is the docroot path (nginx `root` +
FPM `open_basedir` both move `public_html` → `host.docroot`); the three vhosts'
`server_name`/cert/log lines are static, committed config, correct for the WKCC
host. A `server_names` type is only for full from-scratch genericization (the
generic-default flip, increment 7) — added then, with its consumer, not now.

## Renderer mechanism

A `levels` subcommand per the `emit-config` precedent (emits text; the installer/
runbook redirects it). Implemented:
- `levels render-units [--out-dir DIR]` → the class-A drop-in files
  (`<unit>.service.d/cutover.conf`) from `HostConfig`. *(PR #193, merged)*
- `levels render-serving [--out-dir DIR]` → the nginx `root` directive + the FPM
  `open_basedir` directive, both derived from `host.docroot`/`service_home`
  (replacing the runbook's hand-typed `sed`). *(increment 3, this PR)*

Each renderer is **pure** (HostConfig → text; the config is generated, not read
from the repo `conf/` templates, which aren't in the wheel a release runs from),
unit-tested by asserting the rendered text matches the runbook §5 spec. Real
`systemd-analyze verify` / `nginx -t` validation happens on the VM (Pat-driven).

## The class-A drop-in spec (`<unit>.service.d/cutover.conf`)

```ini
[Service]
ExecStart=
ExecStart=/opt/kayak/current/venv/bin/levels <cmd> <args…>
Environment=DATASET_DIR=/opt/kayak/current/dataset
Environment=OUTPUT_DIR=/var/cache/kayak/docroot
WorkingDirectory=/opt/kayak/current
ReadWritePaths=/var/cache/kayak/docroot /home/<svc>/DB
```
(The leading empty `ExecStart=` resets the base unit's value — required by systemd
to replace, not append.) `<cmd> <args…>` comes from the base unit's ExecStart
tail (e.g. `pipeline`; `status --output …`; `audit-gauges --days 16 --email
${AUDIT_EMAIL}`). `ReadWritePaths` differs per unit (status writes
`status_output`; audit-gauges writes the metadata cache — see #191). The renderer
must carry the per-unit write-path set, not a blanket one.

## Increment sequence (each a reviewable PR; VM-validated where noted)

1. **`HostConfig` renderer fields + tests.** Pure schema (scalar fields); no
   behavior change (defaults = current). Foundational. *(merged, PR #193)*
2. **`levels render-units` + tests** asserting the 6 class-A drop-ins match the
   spec. Also adds the two relocatable-cache fields (`map_layers_dir`,
   `gauge_metadata_cache`) — surfaced because `fetch-osmb`/`audit-gauges` default
   those dirs *relative to the install root*, read-only under `/opt/kayak/current`,
   so the drop-ins point them at `/var/cache/kayak/*` (keep-current-then-flip, like
   `docroot`). *(merged, PR #193)*
3. **`levels render-serving` + tests.** The nginx `root` + FPM `open_basedir`
   directives from `host.docroot`/`service_home`. *(this PR)*
4. **Deployer serving-path gate + quiesce-timeout fix** (`deploy/kayak-deploy.sh`):
   when `SERVING_CUTOVER=yes`, verify nginx root / FPM `open_basedir` / unit
   `OUTPUT_DIR`+`ReadWritePaths` resolve to `$KAYAK_DOCROOT`; back out maintenance
   on a drain timeout (the [[deploy_quiesce_timeout_followup]] fix). Branch off
   #192. Slow-test the gate.
5. **Derive `KAYAK_UNITS`/`KAYAK_HOST_UNITS` from installed timers** (closes the
   complete-consumer-enumeration item; resolves D-CONSUMER).
6. **Runbook §5 rewrite** (`deploy/INSTALL-paired-release.md` on `b4c-paired-install`):
   replace hand-crafted step 5 with the `render-*` calls; update for the #3 docroot
   and the audit-gauges promotion. Rebase onto merged #192.
7. **Cutover the live host** (Pat-driven, with the VM rehearsed first): ship the
   live `host.yaml`, run the renderers, ACLs, flip `SERVING_CUTOVER=yes`, first
   real `kayak-deploy` activation. Flip the generic defaults last.

## VM-validation handoff

Increments 2–5 produce text/gate logic unit-tested here, but the rendered units/
vhosts must pass `systemd-analyze verify` + `nginx -t` on the arm64 test VM (ssh
`levels-mac`) before the live cutover — that's the same drive→codex→Pat rehearsal
flow as [[vm_rehearsal_plan]]. The hand-crafted artifacts already validated on the
VM (2026-06-13) are the renderers' golden output to diff against.

## Reproduce / cross-check

```bash
# consumer classification:
for f in systemd/kayak-*.service; do printf '%s\t%s\n' "$(basename "$f")" \
  "$(grep -h '^ExecStart=' "$f" | head -1)"; done
# the hand-crafted spec the renderers must reproduce:
git show b4c-paired-install:deploy/INSTALL-paired-release.md   # § step 5
```
