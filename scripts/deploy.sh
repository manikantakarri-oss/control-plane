#!/usr/bin/env bash
# Deploy (or upgrade) the Data Plane in a customer workspace that has already
# been onboarded with scripts/onboard-customer.sh.
#
#   scripts/deploy.sh [--profile acme] [--app-name agent-portal] [--scope agent-portal] [--build-ui] [--restart]
#
# Databricks auth: --profile, or (in CI) DATABRICKS_HOST / DATABRICKS_CLIENT_ID /
# DATABRICKS_CLIENT_SECRET in the environment.
#
# --build-ui  rebuild data-plane/web from data-plane/ui first (needs Node 18+)
# --restart   stop + start the app afterwards; required after a user_api_scopes
#             change, because the runtime keeps minting OBO tokens with the
#             scope set it booted with.
set -euo pipefail

PROFILE="" APP_NAME="agent-portal" SCOPE="agent-portal" BUILD_UI=0 RESTART=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --build-ui) BUILD_UI=1; shift ;;
    --restart) RESTART=1; shift ;;
    *) echo "usage: $0 [--profile <cli-profile>] [--app-name N] [--scope S] [--build-ui] [--restart]" >&2; exit 2 ;;
  esac
done
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
cd "$ROOT"

if [ "$BUILD_UI" = 1 ]; then
  echo "==> building UI"
  (cd data-plane/ui && npm ci && npm run build)
  rm -rf data-plane/web && cp -r data-plane/ui/out data-plane/web
fi
[ -f data-plane/web/index.html ] || { echo "!! data-plane/web is missing; rerun with --build-ui" >&2; exit 1; }

BUNDLE=(-t customer "${DBX[@]}" --var "app_name=$APP_NAME" --var "secret_scope=$SCOPE")

echo "==> deploying bundle"
# The app's service principal is provisioned asynchronously on first create, so
# the first deploy can fail on a resource that references it. One retry covers it.
databricks bundle deploy "${BUNDLE[@]}" || { echo "    retrying once..."; sleep 20; databricks bundle deploy "${BUNDLE[@]}"; }

echo "==> deploying app source and starting"
databricks bundle run agent_portal "${BUNDLE[@]}"

if [ "$RESTART" = 1 ]; then
  echo "==> restarting so new scopes take effect"
  databricks apps stop "$APP_NAME" "${DBX[@]}"
  databricks apps start "$APP_NAME" "${DBX[@]}"
fi

databricks apps get "$APP_NAME" "${DBX[@]}" -o json | "$PY" -c \
  "import sys,json; a=json.load(sys.stdin); print('    url:', a.get('url')); print('    scopes:', a.get('effective_user_api_scopes'))"
