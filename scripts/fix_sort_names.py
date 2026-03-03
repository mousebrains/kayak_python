#!/usr/bin/env python3
"""Fix AW reach sort_names to align with legacy sorting convention.

Standalone script using sqlite3 only (Python 3.10 compatible, no kayak imports).

Legacy convention: "{family} {group_letter}{sequence}"
  - family: river system name (e.g., "Rogue", "Clackamas", "Santiam")
  - group_letter: a/b/c/d for fork groups, z for main stem
  - sequence: ordering within group (e.g., "01", "02", alphabetical)

AW reaches currently use:
  - "River - N. Description" for main stem
  - "River, Fork - Description" for forks
  - "Tributary - Description" for tributaries (may not match display_name family)

Also normalizes punctuated fork prefixes in display_name:
  S.F / S.F. → SF, N.F / N.F. → NF, W.F / W.F. → WF, etc.
"""

import argparse
import re
import sqlite3

# Fork prefix patterns: canonical form → regex matching display_name prefixes
FORK_PREFIXES = ("NF", "SF", "MF", "EF", "WF", "CF")
FORK_LONG = {
    "North Fork": "NF", "N. Fork": "NF", "N Fork": "NF",
    "North": "NF", "N.": "NF",
    "South Fork": "SF", "S. Fork": "SF", "S Fork": "SF",
    "South": "SF", "S.": "SF",
    "Middle Fork": "MF", "M. Fork": "MF", "M Fork": "MF",
    "Middle": "MF", "M.": "MF",
    "East Fork": "EF", "E. Fork": "EF", "E Fork": "EF",
    "East": "EF", "E.": "EF",
    "West Fork": "WF", "W. Fork": "WF", "W Fork": "WF",
    "West": "WF", "W.": "WF",
    "Coast Fork": "CF",
    "Big North Fork": "NF",
    "Little North": "NF",  # "Little North Santiam" → NF group (Santiam 'a')
}

# Regex to match "NF of MF" style compound prefixes
COMPOUND_PREFIX_RE = re.compile(r"^(NF|SF|MF|EF|WF|CF) of (NF|SF|MF|EF|WF|CF)\b")


def strip_suffix(name):
    """Remove trailing River/Creek/Fork for matching."""
    return re.sub(r"\s+(River|Creek|Fork)$", "", name).strip()


def parse_fork_prefix(display_name):
    """Parse fork prefix from display_name.

    Returns (prefix, base_name) where prefix is like "NF", "SF", "NF of MF"
    or None if no fork prefix found. base_name is the river name without the prefix.
    """
    # Compound: "NF of MF Willamette River"
    m = COMPOUND_PREFIX_RE.match(display_name)
    if m:
        rest = display_name[m.end():].strip()
        return m.group(0), rest

    # Simple: "NF Rogue River"
    for pfx in FORK_PREFIXES:
        if display_name.startswith(pfx + " "):
            return pfx, display_name[len(pfx) + 1:]

    return None, display_name


def parse_comma_sort(sort_name):
    """Parse comma-format AW sort_name like 'Rogue, North Fork - desc'.

    Returns (river, fork_long, description) or None if not comma format.
    """
    if ", " not in sort_name:
        return None
    river_part, rest = sort_name.split(", ", 1)
    # rest is like "North Fork - 2. Description" or "South Fork - Description"
    if " - " in rest:
        fork_part, desc = rest.split(" - ", 1)
    else:
        fork_part, desc = rest, ""
    return river_part.strip(), fork_part.strip(), desc.strip()


def parse_dash_sort(sort_name):
    """Parse dash-format AW sort_name like 'Rogue - 3. Gold Hill'.

    Returns (river, description) or None if not dash format.
    """
    if " - " not in sort_name:
        return None
    river_part, desc = sort_name.split(" - ", 1)
    return river_part.strip(), desc.strip()


def extract_sequence_number(description):
    """Extract leading sequence number from description.

    '1. June Creek' → 1
    '03. Something' → 3
    ' 0. Soda Springs' → 0
    'No number here' → None
    """
    m = re.match(r"^\s*(\d+)\.", description)
    if m:
        return int(m.group(1))
    return None


def _parse_legacy_sort(sort_name):
    """Parse a legacy sort_name into (family, group_letter).

    Legacy convention: "{family} {group_letter}{sequence}"
    Family can be multi-word (e.g., "John Day", "Grande Ronde").
    Group letter is a single lowercase a-z after a space.

    Returns (family, group) or None if not parseable.
    """
    # Find the group letter: last space-separated token starting with [a-z]
    # e.g., "John Day aa aa" → family="John Day", group="a"
    # e.g., "Rogue da aa aa" → family="Rogue", group="d"
    # e.g., "Clackamas ac" → family="Clackamas", group="a"
    m = re.match(r"^(.+?)\s+([a-z])\w*(?:\s|$)", sort_name)
    if m:
        return m.group(1), m.group(2)
    return None


