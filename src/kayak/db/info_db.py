"""Master/corrections query helpers (replaces InfoDB.C)."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

from kayak.db.models import Correction, Master, MergedMaster


def all_states(session: Session) -> list[str]:
    """Return sorted list of distinct state values from MergedMaster."""
    rows = session.execute(
        select(MergedMaster.state)
        .where(MergedMaster.state.isnot(None))
        .distinct()
    ).scalars().all()
    return sorted(set(rows))


def master_query(
    session: Session,
    criteria: str | None = None,
    model=MergedMaster,
) -> list:
    """Query the master/merged_master table with optional filtering."""
    stmt = select(model).order_by(model.sort_key)
    # For simple criteria like "no_show is null and db_name is not null"
    # we translate them to SQLAlchemy filters
    if criteria:
        stmt = _apply_criteria(stmt, model, criteria)
    return list(session.scalars(stmt))


def _apply_criteria(stmt, model, criteria: str):
    """Parse simple SQL-like criteria into SQLAlchemy filters."""
    import re

    parts = re.split(r'\s+and\s+', criteria, flags=re.IGNORECASE)
    for part in parts:
        part = part.strip()
        m = re.match(r'(\w+)\s+is\s+null', part, re.IGNORECASE)
        if m:
            col = getattr(model, m.group(1), None)
            if col is not None:
                stmt = stmt.where(col.is_(None))
            continue

        m = re.match(r'(\w+)\s+is\s+not\s+null', part, re.IGNORECASE)
        if m:
            col = getattr(model, m.group(1), None)
            if col is not None:
                stmt = stmt.where(col.isnot(None))
            continue
    return stmt


def display_name(session: Session, hash_value: str) -> str | None:
    """Get display_name for a hash value."""
    row = session.get(MergedMaster, hash_value)
    return row.display_name if row else None


def new_hash(session: Session) -> str:
    """Generate a new unique hash value."""
    chars = string.digits + string.ascii_lowercase
    for _ in range(100):
        h = "".join(secrets.choice(chars) for _ in range(4))
        if session.get(Master, h) is None:
            return h
    raise RuntimeError("Could not generate unique hash after 100 attempts")


def submit_corrections(
    session: Session,
    hash_value: str,
    user_name: str,
    email: str,
    corrections: dict[str, str],
) -> str:
    """Submit user corrections. Returns the random approval key."""
    key = secrets.token_urlsafe(16)
    corr = Correction(
        hash_value=hash_value,
        user_name=user_name,
        email=email,
        random_key=key,
    )
    for field, value in corrections.items():
        if hasattr(corr, field) and value:
            setattr(corr, field, value)
    session.add(corr)
    return key


def authenticate_correction(session: Session, hash_value: str, key: str) -> bool:
    """Approve a correction by its random key."""
    corr = session.execute(
        select(Correction).where(
            Correction.hash_value == hash_value,
            Correction.random_key == key,
            Correction.approved.is_(None),
        )
    ).scalar_one_or_none()

    if corr is None:
        return False

    corr.approved = "1"
    session.flush()
    rebuild_merged_master(session)
    return True


def clean_old_corrections(session: Session, before: datetime | None = None) -> int:
    """Delete old unapproved corrections."""
    if before is None:
        before = datetime.now(timezone.utc) - timedelta(days=30)
    result = session.execute(
        Correction.__table__.delete().where(
            Correction.approved.is_(None),
            Correction.date < before,
        )
    )
    return result.rowcount


def rebuild_merged_master(session: Session) -> None:
    """Regenerate MergedMaster from Master + approved Corrections.

    Mirrors InfoDB::mkMergedMaster().
    """
    # Clear and copy from Master
    session.query(MergedMaster).delete()
    masters = session.scalars(select(Master)).all()
    mapper = inspect(MergedMaster)
    col_names = [c.key for c in mapper.column_attrs]

    for m in masters:
        data = {col: getattr(m, col) for col in col_names if hasattr(m, col)}
        session.add(MergedMaster(**data))

    session.flush()

    # Apply approved corrections
    corrections = session.scalars(
        select(Correction).where(Correction.approved == "1")
    ).all()

    for corr in corrections:
        merged = session.get(MergedMaster, corr.hash_value)
        if merged is None:
            # Create new entry from correction
            data = {col: getattr(corr, col) for col in col_names
                    if hasattr(corr, col) and getattr(corr, col) is not None}
            session.add(MergedMaster(**data))
        else:
            # Update non-null fields
            for col in col_names:
                if col == "hash_value":
                    continue
                val = getattr(corr, col, None)
                if val is not None:
                    setattr(merged, col, val)
