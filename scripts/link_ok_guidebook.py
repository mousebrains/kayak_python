#!/usr/bin/env python3
"""Link Oregon Kayaking (oregonkayaking.net) write-ups to reaches in the DB.

Standalone script using sqlite3 only (no kayak imports).
"""

import argparse
import sqlite3

GUIDEBOOK_ID = 10  # Oregon Kayaking by Jason Rackley

# Base URLs for the three content sections
R = "https://www.oregonkayaking.net/rivers/"
C = "https://www.oregonkayaking.net/creeks/"
E = "https://www.oregonkayaking.net/exploratory/"
T = "https://www.oregonkayaking.net/tales/"
V = "https://www.oregonkayaking.net/vids/"
W = "https://www.oregonkayaking.net/waterfalls/"
F = "https://www.oregonkayaking.net/floodstage/"

# (reach_id, full_url) — curated mapping
# Multiple reach_ids can map to the same URL when a write-up covers
# a section that spans multiple DB reaches.
MAPPING = [
    # ===== RIVERS (rivers/rivers2.html) =====
    # Alsea, Upper North Fork — already linked, update URL
    (5507, R + "nfalsea/nfalsea.html"),
    # Black Rock Fork of the SF Coquille
    (5576, R + "black_rock_fork/black_rock_fork.html"),
    # Blue River
    (430, R + "blue/blue.html"),
    # Breitenbush River
    (5456, R + "breitenbush/breitenbush.html"),
    (520, R + "breitenbush/breitenbush.html"),
    # Calapooia River, Upper Upper section
    (623, R + "uucala/uucala.html"),
    # Clackamas River, June Creek section
    (5466, F + "clack_high.html"),
    # Clackamas River, Upper (Collawash to Three Lynx)
    (5458, R + "clack/clack.html"),
    # Clackamas River, Killer Fang section
    (827, R + "killerfang/killerfang.html"),
    # Clackamas River, North Fork
    (5513, R + "nf_clack/nf_clack.html"),
    # Clackamas River, Oak Grove Fork
    (5465, R + "oakgrove/oakgrove.html"),
    # Clackamas River, South Fork
    (5511, R + "sf_clack/sf_clack.html"),
    # Collawash River, Middle
    (936, R + "mid_collowash/mid_collowash.html"),
    # Collawash River, Upper Section
    # same physical gauge reach, different URL
    (936, R + "collowash/collowash.html"),
    # Coquille River, South Fork (Coal Creek Canyon)
    (5578, R + "coalcreek/coalcreek.html"),
    # Coquille River, Upper South Fork (The Gem)
    (5518, R + "gem/gem.html"),
    # Coquille River, Upper Upper South Fork (Cataract Canyon)
    (5474, R + "cataracts_SFC/cataracts_SFC.html"),
    # Crooked River
    (1127, R + "crooked/crooked.html"),
    # Deschutes River, Upper Upper (Benham Falls)
    (1221, R + "benham/benham.html"),
    # Devil's Lake Fork of the Wilson (Headwaters run)
    (1242, R + "devils_upper/devils_upper.html"),
    # Hood River, Upper Middle Fork
    (5581, R + "mf_hood/mf_hood.html"),
    # Hood River, Upper East Fork
    (5526, R + "upper_efhood/upper_efhood.html"),
    # Illinois River
    (5485, R + "illinois_98/ill98.html"),
    # Little North Santiam River, Headwaters of Opal Creek
    (5500, R + "head_opal/head_opal.html"),
    # Little North Santiam River, Upper Opal Creek
    (5498, R + "uopal/uopal.html"),
    # Little North Santiam River, Lower Opal Creek
    (5501, R + "lopal/lopal.html"),
    # Little North Santiam River, Lower Opal Creek (higher flows)
    (5501, R + "lopalhw/lopalhw.html"),
    # Little North Santiam River, Opal Gorge
    (5499, R + "opal_gorge/opal_gorge.html"),
    # Little River (Upper Section)
    (5537, R + "little/little.html"),
    # Little Sandy Gorge
    (3878, R + "littlesandy/littlesandy.html"),
    # McKenzie River (The Headwaters)
    (5534, R + "mac_head/mac_head.html"),
    # Middle Santiam (The Concussion Run)
    (5461, R + "middlesantiam/middle.html"),
    # Miracle Mile (NFMF Willamette) — multiple write-ups
    (5492, R + "mmpg/mmpg.html"),
    (5492, R + "mm_six/mm_six.html"),
    (5492, R + "mmfirst/mmfirst.html"),
    # NFMF of the Willamette, Exploring/Paddling headwaters
    (3066, R + "nfmf_run/nfmf_run.html"),
    # NFMF Willamette, lower gorge
    (3067, R + "nfmfgorge/nfmfgorge.html"),
    # Mollala River (Three Bears Run)
    (5484, R + "mollala/mollala.html"),
    # Mollala River, North Fork
    (5514, R + "nfmollala/nfmollala.html"),
    # Mollala River, Table Rock Fork
    (5564, R + "trf_mollala/trf_mollala.html"),
    # Mollala River, Table Rock Fork Gorge
    (5564, R + "trgorge/trgorge.html"),
    # North Santiam, Niagara section
    (5548, R + "niagara/niagara.html"),
    # Owyhee River (lower canyon)
    (3214, R + "owyhee_lower/owyhee_lower.html"),
    # Owyhee River (Upper)
    (5468, R + "owyhee/owyhee.html"),
    # Roaring River (the lower)
    (5477, R + "roaring08/roaring08.html"),
    # Roaring River (the upper)
    (5477, R + "upper_roaring/upper_roaring.html"),
    # Rogue River, Middle Fork Gorge
    (3658, R + "mf_rogue/mf_rogue.html"),
    # Rogue River, North Fork - Natural Bridge Section
    (3652, R + "naturalbridge/nbridge.html"),
    # Rogue River, North Fork - Mill Creek Section
    (3655, R + "millcreek/Millcrk.html"),
    # Rogue River, North Fork - Takilma Gorge
    (5449, R + "takilma/takilma.html"),
    # Salmon River Canyon, Oregon (multiple write-ups → Split Falls to Wilderness)
    (5541, R + "salmonexplore/salmon.html"),
    (5541, R + "salmon_run/salmon_run.html"),
    (5541, R + "salmon_nop/salmon_nop.html"),
    (5541, R + "salmon_2007/salmon_2007.html"),
    # Salmon River Oregon (Wilderness to Arrah-Wanna)
    (5503, R + "salmonexplore/salmon.html"),
    # Salmonberry River (North Fork)
    (5582, R + "nf_salmonberry/nf_salmonberry.html"),
    # Sandy River Gorge (Zigzag to Marmot section)
    (5455, R + "sandygorge/sandygorge.html"),
    # Siletz River, North Fork
    (5528, R + "nf_siletz/nf_siletz.html"),
    # South Umpqua, Three Falls Section
    (5544, R + "three_falls/three_falls.html"),
    # White River, The Lower
    (5525, R + "whitelower/whitelower.html"),
    # White River, the Upper
    (4935, R + "whiteupper/whiteupper.html"),
    # White River, Celestial Gorge at flood
    (4935, W + "celestial/celestial_falls.html"),
    # Hood River (Dee to Tucker)
    (5497, R + "mf_hood/mf_hood.html"),  # MF Hood feeds into this section
    # === Washington rivers ===
    # Cispus River, Super Slides Run
    (5715, R + "cispusss/cispusss.html"),
    # Cispus River, Upper Upper
    (5693, R + "cispusuu/Cispus.html"),
    # Cispus River, North Fork
    (5688, R + "nfcispus/nfcispus.html"),
    # East fork of the Lewis River, Waterfall Run
    (5682, R + "eflewis/eflewis.html"),
    # Elwha River, Grand Canyon (2001 write-up)
    (5654, R + "elwha/elwha.html"),
    # Elwha River, Grand Canyon (2002 write-up)
    (5654, R + "elwha2002/elwha2002.html"),
    # Entiat River (The Canyon)
    (5719, R + "entiat/entiat.html"),
    # Lewis River, Upper North Fork
    (5708, R + "nf_lewis/nf_lewis.html"),
    # Nisqually River, La Grande Canyon
    (5676, T + "toolittle.html"),
    # Ohanepecosh River
    (5718, R + "ohane/ohane.html"),
    # Ohanepecosh River (headwaters)
    (5718, R + "upper_ohane/upper_ohane.html"),
    # White Salmon River, The Farmlands
    (5670, R + "farmlands/farmlands.html"),
    # White Salmon River, Green Truss section
    (5669, R + "greentruss/greentruss.html"),
    # White Salmon River, Green Canyon section
    (5670, R + "green_canyon/green_canyon.html"),
    # Wind River, Lower
    (5050, R + "lwind/lwind.html"),
    (5050, R + "lwindshepherd/lwindshepherd.html"),
    # Wind River, Upper
    (5051, R + "uwind/uwind.html"),
    # Little Klickitat River
    (2428, C + "little_klick/little_klick.html"),
    # Little White Salmon River (Upper)
    (2442, C + "upper_lws/upper_lws.html"),
    # Little White Salmon River (Lower)
    (2442, C + "littlewhite/littlewhite.html"),
    # Skokomish River, North Fork
    (5631, R + "nf_sko/nf_sko.html"),
    # Skokomish River, South Fork
    (5645, R + "sf_sko/sf_sko.html"),
    # ===== CREEKS (creeks/creeklist.html) =====
    # Battle Axe Creek (LNS tributary)
    (5500, C + "battle_axe/battle_axe.html"),
    # Boulder Creek (Siletz tributary)
    (5570, C + "boulder/boulder.html"),
    # Brice Creek, Lower
    (526, C + "bricel/bricel.html"),
    # Brice Creek, Upper
    (525, C + "upper_brice/upper_brice.html"),
    (525, C + "ubrice/ubrice.html"),
    # Butte Creek, Upper-Upper
    (5552, C + "upper_butte/upper_butte.html"),
    # Canyon Creek, Oregon (Upper)
    (665, C + "canyoncreek/canyoncreek.html"),
    # Canyon Creek, Oregon (Lower)
    (665, C + "canyon_ore_lower/canyon_ore_lower.html"),
    # Canyon Creek, Washington (Lewis, Upper)
    (5684, C + "ucanyon_wash/ucanyon_wash.html"),
    # Canyon Creek, Washington (Lewis, Classic Lower)
    (5620, C + "canyon_wash/canyon_wash.html"),
    # Canyon Creek, Washington (Stillaguamish)
    (5616, C + "canyon_creek_stilly/canyon_creek_stilly.html"),
    # Cedar Creek (LNS, Lower)
    (5520, C + "lcedar/lcedar.html"),
    # Cedar Creek (LNS, Upper)
    (5520, C + "cedar/cedar.html"),
    # Christy Creek
    (816, C + "christy/christy.html"),
    # Deer Creek (Oregon)
    (1201, C + "deer/deer.html"),
    (1201, C + "deer2003/deer2003.html"),
    # Eagle Creek (Columbia Gorge)
    (1361, C + "eagle_columbia/eagle_columbia.html"),
    # Elk Creek
    (1407, C + "elk/elk.html"),
    (1407, C + "falls_to_falls/falls_to_falls.html"),
    # Elkhorn Creek (LNS tributary)
    (5521, C + "elkhorn/elkhorn.html"),
    # Elk Lake Creek
    (5463, C + "elk_lake/elk_lake.html"),
    (5463, C + "elk_lake_upper/elk_lake_upper.html"),
    # French Creek (Breitenbush tributary)
    (5577, C + "french/french.html"),
    # Henline Creek (LNS tributary)
    (5543, C + "henline/henline.html"),
    # Jackson Creek, Lower (South Umpqua)
    (5572, C + "lowerjack/lowerjack.html"),
    # Jackson Creek Upper (The Gorge)
    (5572, C + "upperjack/upperjack.html"),
    (5572, C + "uu_jackson/uu_jackson.html"),
    # Lookout Creek
    (2475, C + "lookout/lookout.html"),
    # Mill Creek (Oregon)
    (2760, C + "mill/mill.html"),
    # Nohorn Creek (Clackamas tributary)
    (5549, C + "nohorn/nohorn.html"),
    # Quartzville Creek (Upper)
    (5538, C + "uq/uq.html"),
    (5538, C + "uu_qville/uu_qville.html"),
    # Rickreall Creek (upper)
    (5573, C + "rickreall/rickreall.html"),
    # Rock Creek, Oregon (North Umpqua drainage)
    (5532, C + "rockore/rockore.html"),
    # Rock Creek, Washington
    (5634, C + "rockwash/rockwash.html"),
    # Salmon Creek Gorge
    (3767, C + "salmon/salmon.html"),
    # Salt Creek
    (3796, C + "salt/salt.html"),
    # Silver Creek, Lower (Silver Falls SP)
    (5547, C + "lsilver/lsilver.html"),
    # Silver Creek, Upper
    (5547, C + "usilver/usilver.html"),
    (5547, C + "usilver_2005/usilver_2005.html"),
    # Sweet Creek
    (5559, C + "sweet/sweet.html"),
    # Thomas Creek (Upper)
    (5506, C + "upper_t/upper_t.html"),
    # Wiley Creek
    (5555, C + "wiley/wiley.html"),
    # Winberry Creek
    (5046, C + "winberry/winberry.html"),
    # Icicle Creek, Lower (WA)
    (5615, C + "icicle/icicle.html"),
    # ===== EXPLORATORY (exploratory/explorebanner.html) =====
    # Most are duplicates of rivers/creeks pages — only add unique ones
    # NFMF Willamette headwaters run (duplicate URL already added above)
    # Salmon River Canyon (duplicate)
    # Roaring River (duplicate)
    # etc.
]


