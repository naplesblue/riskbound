#!/usr/bin/env bash
# Local fallback for the forward log: append, verify, and commit locally. It never pushes.
#
# To publish from a local cron job, add after the commit (only once the owner has confirmed publishing):
#   git push
#   uv run riskbound track observe --github OWNER/REPO
#   git add track/observations.jsonl && git commit -m "track: observe $(date -u +%F)" && git push
# A row only counts as forward evidence once a recorded PushEvent shows it reached the public remote
# before its deadline (see track/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."

uv run riskbound track append && uv run riskbound track verify

if [ -n "$(git status --porcelain track/)" ]; then
  git add track/
  git commit -m "track: append $(date -u +%F)"
fi
