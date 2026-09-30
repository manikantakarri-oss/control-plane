#!/usr/bin/env bash
# First-time install from a workstation: register with the Control Plane,
# provision credentials, deploy.
#
#   export CONTROL_PLANE_URL=https://control-plane.example.com
#   export CONTROL_PLANE_ADMIN_KEY=...
#   scripts/onboard-customer.sh --profile acme --customer "Acme Corp" [--github-environment acme]
#   scripts/onboard-customer.sh --workspace-host https://adb-1.azuredatabricks.net --customer "Acme Corp" \
#     --github-environment acme --register-only     # no customer CLI access needed
#
# Prefer the GitHub route once a customer's GitHub Environment exists: register
# with --github-environment and --register-only, then deploy from the Control
# Plane (POST /api/v1/deployments/{id}/deploy with mode=onboard).
#
# Requires: databricks CLI, curl, python3.
set -euo pipefail

usage() {
  echo "usage: $0 --profile <cli-profile> --customer <name> [--workspace-host H] [--app-name N] [--scope S] [--github-environment E] [--register-only]" >&2
  exit 2
}

PROFILE="" WS_HOST="" CUSTOMER="" APP_NAME="agent-portal" SCOPE="agent-portal" GH_ENV="" REGISTER_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --customer) CUSTOMER="$2"; shift 2 ;;
    --workspace-host) WS_HOST="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --github-environment) GH_ENV="$2"; shift 2 ;;
    --register-only) REGISTER_ONLY=1; shift ;;
    *) usage ;;
  esac
done
[ -n "$CUSTOMER" ] || usage
# Deploying needs the customer CLI profile; registering alone needs only the host.
[ -n "$PROFILE" ] || { [ "$REGISTER_ONLY" = 1 ] && [ -n "$WS_HOST" ]; } || usage
: "${CONTROL_PLANE_URL:?set CONTROL_PLANE_URL}"
: "${CONTROL_PLANE_ADMIN_KEY:?set CONTROL_PLANE_ADMIN_KEY}"
CONTROL_PLANE_URL="${CONTROL_PLANE_URL%/}"
export CONTROL_PLANE_URL CONTROL_PLANE_ADMIN_KEY

HERE="$(dirname "$0")"
source "$HERE/lib.sh"

HOST="${WS_HOST:-$(workspace_host)}"
echo "==> customer workspace: $HOST"

echo "==> registering with the Control Plane"
BODY=$("$PY" -c 'import json,sys; d={"customer_name": sys.argv[1], "workspace_host": sys.argv[2], "app_name": sys.argv[3]}
if sys.argv[4]: d["github_environment"] = sys.argv[4]
print(json.dumps(d))' "$CUSTOMER" "$HOST" "$APP_NAME" "$GH_ENV")
if ! RESP=$(cp_api POST /api/v1/deployments "$BODY"); then
  echo "   (409 means it is already registered: use scripts/provision-credentials.sh + scripts/deploy.sh," >&2
  echo "    or deploy it from the Control Plane)" >&2
  exit 1
fi
DEP_ID=$(echo "$RESP" | json "['id']")
echo "    deployment id: $DEP_ID"

if [ "$REGISTER_ONLY" = 1 ]; then
  echo "==> registered only. Deploy with: POST $CONTROL_PLANE_URL/api/v1/deployments/$DEP_ID/deploy {\"mode\":\"onboard\"}"
  exit 0
fi

"$HERE/provision-credentials.sh" --deployment-id "$DEP_ID" --profile "$PROFILE" --scope "$SCOPE"
"$HERE/deploy.sh" --profile "$PROFILE" --app-name "$APP_NAME" --scope "$SCOPE"

cat <<EOF

==> done. Remaining customer-side step (see data-plane/README.md, "Onboarding an agent"):
    share each agent endpoint with the app's service principal, e.g.
      DATABRICKS_CONFIG_PROFILE=$PROFILE python data-plane/scripts/sync_agents.py --apply
    The deployment should report 'online' within a minute:
      GET $CONTROL_PLANE_URL/api/v1/deployments/$DEP_ID
EOF
