#!/bin/sh
# Build a throwaway Subversion repository and working copy for the demos.
#
# The recordings run real commands against this, so what you see in the README
# is genuine output rather than a transcript someone typed out.

set -eu

DEMO_ROOT="${DEMO_ROOT:-/tmp/svngit-demo}"
REPO="$DEMO_ROOT/repo"
WORK="$DEMO_ROOT/work"

rm -rf "$DEMO_ROOT"
mkdir -p "$DEMO_ROOT"

svnadmin create "$REPO"
svn -q mkdir -m "Create standard layout" \
    "file://$REPO/trunk" "file://$REPO/branches" "file://$REPO/tags"
svn -q checkout "file://$REPO/trunk" "$WORK"

cat > "$WORK/parser.c" <<'CODE'
int parse_header(buf_t *buf) {
    size_t len = buf->size;
    for (size_t i = 0; i <= len; i++) {
        if (buf->data[i] == ':') return i;
    }
    return -1;
}
CODE

cat > "$WORK/README" <<'CODE'
A small parser.
CODE

cd "$WORK"
svn -q add parser.c README
svn -q commit -m "Add the header parser"
svn -q update

# The edit the demo commits: an off-by-one fix, plus a stray debug line so
# `git add -p` has something worth declining.
cat > "$WORK/parser.c" <<'CODE'
int parse_header(buf_t *buf) {
    size_t len = buf->size;
    for (size_t i = 0; i < len; i++) {
        if (buf->data[i] == ':') return i;
    }
    return -1;
}
CODE
echo "scratch notes" > "$WORK/notes.md"
