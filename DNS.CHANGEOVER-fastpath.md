# DNS Changeover Fast Path — using the DreamHost-issued cert

Companion to [`DNS.CHANGEOVER.md`](DNS.CHANGEOVER.md). The original plan
acquires a single 3-SAN cert on Hetzner via DNS-01 with a one-time
ClubExpress ticket for permanent CNAME delegation. This fast path uses the
existing `levels.wkcc.org` Let's Encrypt cert that DreamHost auto-issued,
sidesteps DNS-01 entirely, and reduces the ClubExpress touch to a single
ticket (the A→CNAME flip itself).

## When to use which path

| Path | Use when | Trade-off |
|---|---|---|
| Original `DNS.CHANGEOVER.md` | No pre-issued cert for `levels.wkcc.org` available | Most thorough; ends with one 3-SAN cert + permanent `_acme-challenge` CNAMEs as a fallback path |
| **Fast path (this doc)** | Bridge cert installed at `/etc/nginx/certs/levels.wkcc.org.{cert,privkey}` on Hetzner (installed 2026-05-14) | Skips Phases 1 + 2 of the original; uses the DreamHost-issued cert as a bridge until DNS propagates (~T+3 from the A→CNAME flip), then **expands the existing levels.mousebrains.com cert to add `levels.wkcc.org` as a third SAN via HTTP-01**. End-state matches the original (single 3-SAN cert renewed by certbot); the DNS-01 acquisition phase is bypassed entirely. |

## Cert facts (verified 2026-05-14)

