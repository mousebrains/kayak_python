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
#   root (this wrapper):  reads that JSON from stdin, checks it parses, and
#                         atomically installs it (0640 root:www-data).
#
# So a compromised pat can't substitute code to run as root: the grant invokes
# THIS fixed, root-owned script, not the pat-writable /home/pat/.venv/bin/levels
# (which is exactly why the old `sudo levels emit-config` was a pat->root RCE).
# pat still controls the config *content* (its own ~/.config, as before) but not
# what executes as root, and there is no staging file -- so no symlink/TOCTOU.
#
# Install (live server, as root):
#   install -m 0755 -o root -g root \
#       /home/pat/kayak/deploy/kayak-install-runtime-config.sh \
#       /usr/local/sbin/kayak-install-runtime-config
#
set -euo pipefail

DEST="/etc/kayak/runtime-config.json"
tmp="$(mktemp "$(dirname "$DEST")/.runtime-config.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

cat > "$tmp"                                                       # the piped JSON (pat's stdin)
python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$tmp"  # reject non-JSON
chown root:www-data "$tmp"
chmod 0640 "$tmp"
mv -f "$tmp" "$DEST"                                              # atomic, same-filesystem rename
trap - EXIT
echo "installed $DEST"
