"""Editor → kayak_data PR bridge (docs/PLAN_editor_pr_bridge.md).

Tier 3 lives in :mod:`kayak.editor_bridge.dataset_patch` — the deterministic,
network-free logic that turns an endorsed ``change_request.applied_json`` diff
into an allowlisted, minimal-diff edit of a kayak_data dataset directory. The
GitHub worker, PHP auto-queue, and reconciler are separate tiers.
"""
