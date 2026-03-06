# Nginx Security Hardening (2026-03-06)

Analysis of nginx access logs revealed constant automated scanning and exploit
attempts. The existing fail2ban configuration was not catching most of them
because the `nginx-botsearch` filter only matches a narrow set of paths
(phpmyadmin, roundcube, wp-login). This documents the changes made to address
the gaps.

## Problem Summary

### Traffic breakdown (from kayak-access.log, ~541 requests/day)
- ~85% AI crawler traffic (ClaudeBot, GPTBot, OAI-SearchBot)
- ~12% scanner/attack probes
- ~3% real human visitors

### Attack types not caught by fail2ban
- Credential/secret harvesting (`.env`, `.git/config`, `.aws/credentials`,
  `wp-config.php`, `docker-compose.yml`, `credentials.json`)
- PHPUnit RCE exploitation (CVE-2017-9841) — bulk POST to `eval-stdin.php`
- IoT device discovery (`/HNAP1`, `/onvif/device_service`, `/PSIA/index`)
- Path traversal / RCE (`/cgi-bin/.%2e/.%2e/bin/sh`, `../../etc/passwd`)
- Malware download attempts (`/shell?cd+/tmp;rm+-rf+*;wget+...`)
- Open proxy testing (`CONNECT` requests)
- Protocol confusion (TLS handshakes, RDP, SSH, Gh0st RAT over HTTP)
- Infrastructure scanning (`/actuator/`, `/geoserver/`, `/webui/`, `/druid/`)

### Why fail2ban wasn't banning them
1. The `nginx-botsearch` filter only matches roundcube, phpmyadmin, wordpress,
   cgi-bin, and mysqladmin paths.
2. fail2ban only watched `kayak-access.log` — all traffic hitting the bare IP
   (logged to `access.log`) was invisible.
3. No default server block existed, so scanners hitting `http://5.78.185.66`
   got a 301 redirect to the real site instead of being dropped.

## Changes Made

### 1. Default server block — `deploy/nginx-default-server`
Installed to `/etc/nginx/sites-available/default`.

Catches all requests arriving by IP address or unknown hostname and returns
`444` (nginx closes the connection with no response). Logs to a separate
`/var/log/nginx/blocked-access.log` for fail2ban to monitor.

This eliminates nearly all scanner traffic from `access.log` at zero cost —
no response is ever generated.

### 2. Custom fail2ban filter — `hardening/nginx-malicious.conf`
Installed to `/etc/fail2ban/filter.d/nginx-malicious.conf`.

Matches 30+ attack patterns observed in the logs:
- `.env` / `.git` / `.aws` / `.svn` / `.bash_history` harvesting
- `eval-stdin.php` (PHPUnit RCE)
- `cgi-bin` path traversal
- `HNAP1` / `onvif` / `GponForm` / `boaform` (IoT exploits)
- Shell command injection (`/shell?...`)
- `wp-config.php`, `config.json`, `credentials.json`, `docker-compose.*`
- Spring Boot actuator, GeoServer, WebUI, Druid, ReportServer probes
- `CONNECT`, `PROPFIND`, `XDEBUG_SESSION_START`
- Gh0st RAT, MGLNDD banner grabs
- `/etc/passwd` traversal, `/bins/`, `/backup/`

Jail config (`jail.local`): ban on first match (`maxretry = 1`), ban for 1 week.

### 3. Default block fail2ban filter — `hardening/nginx-default-block.conf`
Installed to `/etc/fail2ban/filter.d/nginx-default-block.conf`.

Bans any IP that hits the default server block 3+ times in 10 minutes.
Since legitimate users always use the hostname, any traffic here is scanning.

### 4. Bad user-agent blocking in nginx — `deploy/levels`
Added to the main `levels` server block (before the dotfile block):

```nginx
if ($http_user_agent ~* (zgrab|masscan|scanner/1\.0|libredtail|xfa1|Gh0st|^Hello|nvdorz|FreePBX-Scanner)) {
    return 444;
}
```

Drops connections from known scanning tools immediately.

### 5. robots.txt — `public_html/robots.txt`
Updated to block AI crawlers that were generating 85% of traffic:

```
User-agent: GPTBot
Disallow: /

User-agent: OAI-SearchBot
Disallow: /

User-agent: ClaudeBot
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: *
Allow: /
```

OAI-SearchBot was hitting `/robots.txt` ~197 times/day because the file
previously didn't exist (404). Now that it exists, those requests will get a
response and the bot should stop rechecking so frequently.

### 6. Install script — `hardening/install.sh`
Updated to:
- Copy the two new fail2ban filter files
- Generate a dummy self-signed SSL cert for the default server block
- Install and symlink the default server block

## Installation

```bash
sudo ~/kayak/hardening/install.sh
```

The script validates nginx and sshd configs before reloading services. It also
restarts fail2ban to pick up the new jails.

## Verification

After installation, check:

```bash
# Confirm new jails are active
sudo fail2ban-client status

# Should show: nginx-malicious, nginx-default-block (among others)
sudo fail2ban-client status nginx-malicious

# Test nginx config
sudo nginx -t

# Watch blocked-access.log for IP-direct scanner hits
sudo tail -f /var/log/nginx/blocked-access.log
```

## What was already in place (no changes needed)
- `server_tokens off`
- HSTS with 2-year max-age
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy)
- Dotfile blocking (`location ~ /\.`)
- `client_max_body_size 16k`
- Rate limiting on `edit.php` (5r/m)
- TLS 1.2+ only
- nftables firewall with SSH rate limiting
- sysctl hardening