def main():
    parser = argparse.ArgumentParser(description="Link Oregon Kayaking write-ups to reaches")
    parser.add_argument("--db", required=True, help="Path to kayak.db")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Verify guidebook exists
    cur.execute("SELECT id, title FROM guidebook WHERE id = ?", (GUIDEBOOK_ID,))
    row = cur.fetchone()
    if not row:
        print(f"ERROR: Guidebook {GUIDEBOOK_ID} not found")
        return
    print(f"Guidebook: {row[1]} (id={row[0]})")

    # Get existing links
    cur.execute(
        "SELECT reach_id, url FROM reach_guidebook WHERE guidebook_id = ?",
        (GUIDEBOOK_ID,),
    )
    existing = {(r[0], r[1]) for r in cur.fetchall()}
    print(f"Existing links: {len(existing)}")

    # Deduplicate mapping (same reach_id + url)
    seen = set()
    unique_mapping = []
    for reach_id, url in MAPPING:
        key = (reach_id, url)
        if key not in seen:
            seen.add(key)
            unique_mapping.append(key)

    created = 0
    updated = 0
    skipped = 0

    for reach_id, url in unique_mapping:
        # Verify reach exists
        cur.execute("SELECT id, display_name FROM reach WHERE id = ?", (reach_id,))
        reach = cur.fetchone()
        if not reach:
            print(f"  WARNING: Reach {reach_id} not found, skipping")
            skipped += 1
            continue

        # Check if this exact link already exists
        if (reach_id, url) in existing:
            skipped += 1
            continue

        # Check if reach already has an OK link with different URL
        cur.execute(
            "SELECT url FROM reach_guidebook WHERE reach_id = ? AND guidebook_id = ? AND url = ?",
            (reach_id, GUIDEBOOK_ID, url),
        )
        if cur.fetchone():
            skipped += 1
            continue

        # Check if there's an old generic link to update
        cur.execute(
            "SELECT url FROM reach_guidebook WHERE reach_id = ? AND guidebook_id = ?",
            (reach_id, GUIDEBOOK_ID),
        )
        old = cur.fetchone()

        if args.dry_run:
            if old and old[0] != url:
                print(f"  ADD  {reach[1]} (id={reach_id}): {url}")
            else:
                print(f"  ADD  {reach[1]} (id={reach_id}): {url}")
            created += 1
        else:
            # Always insert — a reach can have multiple OK write-ups
            cur.execute(
                "INSERT OR IGNORE INTO reach_guidebook (reach_id, guidebook_id, url) VALUES (?, ?, ?)",
                (reach_id, GUIDEBOOK_ID, url),
            )
            if cur.rowcount > 0:
                created += 1
            else:
                skipped += 1

    # Update the original 5507 entry from generic frame URL to specific page
    old_url = "https://www.oregonkayaking.net/riverframe.html"
    new_url = R + "nfalsea/nfalsea.html"
    cur.execute(
        "SELECT 1 FROM reach_guidebook WHERE reach_id = 5507 AND guidebook_id = ? AND url = ?",
        (GUIDEBOOK_ID, old_url),
    )
    if cur.fetchone():
        if args.dry_run:
            print(f"  UPDATE reach 5507: {old_url} → {new_url}")
            updated += 1
        else:
            cur.execute(
                "UPDATE reach_guidebook SET url = ? WHERE reach_id = 5507 AND guidebook_id = ? AND url = ?",
                (new_url, GUIDEBOOK_ID, old_url),
            )
            updated += 1

    if not args.dry_run:
        conn.commit()

    print(f"\nResults: {created} created, {updated} updated, {skipped} skipped")
    print(f"Total unique mappings: {len(unique_mapping)}")

    # Show summary by state
    if not args.dry_run:
        cur.execute(
            """
            SELECT s.abbreviation, COUNT(DISTINCT rg.reach_id)
            FROM reach_guidebook rg
            JOIN reach r ON r.id = rg.reach_id
            JOIN reach_state rs ON rs.reach_id = r.id
            JOIN state s ON s.id = rs.state_id
            WHERE rg.guidebook_id = ?
            GROUP BY s.abbreviation
            ORDER BY COUNT(*) DESC
        """,
            (GUIDEBOOK_ID,),
        )
        print("\nReaches linked by state:")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]}")

    conn.close()


if __name__ == "__main__":
    main()
