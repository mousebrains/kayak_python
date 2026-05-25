"""Unit conversions shared across the tracing / elevation tooling.

Canonical home for ``M_TO_FT`` so the elevation/gradient scripts stop each
carrying their own copy. (The archived one-offs under ``docs/one-offs/`` keep
their inline copies on purpose — they're frozen snapshots, not imported code.)
"""

# Exact international foot: 1 m = 1 / 0.3048 ft ≈ 3.28083989501.
M_TO_FT = 3.28083989501
