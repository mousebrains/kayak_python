# DNS Changeover: levels.wkcc.org → levels.mousebrains.com

Plan for adding `levels.wkcc.org` to this host (Hetzner VPS, IP `5.78.185.66`)
without users seeing an SSL error during DNS propagation.

## Current state (as of 2026-05-09)

- **`levels.wkcc.org`** → `A 208.97.186.232` (ClubExpress, the old host).
- **`levels-test.wkcc.org`** → `CNAME levels.mousebrains.com` → `A 5.78.185.66` (this host).
- **Existing Let's Encrypt cert on this host** covers two SANs:
  - `levels.mousebrains.com`
  - `levels-test.wkcc.org`
- Cert is renewed via **HTTP-01** challenge.
- We do **not** have direct access to ClubExpress's DNS records (changes go through
  a support ticket); we **do** control `mousebrains.com` DNS.

## Goal

Have a 3-SAN cert (adding `levels.wkcc.org`) installed and active on this host
**before** the `levels.wkcc.org` A-record is flipped to a CNAME. That way users
see no SSL warning regardless of how long ClubExpress takes to propagate.

## Why HTTP-01 alone won't cut it

HTTP-01 requires `levels.wkcc.org` to resolve to this host so Let's Encrypt can
fetch `http://levels.wkcc.org/.well-known/acme-challenge/<token>`. While
`levels.wkcc.org` still points at `208.97.186.232`, HTTP-01 fails. We can't
acquire the cert until *after* the DNS flip — exactly the window we want to
avoid.

DNS-01 doesn't have that constraint: it only needs a TXT record at
`_acme-challenge.levels.wkcc.org`, which can exist independent of the A record.

## Plan: DNS-01 with CNAME delegation

The standard pattern. ClubExpress adds **one** permanent CNAME pointing the
ACME challenge name into a zone we control. After that, all ACME validation
(initial and renewal) is done by editing TXT records on `mousebrains.com` —
no further ClubExpress involvement.

### Phase 1 — Now (one-time ClubExpress request, user-invisible)

Open a ticket with ClubExpress to add **two** CNAMEs on `wkcc.org`:

```
_acme-challenge.levels.wkcc.org.        CNAME   _acme-challenge.levels.wkcc.org.mousebrains.com.
_acme-challenge.levels-test.wkcc.org.   CNAME   _acme-challenge.levels-test.wkcc.org.mousebrains.com.
```

(The right-hand-side names are arbitrary — anything we control under
`mousebrains.com`. The form above is self-documenting.)

Why both? Phase 2 acquires the cert via DNS-01, which requires a TXT record
satisfiable for **every** SAN on the cert — including `levels-test.wkcc.org`,
which currently renews via HTTP-01 only because its CNAME already resolves to
this host. DNS-01 doesn't follow that A-record CNAME; it needs its own
`_acme-challenge` record on the `wkcc.org` zone.

These records affect only ACME validators; users see nothing. Safe to leave in
place forever — they also serve as a permanent fallback if HTTP-01 ever breaks.

Verify when they're live:

```bash
dig _acme-challenge.levels.wkcc.org CNAME +short @1.1.1.1
# expect: _acme-challenge.levels.wkcc.org.mousebrains.com.

dig _acme-challenge.levels-test.wkcc.org CNAME +short @1.1.1.1
# expect: _acme-challenge.levels-test.wkcc.org.mousebrains.com.
```

### Phase 2 — Acquire the 3-SAN cert (before the A-record flip)

On this host, expand the existing cert with DNS-01:

```bash
sudo certbot certonly --expand \
  --cert-name levels.mousebrains.com \
  --manual --preferred-challenges dns \
  -d levels.mousebrains.com \
  -d levels-test.wkcc.org \
  -d levels.wkcc.org
```

Certbot will pause three times, once per SAN, and print a TXT value to add.
All three TXTs go in the **mousebrains.com** zone on Cloudflare (the Phase 1
CNAMEs delegate the wkcc.org names there):

| Certbot prompts for TXT at | Add on Cloudflare in zone `mousebrains.com` |
|---|---|
| `_acme-challenge.levels.mousebrains.com` | name `_acme-challenge.levels` |
| `_acme-challenge.levels-test.wkcc.org` | name `_acme-challenge.levels-test.wkcc.org` |
| `_acme-challenge.levels.wkcc.org` | name `_acme-challenge.levels.wkcc.org` |

