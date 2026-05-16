#!/usr/bin/env bash
# Live-probe each expected hostname to verify the Let's Encrypt cert is
# healthy. No root required — runs as User=pat from the systemd unit.
#
# Exits 0 if every host in EXPECTED_SANS returns a cert with >= WARN_DAYS
#       remaining, AND the union of served SANs covers EXPECTED_SANS.
# Exits 1 (WARN) if remaining days are in [CRIT_DAYS, WARN_DAYS) — the
#       cert hasn't renewed yet but isn't critical.
# Exits 2 (CRITICAL) if the cert has < CRIT_DAYS remaining, a hostname
#       fails to return a cert in 3 attempts, or any expected SAN is
#       missing from the served union.
#
# OnFailure=kayak-notify-failure@%n.service fires on exit 1 or 2.
# A healthy run posts to ${HC_CERT_EXPIRY} via ExecStartPost.
#
# Union-coverage rationale: during the DNS cutover window (per
# DNS.CHANGEOVER.md) we have two certs in service — the
# certbot-managed 2-SAN cert covers levels.mousebrains.com +
# levels-test.wkcc.org, and the bridge cert at /etc/nginx/certs/ covers
# levels.wkcc.org + www.levels.wkcc.org. The script verifies that the
# UNION of SANs across all probed hosts covers EXPECTED_SANS, not that
# each individual host serves a cert with all names. Post-Phase-C
# (single 3-SAN cert) the same check still passes; no env edit needed.

set -euo pipefail

WARN_DAYS="${WARN_DAYS:-21}"
CRIT_DAYS="${CRIT_DAYS:-7}"
EXPECTED_SANS="${EXPECTED_SANS:-levels.mousebrains.com levels-test.wkcc.org levels.wkcc.org}"

log() { printf '[cert-check] %s\n' "$*"; }

probe_cert_enddate() {
    local host=$1
    local out
    for attempt in 1 2 3; do
        out=$(timeout 8 openssl s_client -servername "$host" \
              -connect "$host:443" </dev/null 2>/dev/null \
              | openssl x509 -noout -enddate 2>/dev/null \
              | cut -d= -f2 || true)
        if [ -n "$out" ]; then
            if [ "$attempt" -gt 1 ]; then
                log "$host: succeeded on attempt $attempt/3"
            fi
            printf '%s' "$out"
            return 0
        fi
        sleep 2
    done
    return 1
}

probe_cert_sans() {
    local host=$1
    timeout 8 openssl s_client -servername "$host" \
        -connect "$host:443" </dev/null 2>/dev/null \
        | openssl x509 -noout -ext subjectAltName 2>/dev/null \
        | grep -oE 'DNS:[^,]+' | sed 's/^DNS://' | tr '\n' ' '
}

WORST_DAYS=9999
WORST_HOST=""
declare -A SEEN_SANS=()
NOW_EPOCH=$(date +%s)

for host in $EXPECTED_SANS; do
    END=$(probe_cert_enddate "$host" || true)
    if [ -z "$END" ]; then
        log "FAIL: TLS handshake to $host did not return a cert after 3 attempts"
        exit 2
    fi
    END_EPOCH=$(date -d "$END" +%s)
    DAYS_LEFT=$(( (END_EPOCH - NOW_EPOCH) / 86400 ))
    SANS=$(probe_cert_sans "$host")
    log "$host: notAfter=$END, days_left=$DAYS_LEFT, served_sans=[$SANS]"
    for san in $SANS; do SEEN_SANS[$san]=1; done
    if [ "$DAYS_LEFT" -lt "$WORST_DAYS" ]; then
        WORST_DAYS=$DAYS_LEFT
        WORST_HOST=$host
    fi
done

# Verify every EXPECTED_SAN was served by at least one host.
for san in $EXPECTED_SANS; do
    if [ -z "${SEEN_SANS[$san]:-}" ]; then
        log "FAIL: expected SAN $san not present in any served cert"
        exit 2
    fi
done

if [ "$WORST_DAYS" -lt "$CRIT_DAYS" ]; then
    log "CRITICAL: $WORST_HOST has $WORST_DAYS days remaining (< $CRIT_DAYS)"
    exit 2
fi
if [ "$WORST_DAYS" -lt "$WARN_DAYS" ]; then
    log "WARN: $WORST_HOST has $WORST_DAYS days remaining (< $WARN_DAYS) — renewal should have happened by now"
    exit 1
fi
log "OK: minimum $WORST_DAYS days remaining (on $WORST_HOST); all EXPECTED_SANS verified."
exit 0
