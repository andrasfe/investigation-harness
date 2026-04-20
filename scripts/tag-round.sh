#!/usr/bin/env bash
# Tag HEAD as `round-N`. Closes the round: subsequent commits are round N+1.
#
# Usage:
#   scripts/tag-round.sh            # auto-number from existing tags
#   scripts/tag-round.sh 2          # force tag round-2
#   scripts/tag-round.sh --push     # also push the tag to origin

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PUSH=0
NUM=""
for a in "$@"; do
  case "$a" in
    --push) PUSH=1 ;;
    [0-9]*) NUM="$a" ;;
    *) echo "usage: $0 [N] [--push]" >&2; exit 2 ;;
  esac
done

if [[ -z "$NUM" ]]; then
  LAST="$(git tag --list 'round-*' --sort=-v:refname | head -1)"
  if [[ -z "$LAST" ]]; then
    NUM=1
  else
    NUM="$(( ${LAST#round-} + 1 ))"
  fi
fi

TAG="round-${NUM}"
if git rev-parse --verify --quiet "refs/tags/$TAG" >/dev/null; then
  echo "error: tag $TAG already exists" >&2
  exit 2
fi

git tag -a "$TAG" -m "scout: close of round ${NUM}"
echo "tagged HEAD as $TAG"

if [[ "$PUSH" == "1" ]]; then
  git push origin "$TAG"
  echo "pushed $TAG to origin"
fi