def get_legacy_families(cur):
    """Build mapping of family → {group_letter → set of display_names}.

    Only considers legacy reaches (not aw_*) with proper sort_name format.
    """
    cur.execute("""
        SELECT sort_name, display_name FROM reach
        WHERE name NOT LIKE 'aw_%'
          AND sort_name LIKE '% %'
    """)

    families = {}  # family → {group → set(display_name)}
    for sort_name, display_name in cur.fetchall():
        parsed = _parse_legacy_sort(sort_name)
        if not parsed:
            continue
        family, grp = parsed
        if family not in families:
            families[family] = {}
        if grp not in families[family]:
            families[family][grp] = set()
        families[family][grp].add(display_name)

    return families


def get_legacy_max_group(families, family):
    """Get the highest group letter for a family."""
    groups = families.get(family, {})
    if not groups:
        return None
    return max(groups.keys())


def match_display_to_family(display_name, families):
    """Match an AW reach's display_name to a legacy family.

    Returns family name or None.
    """
    _prefix, base = parse_fork_prefix(display_name)
    base_stripped = strip_suffix(base)

    # Direct match: display_name's base matches a family
    if base_stripped in families:
        return base_stripped

    # Try full display_name stripped
    full_stripped = strip_suffix(display_name)
    if full_stripped in families:
        return full_stripped

    # Check if the display_name appears in any family's group display_names
    # This handles "Little North Santiam River" → family "Santiam" (legacy group 'a')
    # and "North Santiam River" → family "Santiam" (legacy group 'b')
    for fam, groups in families.items():
        for _grp, gdns in groups.items():
            if display_name in gdns:
                return fam
            for gdn in gdns:
                if strip_suffix(display_name) == strip_suffix(gdn):
                    return fam

    # Try multi-word prefixes (for families like "John Day", "Grande Ronde")
    words = display_name.split() if display_name else []
    for n in range(min(3, len(words)), 0, -1):
        candidate = " ".join(words[:n])
        if candidate in families:
            return candidate
        candidate_stripped = strip_suffix(candidate)
        if candidate_stripped and candidate_stripped in families:
            return candidate_stripped

    return None


def match_fork_to_group(display_name, family, families, comma_fork=None):
    """Match an AW reach to a legacy fork group within a family.

    Args:
        display_name: The reach's display_name
        family: The legacy family name
        families: The legacy families mapping
        comma_fork: The fork name from comma-format sort_name (e.g., "South Fork")

    Returns (group_letter, is_mainstem) where group_letter is the matching
    legacy group, or None if no match found.
    """
    if family not in families:
        return None, False

    groups = families[family]
    prefix, base = parse_fork_prefix(display_name)

    # Check if this is a main stem reach (display_name matches family directly)
    dn_stripped = strip_suffix(display_name)
    family_names = {family, family + " River", family + " Creek"}
    is_mainstem = dn_stripped in {strip_suffix(n) for n in family_names}

    # For each legacy group, check if the AW reach's display matches
    for grp, group_display_names in sorted(groups.items()):
        for gdn in group_display_names:
            # Exact match
            if display_name == gdn:
                return grp, is_mainstem
            # Strip suffixes and compare
            if strip_suffix(display_name) == strip_suffix(gdn):
                return grp, is_mainstem
            # Fork prefix match: "NF Rogue River" matches group with "NF Rogue River"
            if prefix:
                g_prefix, g_base = parse_fork_prefix(gdn)
                if prefix == g_prefix and strip_suffix(base) == strip_suffix(g_base):
                    return grp, False

    # Try matching via comma_fork name if provided
    # e.g., comma_fork="South Fork" should match legacy "SF Rogue River"
    # or comma_fork="Middle" should match legacy "Middle Santiam River"
    if comma_fork:
        # Convert long fork name to short prefix
        short = FORK_LONG.get(comma_fork)
        if short:
            # Build expected legacy display_name: "SF Rogue River"
            for grp, group_display_names in sorted(groups.items()):
                for gdn in group_display_names:
                    g_prefix, g_base = parse_fork_prefix(gdn)
                    if g_prefix == short and strip_suffix(g_base) == family:
                        return grp, False
        # Also try abbreviation variants: "N." → "NF", "S." → "SF"
        for long_name, short_name in FORK_LONG.items():
            if comma_fork == long_name or comma_fork.rstrip(".") == long_name.rstrip("."):
                for grp, group_display_names in sorted(groups.items()):
                    for gdn in group_display_names:
                        g_prefix, _ = parse_fork_prefix(gdn)
                        if g_prefix == short_name:
                            return grp, False
        # Try matching comma_fork as a word prefix in legacy display_names
        # e.g., "Middle" matches "Middle Santiam River", "Little North" matches
        # "Little North Santiam River"
        for grp, group_display_names in sorted(groups.items()):
            for gdn in group_display_names:
                if gdn.startswith(comma_fork + " "):
                    return grp, False

    return None, is_mainstem


