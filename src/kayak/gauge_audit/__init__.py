"""Gauge metadata audit: refresh caches, find candidates, detect data changes.

A wholesale move of the former ``scripts/audit_gauges.py`` closure into the
package so ``levels audit-gauges`` can run it from an installed (paired-release)
venv rather than a source script absent from releases. The thin CLI wrapper
lives in :mod:`kayak.cli.audit_gauges`; the audit logic and the two site-cache
fetchers live here.
"""