Cloudflare auto-appends the zone, so the names typed in the Cloudflare UI are
exactly the strings in the right column above (no trailing `.mousebrains.com`).
Type `TXT`, content = the value certbot printed, proxy = **DNS only** (gray
cloud — TXT can't be proxied anyway, but stay in the habit), TTL = Auto.

Verify each one before pressing Enter in certbot:

```bash
dig _acme-challenge.levels.wkcc.org TXT +short @1.1.1.1
# expect the value certbot printed (returned via the CNAME → mousebrains.com)
```

Cloudflare propagates in seconds. Once all three resolve, let certbot continue.
Let's Encrypt follows each CNAME into mousebrains.com, reads the TXT,
validates.

#### Timing — how long is the TXT good for?

- **Lower bound (when can you press Enter?):** ~30 seconds after saving in
  Cloudflare. The `dig +short @1.1.1.1 <name>` check is canonical — if a
  public resolver sees the value, Let's Encrypt's resolver will too.
- **Upper bound (how long can it sit pending?):** ~7 days. That's the
  lifetime of the pending ACME *order* certbot opens when you run the
  command. After that, the order expires; re-running certbot prints fresh
  TXT values to swap in. Within 7 days, the TXT can sit there indefinitely
  — TXT records don't expire on their own.
- **TTL on the record (Auto = 300s on Cloudflare)** controls how long
  *resolvers cache the answer*, not when it becomes live. Fresh records
  propagate to Cloudflare's edges in seconds.
- **Order of operations matters:** save the TXT *first*, *then* press
  Enter in certbot. If LE queries before the TXT exists and gets
  `NXDOMAIN`, that absence may be negatively cached for 5–15 min (SOA
  minimum), forcing a wait on retry.

#### Why not the `certbot-dns-cloudflare` plugin?

The plugin would normally automate TXT updates, but it tries to find a
Cloudflare zone for the literal challenge name (e.g.
`_acme-challenge.levels.wkcc.org`), which lives in `wkcc.org` — not in
Cloudflare — and errors out. It does not follow CNAMEs. For this one-shot,
`--manual` is faster than scripting around the plugin. Tools that *do* follow
CNAMEs natively (`acme.sh` with `--challenge-alias`, `lego` with
`--dns.resolvers`) would automate it, but switching tools mid-stream isn't
worth it.

#### Cleanup

After the cert issues you can delete the three TXT records on Cloudflare. The
Phase 1 CNAMEs on ClubExpress stay. Future renewals will use HTTP-01 (since
`levels.wkcc.org` resolves here after Phase 3), so no TXT is needed; if you
ever need DNS-01 again, add a fresh TXT with whatever new value certbot prints.

Reload nginx after the cert is issued:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Sanity check the cert covers all three names:

```bash
sudo openssl x509 -in /etc/letsencrypt/live/levels.mousebrains.com/fullchain.pem \
  -noout -text | grep -A1 'Subject Alternative Name'
```

### Phase 3 — DNS cutover at ClubExpress

Open a second ticket with ClubExpress to change:

```
levels.wkcc.org.   A      208.97.186.232
```

to:

```
levels.wkcc.org.   CNAME  levels.mousebrains.com.
```

Because the cert already includes `levels.wkcc.org`, users see a valid
certificate the moment their resolver picks up the new record — no race, no
warning, regardless of propagation time.

### After cutover

The `_acme-challenge` CNAME stays in place permanently. Future renewals can use
HTTP-01 (since `levels.wkcc.org` now resolves to this host), but DNS-01 remains
available as a fallback if HTTP-01 ever breaks — without re-involving
ClubExpress.

### Phase 4 — T+3 cleanup (after DNS has fully propagated)

By T+3 (≈ 2026-05-23) the `levels.wkcc.org` A→CNAME flip from Phase 3 has
reached every cache that respects TTL, and we can verify the renewal path
works end-to-end before forgetting about it.

1.  **Confirm DNS propagation is complete.**

    ```bash
    for r in 1.1.1.1 8.8.8.8 9.9.9.9 208.67.222.222; do
        echo "@$r:"; dig +short @"$r" levels.wkcc.org A levels.wkcc.org CNAME
    done
    # Expect: CNAME levels.mousebrains.com. → A 5.78.185.66 from every resolver.
    ```

2.  **Delete the three temporary `_acme-challenge` TXT records** in the
    Cloudflare zone `mousebrains.com` (added in Phase 2):
    - `_acme-challenge.levels`
    - `_acme-challenge.levels-test.wkcc.org`
    - `_acme-challenge.levels.wkcc.org`

    The two **CNAMEs** at ClubExpress stay in place permanently; only the
    TXT values from Phase 2 are removed.

3.  **Restore the renewal config to HTTP-01.** Phase 2 runs `certbot
    --expand --manual --preferred-challenges dns`, which writes
    `authenticator = manual` into the renewal config and would prompt
    interactively at every renewal. Now that `levels.wkcc.org` resolves
    here, switch back to nginx HTTP-01:

    ```bash
    sudo certbot certonly --force-renewal \
      --cert-name levels.mousebrains.com \
      --nginx \
      -d levels.mousebrains.com -d levels-test.wkcc.org -d levels.wkcc.org
    ```

    This reissues the same 3-SAN cert via HTTP-01 and rewrites the renewal
    config to `authenticator=nginx`. Verify:

    ```bash
    grep authenticator /etc/letsencrypt/renewal/levels.mousebrains.com.conf
    # expect: authenticator = nginx
    ```

4.  **Dry-run the renewal.**

    ```bash
    sudo certbot renew --dry-run
    # expect: "Congratulations, all simulated renewals succeeded"
    ```

5.  **Restore the `levels.wkcc.org` TTL** at ClubExpress (if you lowered
    it for the cutover) back to its previous value, e.g. 3600s.

6.  **Confirm traffic to `levels.wkcc.org` lands on this host.**

    ```bash
    curl -sI https://levels.wkcc.org/ | head -5
    # expect: HTTP/2 200 from this nginx; check x-frame-options /
    #         strict-transport-security headers match the other names.

    openssl s_client -connect levels.wkcc.org:443 -servername levels.wkcc.org \
        </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer
    # expect: subject CN=levels.mousebrains.com, issuer Let's Encrypt
    ```

7.  **Update the access-log analyzer** if it filters by hostname —
    `levels.wkcc.org` is now a primary, not just an alias.

8.  **Optional: prune unused mousebrains.com DNS records** — the
    `_acme-challenge.levels.wkcc.org.mousebrains.com` and sibling **target**
    names from Phase 1 stay (they're the CNAME targets); the **TXT values**
    underneath them from Phase 2 are what gets deleted.

If any of 1–6 fails, the rollback in Phase 3 still applies (revert the
A-record CNAME at ClubExpress); the temporary TXTs and renewal config are
forward-compatible and don't block a rollback.

## Pre-flight checks before Phase 2

Things to confirm before running certbot, since `--manual` interacts with the
existing auto-renewal config:

- **Current authenticator:** `sudo cat /etc/letsencrypt/renewal/levels.mousebrains.com.conf`
  — confirm webroot vs nginx vs standalone, and whether `--expand --manual`
  will overwrite the renewal config. If it does, the next auto-renew won't
  pick up DNS-01 settings; we may want to revert the renewal config to
  HTTP-01 after Phase 3 so cron-driven renewals just work.
- **mousebrains.com DNS provider:** Cloudflare. The `certbot-dns-cloudflare`
  plugin would normally automate this, but doesn't follow CNAMEs (see Phase 2
  note), so manual TXT entry is the simplest path for the one-shot.
- **TTL on the old `levels.wkcc.org` A record:** currently 600s. Lower it (if
  ClubExpress allows) a day or two before Phase 3 to shorten propagation tail
  for any caches that *do* respect TTL.
- **Nginx server block for `levels.wkcc.org`:** make sure there's a
  `server_name` entry for it on the existing TLS listener before Phase 3, so
  requests arriving on the new CNAME don't fall through to a default server.

## Alternatives considered

- **Race the HTTP-01 with the cutover.** At the moment ClubExpress flips DNS,
  run `certbot --expand`. Window of SSL errors = time between user resolvers
  seeing the new DNS and the cert being acquired (minutes to a few hours).
  Acceptable if we can babysit the change, but worse than the DNS-01 plan.

- **Cloudflare in front.** Put `levels.wkcc.org` behind Cloudflare's free SSL.
  ClubExpress would CNAME to Cloudflare, Cloudflare handles the cert and
  proxies traffic to this origin. Bigger architectural change; not worth it
  just to avoid one ClubExpress ticket.

## Rollback

If something goes wrong in Phase 2, the existing 2-SAN cert is untouched until
the new one issues successfully — `--expand` writes the new cert atomically.
If it goes wrong in Phase 3, ClubExpress can revert the CNAME back to the old
A record; DNS-01 cert acquisition has no effect on the old host.
