#!/bin/sh
# Capture the real output behind the README's still images.
#
#     sh docs/demo/capture.sh
#
# Each capture runs against a fresh demo checkout, so the text in the SVGs is
# exactly what the tool prints -- prompts included, nothing retouched.

set -eu

ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
DEMO_ROOT=/tmp/svngit-demo
# Kept outside DEMO_ROOT, which setup.sh wipes on each run.
CAP=/tmp/svngit-captures
export PATH="$ROOT/bin:$ROOT/.venv/bin:$PATH"
export SVNGIT_STATE_DIR="$DEMO_ROOT/state"

rm -rf "$CAP"
mkdir -p "$CAP"

# ---------------------------------------------------------------- add -p
DEMO_ROOT="$DEMO_ROOT" sh "$ROOT/docs/demo/setup.sh"
cd "$DEMO_ROOT/work"
# One real fix plus one stray debug line, far enough apart to split.
cat > parser.c <<'CODE'
int parse_header(buf_t *buf) {
    size_t len = buf->size;
    for (size_t i = 0; i < len; i++) {
        if (buf->data[i] == ':') return i;
    }
    printf("DEBUG: no colon\n");
    return -1;
}
CODE

{
  echo '$ git add -p'
  printf 's\ny\nn\n' | svngit add -p 2>&1
  echo '$ git status --short'
  svngit status --short 2>&1
} > "$CAP/add-p.txt"

# ------------------------------------------------- it explains itself
{
  echo '$ git rebase main'
  svngit rebase main 2>&1 || true
  echo '$'
  echo '$ git merge --strategy=ours feature-x'
  svngit merge --strategy=ours feature-x 2>&1 || true
  echo '$'
  echo '$ git push --tags'
  svngit push --tags 2>&1 || true
} > "$CAP/explains.txt"

# ------------------------------------------------------------- searching
DEMO_ROOT="$DEMO_ROOT" sh "$ROOT/docs/demo/setup.sh"
cd "$DEMO_ROOT/work"
svngit add notes.md >/dev/null 2>&1 || true
svngit commit -q -m "Add notes" >/dev/null 2>&1 || true
svngit push -q >/dev/null 2>&1 || true
svngit tag v1.0 >/dev/null 2>&1 || true
echo "build output" > build.log
{
  echo '$ git grep -n parse_header'
  svngit grep -n parse_header 2>&1 || true
  echo '$ git describe'
  svngit describe 2>&1 || true
  echo '$ git shortlog -s'
  svngit shortlog -s 2>&1 || true
} > "$CAP/search.txt"

echo "captured into $CAP"
