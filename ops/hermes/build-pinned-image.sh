#!/bin/sh
set -eu

HERMES_REPOSITORY="https://github.com/NousResearch/hermes-agent.git"
HERMES_COMMIT="5fc308a70719a83cccdbba4c0e39c23f5a8239d5"
HERMES_IMAGE="dental-legal-hermes:${HERMES_COMMIT}"

workdir="$(mktemp -d)"
cleanup() {
  rm -rf "$workdir"
}
trap cleanup EXIT HUP INT TERM

repo="$workdir/hermes-agent"
git init -q "$repo"
git -C "$repo" remote add origin "$HERMES_REPOSITORY"
git -C "$repo" fetch --depth 1 origin "$HERMES_COMMIT"
git -C "$repo" checkout -q --detach FETCH_HEAD

actual="$(git -C "$repo" rev-parse HEAD)"
if [ "$actual" != "$HERMES_COMMIT" ]; then
  echo "Hermes checkout mismatch: expected $HERMES_COMMIT, got $actual" >&2
  exit 1
fi

# The upstream Dockerfile and lockfiles are part of the immutable checkout.
# Do not replace this build with a floating nousresearch/hermes-agent tag.
docker build --tag "$HERMES_IMAGE" "$repo"
printf '%s\n' "Built $HERMES_IMAGE from $actual"