def compute_sort_name(reach, families, legacy_sort_names, verbose=False):
    """Compute new sort_name for an AW reach.

    Returns (new_sort_name, reason) or (None, reason) if no change needed.
    """
    display_name = reach["display_name"] or ""
    sort_name = reach["sort_name"] or ""
    elevation = reach["elevation"]

    # Parse current sort_name to understand format
    comma = parse_comma_sort(sort_name)
    dash = parse_dash_sort(sort_name)

    # Determine the river from the sort_name (most reliable for family matching)
    if comma:
        sort_river = comma[0]
    elif dash:
        sort_river = dash[0]
    else:
        return None, "unknown sort format"

    # Find the family — prefer display_name match (more reliable for tributaries),
    # fall back to sort_river
    dn_family = match_display_to_family(display_name, families)
    sr_family = match_display_to_family(sort_river + " River", families)
    if not sr_family:
        sr_family = sort_river if sort_river in families else None
    if not sr_family:
        sr_family = strip_suffix(sort_river) if strip_suffix(sort_river) in families else None

    # Prefer display_name family (handles tributary mismatches),
    # but use sort_river family if display_name doesn't match
    family = dn_family or sr_family

    if not family:
        # No legacy family found — use sort_river as family
        family = sort_river
        # Determine if fork or main stem
        if comma:
            # Fork/tributary
            fork_name = comma[1]
            desc = comma[2]
            seq = _sequence_str(desc, elevation)
            return f"{family} 0 {fork_name}", f"no-legacy fork: {fork_name}"
        else:
            desc = dash[1] if dash else ""
            seq = _sequence_str(desc, elevation)
            return f"{family} z {seq}", "no-legacy main"

    # Found a family — now find the group
    comma_fork = comma[1] if comma else None
    grp, is_mainstem = match_fork_to_group(display_name, family, families,
                                           comma_fork=comma_fork)

    if grp:
        # Matched an existing legacy group
        desc = (comma[2] if comma else dash[1]) if (comma or dash) else ""
        seq = _sequence_str(desc, elevation)
        max_grp = get_legacy_max_group(families, family)
        # Append after legacy reaches: use group + suffix letters
        suffix = _next_suffix(family, grp, families, legacy_sort_names)
        return f"{family} {grp}{suffix} {seq}", f"group {grp}"
    elif is_mainstem:
        # Main stem but no exact group match — append after last group
        max_grp = get_legacy_max_group(families, family)
        if max_grp:
            suffix = _next_suffix(family, max_grp, families, legacy_sort_names)
            desc = (comma[2] if comma else dash[1]) if (comma or dash) else ""
            seq = _sequence_str(desc, elevation)
            return f"{family} {max_grp}{suffix} {seq}", f"mainstem after {max_grp}"
        else:
            desc = (comma[2] if comma else dash[1]) if (comma or dash) else ""
            seq = _sequence_str(desc, elevation)
            return f"{family} z {seq}", "mainstem no-groups"
    else:
        # Fork/tributary with no matching group — sort before 'a' with '0'
        if comma:
            fork_name = comma[1]
            desc = comma[2]
        else:
            # Dash format tributary — use sort_river as fork name if different from family
            if sort_river != family:
                fork_name = sort_river
            else:
                fork_name = display_name
            desc = dash[1] if dash else ""
        seq = _sequence_str(desc, elevation)
        return f"{family} 0 {fork_name} {seq}".rstrip(), f"unmatched fork: {fork_name}"


def _sequence_str(description, elevation):
    """Build sequence string from description number or elevation."""
    num = extract_sequence_number(description)
    if num is not None:
        return f"{num:02d}"
    if elevation is not None:
        # Higher elevation = upstream = sort first; invert for ascending sort
        return f"e{9999 - int(elevation):05d}"
    return ""


