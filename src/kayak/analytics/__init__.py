"""Operator log/journal analytics — ported from ~/logs.analyze.

Three reading interfaces:

- ``_log_sources`` — iterators over nginx access/error/blocked logs,
  journalctl, and the CSP report log. Replaces the syncit harvest step
  (the tool now runs on the live host and reads /var/log + journalctl
  directly).
- ``_release_context`` — snapshot helpers that capture the syncit
  ``release/*`` outputs (the /opt/kayak/current release-pointer mtime,
  git log, DB health).
- ``release_postmortem`` + ``humans`` — the actual analyses.

The CLI dispatcher lives at ``kayak.cli.analyze_logs``. See
``docs/PLAN_logs_analyze_migration.md`` for the migration history.
"""
