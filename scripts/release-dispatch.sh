#!/usr/bin/env bash
set -euo pipefail

version="$1"
canary="$2"
case "$canary" in
  true|false) ;;
  *) echo 'Canary must be true or false.' >&2; exit 2 ;;
esac

gh workflow run release.yml --repo general-inference/skippy --ref main \
  -f "version=$version" -f "canary=$canary"