def _next_suffix(family, grp, families, legacy_sort_names=None):
    """Find a suffix letter that sorts after all existing legacy entries in this group.

    Legacy uses patterns like "aa", "ab", "ac" within a group. We pick a letter
    after the last used sub-group letter so AW reaches sort after legacy ones.
    """
    if not legacy_sort_names:
        return ""
    # Find all sub-group letters used in this family+group
    # e.g., for "Rogue" group "a": sort_names are "Rogue aa", "Rogue ab", "Rogue ac"
    #   → sub-letters are a, b, c → next is d
    prefix = f"{family} {grp}"
    max_sub = None
    for sn in legacy_sort_names:
        if sn.startswith(prefix) and len(sn) > len(prefix):
            next_char = sn[len(prefix)]
            if 'a' <= next_char <= 'z' and (max_sub is None or next_char > max_sub):
                max_sub = next_char
    if max_sub and max_sub < 'z':
        return chr(ord(max_sub) + 1)
    return ""


def normalize_display_name(display_name):
    """Normalize punctuated fork prefixes in display_name.

    S.F / S.F. → SF, N.F / N.F. → NF, W.F. / W.F → WF, etc.
    """
    if not display_name:
        return display_name
    # Match patterns like "S.F ", "N.F. ", "W.F. ", "E.F ", "M.F." at start
    m = re.match(r"^([SNMEW])\.([F])\.?\s", display_name)
    if m:
        return m.group(1) + m.group(2) + " " + display_name[m.end():]
    return display_name


def main():
    parser = argparse.ArgumentParser(description="Fix AW reach sort_names")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    parser.add_argument("--state", help="Only process reaches in this state")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Build legacy family mapping
    families = get_legacy_families(cur)
    if args.verbose:
        print(f"Found {len(families)} legacy families")

    # Collect all legacy sort_names for suffix computation
    cur.execute("SELECT sort_name FROM reach WHERE name NOT LIKE 'aw_%'")
    legacy_sort_names = [row["sort_name"] for row in cur.fetchall() if row["sort_name"]]

    # Get AW reaches to fix
    if args.state:
        cur.execute("""
            SELECT r.id, r.name, r.display_name, r.sort_name, r.description, r.elevation
            FROM reach r
            JOIN reach_state rs ON r.id = rs.reach_id
            JOIN state s ON s.id = rs.state_id
            WHERE r.name LIKE 'aw_%' AND s.name = ?
            ORDER BY r.sort_name
        """, (args.state,))
    else:
        cur.execute("""
            SELECT id, name, display_name, sort_name, description, elevation
            FROM reach WHERE name LIKE 'aw_%'
            ORDER BY sort_name
        """)

    reaches = [dict(row) for row in cur.fetchall()]
    print(f"Processing {len(reaches)} AW reaches")

    # Compute new sort_names
    updates = []  # (reach_id, new_sort_name, old_sort_name)
    dn_updates = []  # (reach_id, new_display_name, old_display_name)

    for reach in reaches:
        # Check display_name normalization
        old_dn = reach["display_name"] or ""
        new_dn = normalize_display_name(old_dn)
        if new_dn != old_dn:
            dn_updates.append((reach["id"], new_dn, old_dn))

        result = compute_sort_name(reach, families, legacy_sort_names, verbose=args.verbose)
        if result is None:
            continue
        new_sort, reason = result
        if new_sort is None:
            if args.verbose:
                print(f"  SKIP {reach['name']}: {reason}")
            continue
        old_sort = reach["sort_name"]
        if new_sort != old_sort:
            updates.append((reach["id"], new_sort, old_sort))
            if args.verbose:
                print(f"  {reach['name']:15s} {reach['display_name'][:30]:30s} "
                      f"{old_sort[:40]:40s} → {new_sort[:40]:40s}  ({reason})")

    # Also normalize display_names for non-AW reaches
    cur.execute("SELECT id, display_name FROM reach WHERE name NOT LIKE 'aw_%'")
    for row in cur.fetchall():
        old_dn = row["display_name"] or ""
        new_dn = normalize_display_name(old_dn)
        if new_dn != old_dn:
            dn_updates.append((row["id"], new_dn, old_dn))

    print(f"\nSort name changes: {len(updates)}")
    print(f"Display name normalizations: {len(dn_updates)}")

    if dn_updates and args.verbose:
        print("\nDisplay name changes:")
        for rid, new_dn, old_dn in dn_updates:
            print(f"  id={rid}: {old_dn!r} → {new_dn!r}")

    if args.dry_run:
        print("\nDry run — no changes applied")
    else:
        for rid, new_sort, _old_sort in updates:
            cur.execute("UPDATE reach SET sort_name = ? WHERE id = ?", (new_sort, rid))
        for rid, new_dn, _old_dn in dn_updates:
            cur.execute("UPDATE reach SET display_name = ? WHERE id = ?", (new_dn, rid))
        conn.commit()
        print(f"\nApplied {len(updates)} sort_name updates and {len(dn_updates)} display_name updates")

    conn.close()


if __name__ == "__main__":
    main()
