#!/bin/sh
# Build the Homebrew formula from the working tree, so a change to it can be
# checked before there is a release to point at.
#
#     sh packaging/homebrew/test-local.sh
#
# It copies the formula into a scratch tap with the url and sha256 swapped
# for the freshly built sdist, installs it, runs the formula's own test
# block, then removes it again.

set -eu

ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
TAP_NAME=svngit-localtest
TAP_DIR="$(brew --repository)/Library/Taps/local/homebrew-$TAP_NAME"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")
TARBALL="$ROOT/dist/svngit-$VERSION.tar.gz"

cleanup() {
    brew uninstall --force "local/$TAP_NAME/svngit" >/dev/null 2>&1 || true
    rm -rf "$TAP_DIR"
}
trap cleanup EXIT

[ -f "$TARBALL" ] || { echo "build the sdist first: python -m build" >&2; exit 1; }

mkdir -p "$TAP_DIR/Formula"
SHA=$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)
sed -e "s|url \".*\"|url \"file://$TARBALL\"|" \
    -e "s|sha256 \".*\"|sha256 \"$SHA\"|" \
    "$ROOT/packaging/homebrew/svngit.rb" > "$TAP_DIR/Formula/svngit.rb"

echo "Installing from $TARBALL ..."
brew install --build-from-source "local/$TAP_NAME/svngit"
echo "Running the formula's test block ..."
brew test "local/$TAP_NAME/svngit"
echo "Formula builds, installs and passes its own tests."
