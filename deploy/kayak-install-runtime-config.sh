#!/usr/bin/env bash
#
# kayak-install-runtime-config -- install the typed-config JSON snapshot to
# /etc/kayak/runtime-config.json. Installed root-owned at
# /usr/local/sbin/kayak-install-runtime-config (root:root 0755).
#
# This is the ONLY command the emit-config sudoers grant runs as root
# (deploy/sudoers.d/kayak-emit-config; review-3 R1.5). The privilege boundary:
#
#   pat  (unprivileged):  levels emit-config --dry-run  -- renders the resolved
#                         config to stdout; reads pat's ~/.config, writes nothing.
#   root (this wrapper):  reads that JSON from stdin, checks it parses, MERGES
#                         the root-only /etc/kayak/secrets.env values in, and
#                         atomically installs it (0640 root:www-data).
#
# So a compromised pat can't substitute code to run as root: the grant invokes
# THIS fixed, root-owned script, not the pat-writable /home/pat/.venv/bin/levels
# (which is exactly why the old `sudo levels emit-config` was a pat->root RCE).
# pat still controls the config *content* (its own ~/.config, as before) but not
# what executes as root, and there is no staging file -- so no symlink/TOCTOU.
#
# Why the merge (gpt-5.5 take-2 review, 2026-06-03): /etc/kayak/secrets.env
# (0600 root:www-data -- TURNSTILE_SITE_KEY / TURNSTILE_SECRET) is unreadable
# by pat, so the pat-rendered JSON arrives WITHOUT those keys. Before R1.5,
# `sudo levels emit-config` ran as root and load_dotenv picked secrets.env up;
# the wrapper split silently dropped them, which disabled Turnstile in prod
# (Config::str has no getenv fallback since T3.3 Phase 4). Each KEY=VALUE in
# secrets.env lands as lowercase(KEY) in the JSON, but only when the rendered
# JSON doesn't already carry a non-empty value -- parity with config.py's
# `load_dotenv(_SECRETS_ENV, override=False)` precedence (operator env wins).
#
# Test hooks: KAYAK_INSTALL_DEST / KAYAK_INSTALL_SECRETS redirect the two
# fixed paths, honored ONLY when NOT running as root -- as root (the sudoers
# entry) the paths are immutable, and `sudo -n` env_reset strips the vars
# anyway. Unprivileged runs can't write /etc anyway; this just lets
# tests/test_scripts/test_install_runtime_config.py exercise the real script.
#
# Install (live server, as root):
#   install -m 0755 -o root -g root \
#       /home/pat/kayak/deploy/kayak-install-runtime-config.sh \
#       /usr/local/sbin/kayak-install-runtime-config
#
set -euo pipefail

DEST="/etc/kayak/runtime-config.json"
SECRETS="/etc/kayak/secrets.env"
if [ "$(id -u)" -ne 0 ]; then
    DEST="${KAYAK_INSTALL_DEST:-$DEST}"
    SECRETS="${KAYAK_INSTALL_SECRETS:-$SECRETS}"
fi

tmp="$(mktemp "$(dirname "$DEST")/.runtime-config.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

cat > "$tmp"                                          # the piped JSON (pat's stdin)

# Validate + merge in one pass: reject non-JSON, then fill in secrets.env
# values the unprivileged render couldn't see. Program text via argv (-c),
# stdin already consumed by `cat` above. Single quotes are deliberate —
# nothing in the python text is meant to shell-expand.
# shellcheck disable=SC2016
python3 -c '
import json, sys
from pathlib import Path

tmp, secrets = sys.argv[1], sys.argv[2]
data = json.load(open(tmp))                # reject non-JSON (exits non-zero)
if not isinstance(data, dict):
    sys.exit("runtime-config JSON must be an object")

p = Path(secrets)
if p.is_file():
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # `export KEY=VALUE` is accepted by both other consumers of this
        # file (python-dotenv in config.py, systemd EnvironmentFile);
        # without this strip it would become a bogus "export key" JSON
        # key and silently re-create the captcha-off bug.
        line = line.removeprefix("export ").lstrip()
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("\"").strip("\x27")
        if not k or not v:
            continue                       # empty value = key disabled
        key = k.lower()
        if not data.get(key):              # fill-if-absent/empty: rendered env wins
            data[key] = v

with open(tmp, "w") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
    fh.write("\n")
' "$tmp" "$SECRETS"

if [ "$(id -u)" -eq 0 ]; then
    chown root:www-data "$tmp"
fi
chmod 0640 "$tmp"
mv -f "$tmp" "$DEST"                                  # atomic, same-filesystem rename
trap - EXIT
echo "installed $DEST"
