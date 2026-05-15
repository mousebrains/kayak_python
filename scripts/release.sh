#!/usr/bin/env bash
# Prep a release commit: bump pyproject.toml, flip CHANGELOG.md's
# [Unreleased] section to a dated [<version>] heading, and commit.
#
# DOES NOT create or push the git tag — that step is intentionally
# manual. See `project_pr_mode_after_v1` for the v1.0.0 tag deferral
# rationale (the user controls when the tag goes out). The script
# prints the tag command for the operator to copy-paste when ready.
#
# Usage:
#   scripts/release.sh <version>
#
# Examples:
#   scripts/release.sh 1.0.0          # first tagged release
#   scripts/release.sh 1.0.1          # patch
#   scripts/release.sh 1.1.0          # minor
#
# Per T3.6 of docs/done/PLAN_pre_release_followup.md and the release-flow
# section of docs/operations.md.

set -euo pipefail

: "${KAYAK_HOME:=/home/pat}"
[ -r /etc/kayak/env ] && . /etc/kayak/env

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <version>" >&2
    echo "  e.g. $0 1.0.0" >&2
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version '$VERSION' is not semver (X.Y.Z)" >&2
    exit 1
fi

cd "$(dirname "$0")/.."

if ! git diff-index --quiet HEAD --; then
    echo "Error: working tree has uncommitted changes — commit or stash first" >&2
    git status --short >&2
    exit 1
fi

if git rev-parse -q --verify "refs/tags/v${VERSION}" >/dev/null; then
    echo "Error: tag v${VERSION} already exists locally" >&2
    exit 1
fi

if git ls-remote --tags origin "refs/tags/v${VERSION}" 2>/dev/null | grep -q "v${VERSION}"; then
    echo "Error: tag v${VERSION} already exists on origin" >&2
    exit 1
fi

# ---- pyproject.toml --------------------------------------------------------

CURRENT_PYPROJECT=$(grep -E '^version = "' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
if [[ -z "$CURRENT_PYPROJECT" ]]; then
    echo "Error: couldn't parse current version from pyproject.toml" >&2
    exit 1
fi

if [[ "$CURRENT_PYPROJECT" != "$VERSION" ]]; then
    # The leading `^` + `$` on the sed pattern guards against an inadvertent
    # match inside a dependency line that happens to include the same X.Y.Z.
    sed -i -E "s/^version = \"${CURRENT_PYPROJECT}\"\$/version = \"${VERSION}\"/" pyproject.toml
    NEW=$(grep -E '^version = "' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
    if [[ "$NEW" != "$VERSION" ]]; then
        echo "Error: pyproject.toml bump failed (got '$NEW')" >&2
        exit 1
    fi
    echo "Bumped pyproject.toml: $CURRENT_PYPROJECT → $VERSION"
else
    echo "pyproject.toml already at $VERSION (no bump)"
fi

# ---- CHANGELOG.md ----------------------------------------------------------

DATE=$(date -u +%Y-%m-%d)
if ! grep -q '^## \[Unreleased\]' CHANGELOG.md; then
    echo "Error: CHANGELOG.md has no '## [Unreleased]' heading — refusing to release" >&2
    exit 1
fi

if grep -q "^## \[${VERSION}\]" CHANGELOG.md; then
    echo "Error: CHANGELOG.md already has a [${VERSION}] heading" >&2
    exit 1
fi

# Insert a fresh empty [Unreleased] heading above the existing one and
# rename the existing one to [VERSION] - DATE. Using awk to keep the rest
# of the file byte-identical.
awk -v version="$VERSION" -v date="$DATE" '
    /^## \[Unreleased\]/ && !done {
        print "## [Unreleased]"
        print ""
        print "## [" version "] - " date
        done = 1
        next
    }
    { print }
' CHANGELOG.md > CHANGELOG.md.tmp && mv CHANGELOG.md.tmp CHANGELOG.md
echo "Renamed [Unreleased] → [${VERSION}] - ${DATE}; inserted fresh [Unreleased]"

# ---- commit ----------------------------------------------------------------

git add pyproject.toml CHANGELOG.md
git commit --quiet -m "release v${VERSION}"
echo "Committed: $(git rev-parse --short HEAD) release v${VERSION}"

# ---- commit-log digest -----------------------------------------------------

LAST_TAG=$(git tag -l 'v*' --sort=-v:refname | head -1)
if [[ -n "$LAST_TAG" ]]; then
    echo
    echo "Commits since $LAST_TAG (curate into CHANGELOG.md if anything was missed):"
    git log --pretty=format:'  - %s' "${LAST_TAG}..HEAD^" | head -30
    echo
fi

# ---- next steps ------------------------------------------------------------

cat <<EOF

=== Ready to tag ===
Run when you're ready to publish:

    git tag -a v${VERSION} -m "release v${VERSION}"
    git push origin v${VERSION}
    git push origin main

After v1.0.0 lands, switch to branch+PR workflow per
project_pr_mode_after_v1 (memory). scripts/deploy.sh + the future
"deploy only from tags" enforcement (production-discipline Tier 3
follow-up) take the tag-as-deploy-unit story from here.
EOF
