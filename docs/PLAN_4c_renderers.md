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

**B. Repo-shell-script consumers — run `/home/<svc>/kayak/{scripts,systemd}/*.sh`,
NOT the venv.** They keep running from the repo checkout (which still exists on
the live host), so the cutover does **not** re-point them. But two of them touch
the DB and so must still be in the deployer's quiesce set (`KAYAK_UNITS`) even
though they're not re-pointed:
- `kayak-healthcheck` (`scripts/health-check.sh`) — reads the DB.
- `kayak-config-drift` (`scripts/check-config-drift.sh`) — compares repo↔/etc.
- `kayak-recap` (`systemd/kayak-recap.sh`) — reads journald, not the DB.
- `kayak-heartbeat` (`systemd/kayak-heartbeat.sh`) — curl heartbeat, not the DB.
- `kayak-cert-expiry` (`scripts/check-cert-expiry.sh`), `kayak-cert-renewal-test`
  (`certbot`) — no DB.

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

The vhost `server_names` need a small structured type (a list of
`{server_name, cert_host, enabled}`) for the three sites (`levels.wkcc.org`,
`levels.mousebrains.com`, `levels-test`). That type lands with the **nginx
renderer (increment 3)** that consumes it, not here — adding it before its
consumer risks the wrong shape. Increment 1 is the scalar fields only.

## Renderer mechanism

A `levels` subcommand per the `emit-config` precedent (emits text; the installer/
runbook redirects it). Proposed:
- `levels render-units [--out-dir DIR]` → the class-A drop-in files
  (`<unit>.service.d/cutover.conf`) from `HostConfig`. Default: print a manifest;
  `--out-dir` writes the files.
- `levels render-nginx [--site NAME]` / `levels render-fpm` → vhost root +
  `open_basedir` substitutions (or a sed-spec the runbook applies). Increment 3.

Each renderer is **pure** (HostConfig + templates → text), unit-tested by asserting
the rendered text matches the runbook §5 spec. Real `systemd-analyze verify` /
`nginx -t` validation happens on the VM (Pat-driven; see handoff).

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

1. **`HostConfig` renderer fields + tests.** Pure schema (scalar fields only); no
   behavior change (defaults = current). Foundational. *(this PR)*
2. **`levels render-units` + tests** asserting the 6 class-A drop-ins match the
   spec. Wire nothing yet.
3. **`levels render-nginx` / `render-fpm` + tests.** The root/`open_basedir`
   substitutions.
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