| Property | Value |
|---|---|
| Installed at | `/etc/nginx/certs/levels.wkcc.org.cert` (3616 bytes, fullchain) + `/etc/nginx/certs/levels.wkcc.org.privkey` (1704 bytes, PKCS#8 unencrypted) |
| Cert/key match | ✅ moduli identical |
| Subject | `CN=levels.wkcc.org` |
| SubjectAltName | `DNS:levels.wkcc.org, DNS:www.levels.wkcc.org` |
| Issuer | `C=US, O=Let's Encrypt, CN=R13` |
| Validity | `notBefore=2026-05-12T00:47:33Z` → `notAfter=2026-08-10T00:47:32Z` |
| Bundle | 2-cert chain (leaf + intermediate — nginx `ssl_certificate` ready as-is) |
| Origin | Auto-issued by DreamHost's LE automation for the current ClubExpress-hosted site; staged transiently under `dreamhost/` then installed and removed from the repo on 2026-05-14 |

## Plan

### Phase A — Install the bridge cert on Hetzner (today, user-invisible)

The cert/key files have been placed on the Hetzner host at:

```
/etc/nginx/certs/levels.wkcc.org.cert      (fullchain — leaf + intermediate)
/etc/nginx/certs/levels.wkcc.org.privkey   (PKCS#8 private key)
```

Note: deliberately not under `/etc/letsencrypt/live/`. The bridge cert is
**not** certbot-managed, and putting it under `/etc/letsencrypt/` would
invite a future operator (or certbot itself) to assume the renewal config
exists. Keeping it in `/etc/nginx/certs/` makes the bridge state visually
obvious.

1. **Verify the files are in place and the pair is valid:**

   ```bash
   ls -l /etc/nginx/certs/levels.wkcc.org.{cert,privkey}
   # expect: cert 0644 root:root, privkey 0600 root:root
   diff <(sudo openssl x509 -in /etc/nginx/certs/levels.wkcc.org.cert -modulus -noout) \
        <(sudo openssl rsa  -in /etc/nginx/certs/levels.wkcc.org.privkey -modulus -noout)
   # expect: no output (moduli match)
   ```

2. **Deploy the updated `conf/sites/levels-wkcc-org`.** The repo file
   already references `/etc/nginx/certs/levels.wkcc.org.{cert,privkey}`
   (committed alongside this doc). Install and reload:

   ```bash
   sudo install -m 0644 conf/sites/levels-wkcc-org \
       /etc/nginx/sites-available/levels-wkcc-org
   sudo nginx -t && sudo systemctl reload nginx
   ```

3. **Smoke-test via SNI** before the DNS flip. Because `levels.wkcc.org`
   still resolves to ClubExpress, hit Hetzner by IP and pass the SNI name:

   ```bash
   openssl s_client -connect 5.78.185.66:443 -servername levels.wkcc.org \
       </dev/null 2>/dev/null \
       | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
   # expect:
   #   subject=CN=levels.wkcc.org
   #   issuer=C=US, O=Let's Encrypt, CN=R13
   #   notBefore=May 12 00:47:33 2026 GMT
   #   notAfter=Aug 10 00:47:32 2026 GMT
   #   X509v3 Subject Alternative Name: DNS:levels.wkcc.org, DNS:www.levels.wkcc.org
   ```

   If that fails, do not proceed — the A-record flip relies on Hetzner
   serving this cert.

### Phase B — DNS cutover at ClubExpress (one ticket) + repo branding flip

Identical to `DNS.CHANGEOVER.md` Phase 3 on the DNS side. Open a ticket
with ClubExpress to change:

```
levels.wkcc.org.   A      208.97.186.232
```

to:

```
levels.wkcc.org.   CNAME  levels.mousebrains.com.
```

Per the original plan: lower the existing 600s TTL a day or two ahead of
the flip if ClubExpress allows it, to shorten the propagation tail.

Because Hetzner already has a valid cert for `levels.wkcc.org`, users see
no SSL warning regardless of resolver propagation order. There's no race
to win.

#### Repo branding flip (run alongside the DNS flip)

Pre-staged on branch `cutover/wkcc-branding-flip` (one commit; do the
audit yourself with `git log -p main..cutover/wkcc-branding-flip` —
it's 4 files, 17/13 lines). Four edits:

- `LICENSE-DATA` opening paragraph URL + attribution example.
- `src/kayak/web/build/_shared.py:33` — `_LICENSE_META["attribution"]`
  flip; embedded in every generated `*.geojson` + `sparklines.json`
  download, so the value travels with each downloaded copy.
- `tests/test_build_geojson_split.py:125` — the assertion that
  matches `_LICENSE_META["attribution"]`; must flip in lock-step.
- `deploy/nginx-editor-env.conf` `$site_url` map — soak-default
  `"https://levels-test.wkcc.org"` becomes a hostname-aware map
  (explicit `levels-test.wkcc.org → test`; `default → https://levels.wkcc.org`).
  PHP magic-link emails will start citing the new URL.

Apply order (the user accepts the propagation gap — i.e. some
resolvers still resolve to the old A record while the generated
output and PHP responses already cite the new hostname; resolver
state catches up over ~T+3 hours per the original plan's TTL math):

```bash
# 1. Merge the cutover branch on main.
git -C /home/pat/kayak fetch
git -C /home/pat/kayak checkout main
git -C /home/pat/kayak merge --ff-only cutover/wkcc-branding-flip
git -C /home/pat/kayak push

# 2. Deploy the new editor-env.conf and reload nginx.
sudo install -m 0644 /home/pat/kayak/deploy/nginx-editor-env.conf \
    /etc/nginx/conf.d/editor-env.conf
sudo nginx -t && sudo systemctl reload nginx

# 3. Trigger an early pipeline run so the next build emits JSON with
#    the new attribution. (Otherwise the systemd timer would catch up
#    on its next firing — also fine.)
/home/pat/.venv/bin/levels build

# 4. Smoke-check the change landed:
grep -q 'levels.wkcc.org' /home/pat/public_html/static/reaches.geojson \
    && echo "OK: GeoJSON carries new attribution" \
    || echo "MISS: re-run levels build"
curl -s -I 'https://levels-test.wkcc.org/' >/dev/null \
    && curl -s 'https://levels-test.wkcc.org/contact.php' | grep -q 'levels-test.wkcc.org' \
    && echo "OK: soak host still cites test URL" \
    || echo "MISS: editor-env.conf may not have reloaded"
```

If any step fails, the rollback is `git -C /home/pat/kayak revert
HEAD --no-edit && git push` (which restores the pre-flip attribution
on the next build) plus the inverse `sudo cp` of the previously
deployed editor-env.conf from `~/etc-backups/` or a manual edit.
The DNS A→CNAME flip can be rolled back independently by reverting
the ClubExpress ticket.

After ClubExpress actions the ticket and resolvers begin pointing
`levels.wkcc.org` at Hetzner, the only user-visible difference is
that the new hostname renders correctly with a valid cert (because
the bridge cert from Phase A is already installed).

### Phase C — Expand the existing cert to add `levels.wkcc.org` as a third SAN (~T+3 from Phase B, or later)

Wait until DNS has propagated to every resolver we care about (the same
T+3 check the original plan uses in its Phase 4 step 1):

```bash
for r in 1.1.1.1 8.8.8.8 9.9.9.9 208.67.222.222; do
    echo "@$r:"; dig +short @"$r" levels.wkcc.org A levels.wkcc.org CNAME
done
# Expect: CNAME levels.mousebrains.com. → A 5.78.185.66 from every resolver.
```

Then expand the existing 2-SAN cert to include `levels.wkcc.org`,
authenticating via **HTTP-01** (works now because `levels.wkcc.org`
resolves to Hetzner):

```bash
sudo certbot --nginx --expand \
    --cert-name levels.mousebrains.com \
    -d levels.mousebrains.com \
    -d levels-test.wkcc.org \
    -d levels.wkcc.org
```

This:

- Replaces `/etc/letsencrypt/live/levels.mousebrains.com/{fullchain.pem,privkey.pem}`
  with a fresh 3-SAN cert.
- Leaves `/etc/letsencrypt/renewal/levels.mousebrains.com.conf` with
  `authenticator = nginx` (unchanged — `--expand` reuses the existing
  renewal config).
- Does **not** edit the `levels-wkcc-org` vhost automatically, because
  that vhost currently points to `/etc/nginx/certs/`. We do that in the
  next step.

Switch the `levels.wkcc.org` vhost back to the certbot-managed path so
the bridge cert is no longer load-bearing:

```bash
# In repo: edit conf/sites/levels-wkcc-org so the ssl_certificate /
# ssl_certificate_key directives point back to
#     /etc/letsencrypt/live/levels.mousebrains.com/fullchain.pem
#     /etc/letsencrypt/live/levels.mousebrains.com/privkey.pem
# (Revert the bridge paths added in Phase A; restore the original
# /etc/letsencrypt/live/levels.mousebrains.com/ lines.)

sudo install -m 0644 conf/sites/levels-wkcc-org \
    /etc/nginx/sites-available/levels-wkcc-org
sudo nginx -t && sudo systemctl reload nginx
```

Verify the new state:

```bash
sudo openssl x509 -in /etc/letsencrypt/live/levels.mousebrains.com/fullchain.pem \
    -noout -ext subjectAltName
# expect:
#   X509v3 Subject Alternative Name:
#       DNS:levels.mousebrains.com, DNS:levels-test.wkcc.org, DNS:levels.wkcc.org

grep authenticator /etc/letsencrypt/renewal/levels.mousebrains.com.conf
# expect: authenticator = nginx (unchanged from before)

sudo certbot renew --dry-run --cert-name levels.mousebrains.com
# expect: "Congratulations, all simulated renewals succeeded"

openssl s_client -connect levels.wkcc.org:443 -servername levels.wkcc.org \
    </dev/null 2>/dev/null | openssl x509 -noout -subject -dates -ext subjectAltName
# expect: subject CN=levels.mousebrains.com, SAN includes levels.wkcc.org,
#         notAfter ~90 days from now (NOT the bridge cert's Aug 10 date).
```

Delete the bridge files once the new cert is confirmed in service:

```bash
sudo rm /etc/nginx/certs/levels.wkcc.org.cert
sudo rm /etc/nginx/certs/levels.wkcc.org.privkey
# /etc/nginx/certs/ directory can stay or be removed depending on whether
# it holds other certs.
```

### Phase D — Cleanup

1. **Update `EXPECTED_SANS`** in `~/.config/kayak/.env` for the cert-expiry
   monitor (see [`docs/PLAN_pre_release_followup.md`](docs/PLAN_pre_release_followup.md)
   §P0.2):

   ```
   EXPECTED_SANS="levels.mousebrains.com levels-test.wkcc.org levels.wkcc.org"
   ```

   End-state is a single 3-SAN cert at
   `/etc/letsencrypt/live/levels.mousebrains.com/` serving all three
   hostnames. The monitor probes by hostname so the same `EXPECTED_SANS`
   list works.

2. **Enable the weekly renewal dry-run** (`kayak-cert-renewal-test.timer`).
   The fast path never puts any cert into `authenticator=manual`, so
   there's no "wait until Phase 4 step 3" gate. Enable as soon as the
   daily probe is healthy — the existing `levels.mousebrains.com.conf`
   has been on `authenticator=nginx` throughout, and Phase C's `--expand`
   preserves that.

3. **`dreamhost/`** has already been removed from the repo (2026-05-14,
   before Phase A landed). No further action; this item is here only as
   a marker that the cleanup is done.

4. **Optionally** add the permanent `_acme-challenge` CNAMEs from the
   original plan's Phase 1 anyway. The fast path doesn't need them, but
   they're harmless (only ACME validators see them) and give you DNS-01
   as a fallback if HTTP-01 ever breaks (nginx auth misconfigured, port
   80 firewalled by accident, etc.). Not urgent; consider during a future
   ClubExpress ticket cycle.

## Compare with original `DNS.CHANGEOVER.md`

| Step | Original | Fast path |
|---|---|---|
| ClubExpress tickets | 2 (CNAME delegation + A→CNAME) | 1 (A→CNAME only) |
| Cloudflare TXT records during acquisition | 3 (during `certbot --manual`) | 0 |
| `certbot --manual` DNS-01 acquisition | yes | no — `--expand` over HTTP-01 once DNS resolves |
| Renewal config ever in `authenticator=manual` | yes (until Phase 4 step 3 restores nginx auth) | no |
| Cert structure end-state | 1 cert, 3 SANs at `/etc/letsencrypt/live/levels.mousebrains.com/` | **same** (1 cert, 3 SANs, same path) |
| Cert structure during bridge window | n/a | 2 certs (existing 2-SAN + DreamHost 1-SAN-with-www at `/etc/nginx/certs/`) for the ~T+3 between A→CNAME flip and `--expand` |
| Wall-clock to user-visible cutover | ~24h+ waiting for ClubExpress to action the Phase 1 CNAME ticket | minutes once Phase A is deployed |
| Permanent `_acme-challenge` CNAMEs at ClubExpress as fallback | yes | no by default — could be added later via Phase D step 4 if desired |

The fast path is strictly simpler operationally and lands at the same
end-state. The only extra work is the Phase C vhost revert; everything
else collapses to "use the cert we already have until DNS settles."

## Rollback

- **If Phase A's `nginx -t` fails:** the live nginx config is unchanged.
  Fix the vhost in repo, redeploy.
- **If Phase B's A-record flip causes any issue:** ClubExpress reverts the
  CNAME back to the old A record (`208.97.186.232`). The bridge cert on
  Hetzner remains installed but harmless — it never serves anything
  because traffic for `levels.wkcc.org` now routes back to ClubExpress.
- **If Phase C's `certbot --nginx --expand` fails:** the existing 2-SAN
  cert at `/etc/letsencrypt/live/levels.mousebrains.com/` is unchanged
  (certbot writes atomically; a failed `--expand` leaves the prior cert
  in place). The bridge cert continues to serve `levels.wkcc.org` until
  2026-08-10. Plenty of window to debug. The monitor's warning threshold
  (21 days remaining) fires around 2026-07-20 — a hard deadline visible
  from telemetry.
- **If the Phase C vhost revert fails to deploy** (after `--expand`
  already succeeded): the vhost still points at `/etc/nginx/certs/`, so
  the bridge cert keeps serving even though the 3-SAN cert is sitting
  ready on disk. Same Aug-10 ceiling; just re-run the deploy.

## Why this is safe

The bridge cert and the post-cutover certbot-issued cert are both
LE-signed leafs chained to ISRG Root X1. Browsers don't distinguish
between them, so:

- **Pre-cutover:** `levels.wkcc.org` A → `208.97.186.232` → ClubExpress
  serves its copy of the cert. No user-visible change.
- **Mid-cutover (during DNS propagation):** some resolvers see the old A
  record, some see the new CNAME. Either path lands on a host with a
  valid LE cert for `levels.wkcc.org`. No SSL warning.
- **Post-cutover, pre-Phase-C (the bridge window, ~T+3):** Hetzner serves
  the DreamHost-issued bridge cert from `/etc/nginx/certs/`. Users see a
  valid LE cert for `levels.wkcc.org`.
- **Post-Phase-C:** Hetzner serves the expanded 3-SAN cert from
  `/etc/letsencrypt/live/levels.mousebrains.com/`. End-state cert
  structure is identical to what the original DNS.CHANGEOVER.md targets,
  and `certbot.timer` auto-renews it via HTTP-01 from now on.

The "race" the original plan was designed to avoid (user resolves to
Hetzner before Hetzner has a valid cert) is impossible on the fast path
because Hetzner has the bridge cert installed before the A record flips.

## Pre-flight checks before Phase B

Before opening the ClubExpress ticket:

- `openssl s_client` SNI smoke test in Phase A step 3 returned the
  expected cert.
- `conf/sites/levels-wkcc-org` is committed and deployed (`diff` between
  repo and `/etc/nginx/sites-available/levels-wkcc-org` is empty).
- The existing `kayak-cert-expiry.timer` (if already deployed per
  `docs/PLAN_pre_release_followup.md` §P0.2) is still passing with
  `EXPECTED_SANS` unchanged — the bridge install doesn't affect the
  existing 2-SAN monitor.
- A recent backup of `/etc/nginx/` exists (since this path edits a vhost).

## Cross-references

- [`DNS.CHANGEOVER.md`](DNS.CHANGEOVER.md) — original plan; this doc
  obsoletes its Phases 1 + 2 but its Phase 3 (A→CNAME flip) is identical
  to Phase B here.
- [`docs/PLAN_pre_release_followup.md`](docs/PLAN_pre_release_followup.md)
  §P0.2 — cert-expiry monitor; this doc clarifies the `EXPECTED_SANS`
  values for fast-path adopters and removes the "wait for Phase 4 step 3"
  caveat on the weekly renewal timer.
