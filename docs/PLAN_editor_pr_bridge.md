# Plan — Complete the editor feature with dataset PRs

> Drafted: 2026-06-19 PDT. This plan implements the deferred
> proposal-to-PR bridge from `docs/done/PLAN_dataset_separation.md` §SA, while
> preserving decision D1/SA-lite's load-bearing rule: **the dataset repository is
> the only metadata authority**. Web review may endorse a diff; production only
> changes after a reviewed dataset PR merges and deploys.

## Goal

Turn the current editor flow into a complete end-to-end path:

1. An editor submits a proposal.
2. A maintainer reviews, edits, and endorses it.
3. A privileged worker creates or updates exactly one `kayak_data` PR for that
   endorsed diff.
4. Dataset CI validates the PR under the existing trusted-base engine pin rules.
5. A human merges the PR and a normal paired-release deploy activates it.
6. The proposal is marked deployed/resolved and the proposer is notified.

No PHP request may write metadata tables, run `git`, hold a GitHub credential, push
`main`, merge PRs, or deploy. `change_request`, editor accounts, sessions, bridge
state, and attachments remain runtime state, never dataset content.

## Current State

- `edit.php` and `review.php` now implement SA-lite: they freeze endorsed diffs in
  `change_request.applied_json`, leave `reach`/`gauge`/`reach_class` untouched,
  and write no `edit_history` rows.
- `change_request.status='approved'` means "endorsed for data review, not yet
  deployed." `resolved` is the manual loop-closer after deploy.
- The dataset workflow already has the right trust shape: `pull_request`, trusted
  base-branch `engine_test_ref`, pin-only bump discipline, read-only engine
  checkout, `validate-dataset`, `generate-sources --check`, clean sync, second
  no-op sync, and build smoke.
- `kayak-deploy.sh` activates immutable paired releases and records
  `dataset_sha` in `release.json`.
- The deferred bridge design in the data-separation plan is still the right target,
  but it needs to be implemented as runtime state plus a worker, not as more
  values jammed into `change_request.status`.

## Verified against the code (2026-06-21)

Grounding the design in the actual editor + dataset code, so the tiers below are
implementation-ready rather than aspirational. File:line evidence in the engine
(`src/kayak/...`, `src/kayak/web/php/...`) and dataset (`kayak_data/...`).

**The frozen diff — exact shape.** `change_request.applied_json` (the
maintainer-edited diff; **read this, never `payload_json`**) is a diff-only,
new-values-only JSON object keyed by table:
- reach: `{"reach": {"<col>": "<new>", ...}, "reach_class": {"names": ["III","III+"], "range": {"low": 400.0, "high": 2000.0, "data_type": "flow"}}}` — `reach_class` present only if changed (`propose_handler.php`, `review_logic.php`).
- gauge (maintainer `edit.php` only): `{"gauge": {"<col>": "<new>", ...}}`.
- site comment / reply-only: `{"body": "..."}` or empty — **no metadata diff**.
There is no "before" snapshot in the payload; the adapter reads the current CSV
cell as the before. Targetable today: `reach` + `reach_class` (propose/review) and
`gauge` (maintainer direct edit). `source` is in the enum but **has no form** — out
of scope.

**ID equality — the key simplification.** `change_request.target_id` is the live
DB row PK, and `reach.csv` / `gauge.csv` / `reach_class.csv` are keyed by that
**same** stable id (`sync-metadata` matches by id and never reassigns). So the
adapter does a direct `target_id → CSV row id` lookup; **no translation table.**

**Editable field allowlists (small, server-enforced at freeze for reach):**
- `REACH_TEXT_FIELDS = {description, features}` (any editor); `REACH_FULL_FIELDS =
  {display_name, latitude_start, longitude_start, latitude_end, longitude_end}`
  (full/maintainer). `review_logic.php` filters `applied_json.reach` to this set on
  approve, so reach keys are guaranteed valid `reach.csv` columns.
- **Asymmetry to handle:** the `gauge` path (`edit.php`) is **not** allowlist-filtered
  at freeze, so the gauge adapter must re-validate keys against `gauge.csv` columns
  itself.

