#!/usr/bin/env bash
# Build the scout-builder image used to run mvn/gradle/jacoco against
# target repos without polluting the host with a JDK.
#
# Usage:
#   bash scripts/build-docker-image.sh [tag]
#
# The image defaults to `scout-builder:latest`. Override via $SCOUT_BUILD_IMAGE
# or the positional arg.

set -euo pipefail

IMAGE_TAG="${1:-${SCOUT_BUILD_IMAGE:-scout-builder:latest}}"

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker CLI not found on PATH" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "error: docker daemon not reachable. Start Docker Desktop and retry." >&2
  exit 1
fi

echo "==> building $IMAGE_TAG from docker/Dockerfile.builder"
docker build \
  --tag "$IMAGE_TAG" \
  --file docker/Dockerfile.builder \
  docker/

echo ""
echo "==> verifying"
docker run --rm "$IMAGE_TAG" bash -lc 'mvn --version && echo --- && gradle --version && echo --- && java -version'

echo ""
echo "scout-builder image ready: $IMAGE_TAG"
echo ""
echo "To activate docker-backed builds in scout, add to .env:"
echo "  SCOUT_USE_DOCKER=1"
echo "  SCOUT_BUILD_IMAGE=$IMAGE_TAG"
echo "  # SCOUT_DRY_RUN=0   # disable dry-run; real builds go through the container"
