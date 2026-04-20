# One-off import and migration scripts

Scripts in this directory ran once (or a small number of times) to import
or repair specific data sets and are kept for historical reference rather
than active use. Each filename links back to the file's git history —
`git log --follow` shows the full context.

| Script | Last touched | What it did |
|---|---|---|
| `import_dreamflows.py` | 2026-04-11 | Scraped Dreamflows run pages and linked them back to reach rows by AW ID so their text descriptions could be surfaced alongside live flows. |
| `fix_sort_names.py` | 2026-04-11 | Backfilled `reach.sort_name` for older rows where the value was missing or inconsistent with the current naming convention. |
| `link_ok_guidebook.py` | 2026-04-11 | Populated `reach_guidebook` rows for the Oregon "Paddling Oregon" / "Soggy Sneakers" entries from a flat source list. |

## Running one again

You can still run these from the repo root via:

```bash
/home/pat/.venv/bin/python3 docs/one-offs/<script>.py
```

but review the code first — each assumes a specific state of the live
DB and external data that may have shifted since the last run.

## Adding a new one-off

1. Write the script under `scripts/` while iterating.
2. Once it has run and the outcome is captured in the DB, move it here
   with `git mv` and add a row to the table above.