**`reach_class` is the one hard blocker (sharpens Tier 3 + adversarial #4).** The
payload is a *set* `{names:[...], range:{...}}` with no per-row ids, but
`reach_class.csv` is **one id-bearing row per class name** (`id,reach_id,name,low,
low_data_type,high,high_data_type`; counter `reach_class,444`). Auto-applying it is
a delete-absent / update-existing / insert-new-with-id-from-`id_counters.csv` /
record-removed-in-`retired_ids.yaml` operation in CSV space. **This cannot be done
safely from the current payload** → making the propose/review payload row-id-aware
is a *prerequisite* before `reach_class` auto-bridging (reach text/coord + gauge
edits can ship first).

**Today the approval→PR hop is fully MANUAL.** No PHP path writes any metadata
table or `edit_history` (verified by grep); approval only CAS-writes
`applied_json` + `status='approved'`. The terminal UI literally instructs a human
to transcribe the diff into a `kayak_data` PR and click "Mark resolved (deployed)."
This manual hop is exactly what the bridge removes.

**The credential gap is the single new trust-infra piece.** No workflow or
credential in either repo can open a `kayak_data` PR today: the only cross-repo
secret is `KAYAK_ENGINE_DEPLOY_KEY` (read-only, data-CI→engine direction); no
workflow grants `contents:write`/`pull-requests:write`; the local `gh` PAT has
broad classic `repo` scope (rejected for a worker, adversarial #6). The bridge must
provision a **new GitHub App / fine-grained token scoped to `mousebrains/kayak_data`
with only `contents:write` + `pull-requests:write`**, readable solely by the CLI
worker. This is a Tier-1 deliverable (below), not an afterthought.

**Dataset CI gates the bridge's PR must satisfy** (all in kayak_data
`validate.yml`, required): `validate-dataset` (contract + complete projection +
per-cell types + **id-counter high-water** + **retired-id no-reuse** + gauge_source
cardinality + FK resolution + reach-name uniqueness + regression closure +
materialize/check-reaches) and `generate-sources --check` (the source/fetch_url/
gauge_source trio byte-identical to `sources.yaml`). The reach/gauge/reach_class
edits never touch that generated trio, so the bridge needs **no `generate-sources`
run** for them — only a future `source` adapter would. `engine_test_ref` is read
from the PR **base** commit, so a bridge PR that never touches `dataset.yaml`
validates against the trusted pin for free (adversarial #9).

**Deploy closure mechanics.** `kayak-deploy.sh` takes an operator-supplied 40-hex
`--dataset-ref` and verifies it is an **ancestor of `main`** before applying — so
"merged → deployed" is structurally enforced and a human stays in the merge+deploy
loop. `mark-deployed` (Tier 5) is a best-effort post-activation hook keyed on that
ref; the periodic reconciler catches misses.

**Attachments: nothing to bridge yet.** `change_request_attachment` is schema-only
— no upload endpoint exists — so the bridge skips attachments entirely (Tier 6 note
stands).

## Invariants

- PHP-FPM never receives repository paths, git executables, deploy keys, or GitHub
  tokens.
- The worker may push proposal branches and open/update PRs only. It cannot bypass
  branch protection, merge, push `main`, or deploy.
- Every state transition is compare-and-set/idempotent. A crash after branch push
  but before DB update must reuse the existing branch/PR, not create a duplicate.
- The worker edits datasets with CSV/YAML parsers and a per-target allowlist. It
  rejects unexpected files, fields, symlinks, paths, and generated drift.
- PR bodies do not expose proposer email or private maintainer notes. They link to
  the authenticated review page and include only public target/diff summary.
- Existing SA-lite tests remain true: endorsement alone never mutates metadata.

## Target State Model

Keep `change_request.status` coarse:

| Status | Meaning |
|---|---|
| `pending` | Awaiting maintainer review |
| `approved` | Endorsed; bridge state owns PR/deploy progress |
| `rejected` | Closed by maintainer |
| `resolved` | Terminal: deployed or otherwise closed |

Add a new `change_request_bridge` table with one row per endorsed request:

- `change_request_id` UNIQUE FK.
- `state`: `queued`, `pr_open`, `merged`, `deployed`, `pr_closed`, `conflict`,
  `worker_error`.
- `attempt`, `base_dataset_sha`, `reviewed_base_json`, `applied_json_sha256`.
- `branch_name`, `pr_number`, `pr_url`, `pr_head_sha`, `pr_merge_sha`.
- `queued_by`, `queued_at`, `last_error`, `conflict_json`.
- Lease fields: `lease_owner`, `lease_expires_at`, `heartbeat_at`.

`approved + queued/pr_open/merged` is the active work queue. `deployed` advances
the parent `change_request` to `resolved`.

## Implementation Tiers

### 1. Schema, Config, and CLI Skeleton

- Add migration + SQLAlchemy model for `change_request_bridge`; update schema
  docs and fixtures.
- Add host/runtime config for bridge enablement, dataset repo owner/name/base
  branch, scratch root, branch prefix, PR base URL, and GitHub credential path/env.
- **Provision the write credential (the single new trust-infra piece — none exists
  today).** Register a GitHub App installation token, or a fine-grained PAT, scoped
  to `mousebrains/kayak_data` with **only** `contents:write` + `pull-requests:write`
  — it must not merge, push `main`, bypass branch protection, or deploy. Store the
  key at the config path, readable by the worker user only, never by PHP-FPM.
  Document issuance + rotation in `operations.md`.
- Add `levels editor-bridge status`, `queue`, `run-once`, `reconcile`, and
  `mark-deployed` subcommands. Start with dry-run/no-network behavior and clear
  exit codes.
- Add systemd service/timer templates, disabled unless bridge config is present.

### 2. Review Queue Integration

- On maintainer endorse, insert `change_request_bridge(state='queued')` in the
  same transaction that freezes `applied_json`.
- Only queue **bridgeable** requests: `target_type ∈ {reach, gauge}` with a
  non-empty metadata diff in `applied_json`. Skip `site`/comment and reply-only
  requests (body-only or empty payload) — they carry no dataset change and resolve
  without a PR. (Today the review UI already only offers approve for `reach`.)
- Capture reviewed base values for every touched field from the current DB
  projection, plus the active release's `dataset_sha` where available.
- Direct maintainer edits (`edit.php`) self-endorse and queue the same way.
- Existing approved rows without bridge state stay manual by default; add a
  maintainer-only "queue as new attempt" action that captures a fresh base.
- Update review/admin UI to show bridge state, PR link, error/conflict details,
  and requeue controls.

### 3. Dataset Patch Adapters

Implement Python adapters that patch a temporary dataset worktree, never the live
release dataset:

- `reach`: patch allowlisted `reach.csv` fields by stable `id`; stamp
  `updated_at` when a reach row or its class rows change.
- `gauge`: patch `gauge.csv` metadata fields by stable `id`. **Re-validate keys
  against `gauge.csv` columns in the adapter** — the gauge freeze path (`edit.php`)
  is not allowlist-filtered server-side, unlike the reach path.
- **Minimal, reviewable diffs:** rewrite only the target row's *physical line span*
  (logical→physical mapping via `csv.reader.line_num`), preserving every other
  row's exact bytes — including an embedded-newline cell (the live `reach.csv` has
  one) or a source-over-quoted sibling — so a bridge PR shows exactly the changed
  cells with no incidental re-quoting. Drift is fail-closed: each `reviewed_base`
  value is coerced through the same cell rendering before comparison, and a changed
  field with no captured base is itself a conflict. Built + verified against the
  live dataset in the Tier-3 adapter (`kayak.editor_bridge.dataset_patch`, #214).
- `reach_class`: make the proposal/review payload row-ID aware before automatic
  bridge support is enabled. Preserve existing IDs for edits/renames, allocate new
  IDs from `id_counters.csv`, and record removed IDs in `retired_ids.yaml`.
- Reject unsupported target types and unsupported fields loudly.
- After patching, run `validate-dataset`, `generate-sources --check`, clean
  `sync-metadata` into a throwaway DB, a second no-op sync, and build smoke.
- Reject any diff outside the adapter allowlist, especially `.github/`,
  `dataset.yaml`, workflow files, and unrelated CSVs.

### 4. GitHub PR Worker

- Use argv-only `git` subprocess calls for clone/fetch/branch/commit/push; no
  shell interpolation.
- Use a small GitHub REST client for PR create/update/read. Prefer a GitHub App or
  fine-grained token with only contents and pull-request write on the dataset repo.
- Branch identity is deterministic: `proposal/<change_request_id>-<attempt>`.
- Before editing, compare `reviewed_base_json` with the current dataset branch. If
  any touched base value changed, transition to `conflict` and require re-review.
- On retry, discover and reuse the existing branch and PR for the same attempt.
- PR title/body include request ID, public target, source URL if sanitized/public,
  validation summary, and a link to `/review.php?id=N`.

### 5. Reconciliation and Deploy Closure

- A reconciler polls PR state. `pr_open -> merged` records `pr_merge_sha`;
  unmerged close becomes `pr_closed`.
- Add `levels editor-bridge mark-deployed --dataset-ref <sha> --release-id <id>`.
  It marks merged bridge rows deployed only when their merge commit is the deployed
  dataset ref or an ancestor of it.
- Call `mark-deployed` as a best-effort post-activation step in `kayak-deploy.sh`;
  do not roll back a successful deploy if notification/state marking fails. The
  periodic reconciler catches missed marks.
- On deployed, set parent `change_request.status='resolved'`, append reviewer note
  "deployed in release <id>", and notify the proposer.

### 6. Security and Operations

- Update `docs/security/editor-surface.md`, threat model, controls map, and
  decisions for the new worker, GitHub credential, bridge table, and PR data
  exposure.
- Add credential-rotation and stuck-lease recovery to `docs/operations.md`.
- Add fail2ban/nginx changes only if new HTTP endpoints are introduced; prefer CLI
  worker controls over web-triggered background jobs.
- Keep attachments out of git. If uploads are later wired, PRs link to the
  authenticated review page; attachment retention/backup follows the existing
  deferred security decision.

### 7. Verification

- PHP integration: endorse queues bridge row; non-maintainers cannot queue/requeue;
  current no-metadata-write assertions remain.
- Python unit tests: reach/gauge/reach_class adapters, ID allocation, retired IDs,
  conflict detection, allowlisted diff enforcement, malformed payload rejection.
- Worker integration: local bare git repo + fake GitHub client; crash points after
  branch creation, after push, after PR creation, and after DB update all converge
  to one branch/PR.
- Deploy tests: `mark-deployed` handles exact dataset ref, ancestor ref, unrelated
  ref, missing PR merge SHA, and idempotent rerun.
- E2E smoke: editor proposal -> maintainer endorse -> worker opens fake PR ->
  fake merge -> mark deployed -> request resolved.

## Rollout

1. Ship schema/config/CLI with worker disabled.
2. Enable queue capture in staging/local and verify approved rows remain manual
   unless bridge config is enabled.
3. Run worker against a test dataset repo with a real branch-protected PR flow.
4. Enable for one maintainer direct edit on production; verify PR, merge, deploy,
   and deployed notification.
5. Enable for editor-submitted reach proposals.
6. Revisit the deferred file-upload and WebAuthn decisions only after bridge
   telemetry shows real editor volume.

## Acceptance Criteria

- A proposal creates at most one open PR per attempt across worker crashes.
- Production metadata changes only after dataset PR merge and paired-release deploy.
- The worker cannot edit workflow/pin files or unrelated metadata files.
- Conflicts caused by dataset-main drift stop before push and require human review.
- Reach-class edits preserve stable IDs and never reuse retired IDs.
- No proposer email, private notes, tokens, or credentials appear in public PR data.
- A successful deploy eventually marks matching merged proposals resolved, even if
  the immediate post-deploy marker failed.

## Adversarial Self-Review

1. **Status sprawl risk.** Adding `queued/pr_open/conflict` directly to
   `change_request.status` would mix moderation state with worker operations and
   force brittle UI filters. Resolved by a separate bridge table.
2. **Direct-write regression risk.** "Fully implemented edit" could be misread as
   returning to DB writes. Rejected: it violates dataset separation and would be
   reverted by `sync-metadata`.
3. **Base-drift blind spot.** A frozen diff alone cannot detect that dataset `main`
   changed since review. Resolved by storing reviewed base values and refusing
   conflicts before branch push.
4. **Reach-class ID corruption.** The current comma-list class payload is not enough
   for safe automated PRs. Resolved by requiring row-ID-aware payloads before
   enabling automatic `reach_class` bridging.
5. **PII in PRs.** GitHub PRs may be public or more widely visible than the editor
   DB. Resolved by excluding email/private notes and linking to authenticated review.
6. **Worker credential overreach.** A PAT with broad repo/admin rights would turn the
   worker into a deploy bypass. Resolved by a repo-scoped App/fine-grained token and
   branch protection as the merge gate.
7. **Duplicate PRs after crashes.** A naive retry would create branch/PR spam.
   Resolved by deterministic branch names, attempt numbers, and discover-before-create.
8. **Deploy coupling.** Failing to mark a proposal deployed must not roll back a good
   release. Resolved by best-effort deploy hook plus periodic reconciliation.
9. **Validator bypass.** The worker must not weaken the dataset workflow or engine
   pin. Resolved by diff allowlists that reject `.github/` and `dataset.yaml`, plus
   reliance on the existing trusted-base CI gate.
10. **Legacy approved rows.** Existing SA-lite approved rows lack base snapshots.
    Resolved by keeping them manual unless a maintainer explicitly requeues with a
    fresh base snapshot.

No further material improvements found after pass 10; remaining choices are naming
and PR sizing details for implementation.
