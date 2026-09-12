#!/bin/sh
# Build a single-file svngit that runs on any Python 3.9+, with no install.
#
#     sh packaging/make-zipapp.sh
#     ./dist/svngit.pyz --version
#
# svngit depends on nothing outside the standard library, which is what makes
# this possible: the archive holds only svngit itself.

set -eu

ROOT=$(cd -- "$(dirname -- "$0")/.." && pwd)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

cp -R "$ROOT/src/svngit" "$STAGE/svngit"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# zipapp runs __main__.py at the archive root.
cat > "$STAGE/__main__.py" <<'PY'
import sys

from svngit.cli import main

if __name__ == "__main__":
    sys.exit(main())
PY

mkdir -p "$ROOT/dist"
python3 -m zipapp "$STAGE" \
    --output "$ROOT/dist/svngit.pyz" \
    --python "/usr/bin/env python3" \
    --compress

chmod +x "$ROOT/dist/svngit.pyz"
echo "Wrote dist/svngit.pyz ($(du -h "$ROOT/dist/svngit.pyz" | cut -f1))"
