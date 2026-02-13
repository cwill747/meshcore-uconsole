#!/usr/bin/env bash
# Called by commitizen post_bump_hooks to update debian/changelog
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION=$(grep -E '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
DATE=$(date -R)
MAINTAINER=$(grep -E '^Maintainer:' debian/control | sed 's/Maintainer: //')

cat > debian/changelog.new << EOF
meshcore-uconsole ($VERSION-1) unstable; urgency=low

  * Release v$VERSION

 -- $MAINTAINER  $DATE

EOF
cat debian/changelog >> debian/changelog.new
mv debian/changelog.new debian/changelog

git add debian/changelog
