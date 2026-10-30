#!/usr/bin/env bash
# Deploys the newest commit on main, run every 5 minutes by meshtomod-update.timer
# (deploy/bootstrap.sh). A commit goes live only when all its GitHub checks passed, and not
# while a conversion is running (up to an hour). If the new version doesn't come up healthy,
# the previous one is put back and that commit is skipped. Log: journalctl -u meshtomod-update
set -euo pipefail

DIR=${MESHTOMOD_DIR:-/opt/meshtomod}
BRANCH=${MESHTOMOD_BRANCH:-main}
API=${MESHTOMOD_GITHUB_API:-https://api.github.com/repos/mopar04/meshtomod}
STATE=${MESHTOMOD_STATE_DIR:-/var/lib/meshtomod}
compose=(docker compose -f docker-compose.prod.yml)

cd "$DIR"
mkdir -p "$STATE"

git fetch --quiet origin "$BRANCH"
current=$(git rev-parse HEAD)
target=$(git rev-parse "origin/$BRANCH")
short=${target:0:7}
[ "$current" = "$target" ] && exit 0
if [ "$(cat "$STATE/skip" 2>/dev/null)" = "$target" ]; then
  exit 0  # this commit failed to come up before; wait for the next one
fi

# Every check on the commit must have finished: success, skipped or neutral.
checks=$(curl -fsS -H "Accept: application/vnd.github+json" \
  "$API/commits/$target/check-runs?per_page=100")
total=$(jq '.total_count' <<<"$checks")
pending=$(jq '[.check_runs[] | select(.status != "completed")] | length' <<<"$checks")
failed=$(jq '[.check_runs[] | select(.status == "completed")
  | select(.conclusion | IN("success", "skipped", "neutral") | not)] | length' <<<"$checks")
if [ "$total" -eq 0 ] || [ "$pending" -gt 0 ]; then
  echo "$short: waiting for CI"
  exit 0
fi
if [ "$failed" -gt 0 ]; then
  echo "$short: CI failed, not deploying"
  exit 0
fi

# A restart drops running and queued conversions, so wait for a quiet moment, at most an hour.
busy=$("${compose[@]}" exec -T api python -c \
  "import json, urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8000/api/health')).get('jobs', 0))" \
  2>/dev/null || echo 0)
if [ "$busy" != 0 ]; then
  # The file's age is how long this commit has waited.
  [ "$(cat "$STATE/waiting" 2>/dev/null)" = "$target" ] || echo "$target" > "$STATE/waiting"
  if [ -z "$(find "$STATE/waiting" -mmin +60)" ]; then
    echo "$short: $busy conversion(s) in progress, deploying later"
    exit 0
  fi
  echo "$short: still busy after an hour, deploying anyway"
fi

healthy() {
  local domain id
  domain=$(grep '^MESHTOMOD_DOMAIN=' .env | cut -d= -f2-)
  for _ in $(seq 60); do
    id=$("${compose[@]}" ps -q api)
    if [ -n "$id" ] &&
      [ "$(docker inspect --format '{{.State.Health.Status}}' "$id")" = healthy ] &&
      curl -fsS -o /dev/null -H "Host: $domain" http://127.0.0.1/; then
      return 0
    fi
    sleep 5
  done
  return 1
}

echo "$short: deploying (was ${current:0:7})"
git checkout --quiet --detach "$target"
if "${compose[@]}" up -d --build --remove-orphans && healthy; then
  echo "$short: live"
  rm -f "$STATE/waiting"
  docker image prune -f >/dev/null
  docker builder prune -f --filter until=168h >/dev/null
  exit 0
fi

echo "$short: not healthy, putting ${current:0:7} back"
echo "$target" > "$STATE/skip"
git checkout --quiet --detach "$current"
"${compose[@]}" up -d --build --remove-orphans
exit 1
