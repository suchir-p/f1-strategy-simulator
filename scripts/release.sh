#!/usr/bin/env bash
# Builds a source release from the committed tree:
#   dist/f1-strategy-simulator-<VERSION>.zip   (no .git, no build output, no local data)
#
#   bash scripts/release.sh
#
# Uses `git archive`, so only committed files go in: no .git directory, no
# virtualenv, no engine build, no node_modules, no .env, no download cache.
# Refuses to run with uncommitted changes, so the archive matches a commit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(tr -d '[:space:]' < VERSION)"
if [[ -n "$(git status --porcelain)" ]]; then
  echo "uncommitted changes; commit them first so the release matches a commit" >&2
  exit 1
fi
python3 - "$VERSION" <<'PY'
import json, sys
version = sys.argv[1]
web = json.load(open("frontend/package.json"))["version"]
if web != version:
    sys.exit(f"frontend/package.json says {web}, VERSION says {version}")
PY

NAME="f1-strategy-simulator-$VERSION"
mkdir -p dist
git archive --format=zip --prefix="$NAME/" -o "dist/$NAME.zip" HEAD
echo "dist/$NAME.zip ($(du -h "dist/$NAME.zip" | cut -f1)), from commit $(git rev-parse --short HEAD)"
if unzip -l "dist/$NAME.zip" | grep -qE "/\.git/|/\.venv/|node_modules/|engine/build"; then
  echo "unexpected files in the archive" >&2; exit 1
fi
