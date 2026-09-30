#!/usr/bin/env bash
# Issue fresh Control Plane credentials for a registered deployment and store
# them in the customer's own secret scope.
#
#   scripts/provision-credentials.sh --deployment-id <id> [--profile acme] [--scope agent-portal]
#
# Databricks auth: --profile, or (in CI) DATABRICKS_HOST / DATABRICKS_CLIENT_ID /
# DATABRICKS_CLIENT_SECRET in the environment.
# Also needs CONTROL_PLANE_URL and CONTROL_PLANE_ADMIN_KEY, plus Control Plane
# auth as described in lib.sh.
#
# When the Control Plane is hosted as a Databricks App, set
# CONTROL_PLANE_OAUTH_HOST + HEARTBEAT_CLIENT_ID + HEARTBEAT_CLIENT_SECRET: the
# provider service principal the portal uses to get through the Apps proxy.
# It holds only CAN_USE on the Control Plane app, and every endpoint still
# demands the deployment's own token.
#
# Refuses to write anything unless the workspace these credentials reach is
# the one the Control Plane has on record for this deployment, so one
# customer's token can never land in another customer's workspace.
set -euo pipefail

DEP_ID="" PROFILE="" SCOPE="agent-portal"
while [ $# -gt 0 ]; do
  case "$1" in
    --deployment-id) DEP_ID="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    *) echo "usage: $0 --deployment-id <id> [--profile P] [--scope S]" >&2; exit 2 ;;
  esac
done
[ -n "$DEP_ID" ] || { echo "--deployment-id is required" >&2; exit 2; }
: "${CONTROL_PLANE_URL:?set CONTROL_PLANE_URL}"
: "${CONTROL_PLANE_ADMIN_KEY:?set CONTROL_PLANE_ADMIN_KEY}"
CONTROL_PLANE_URL="${CONTROL_PLANE_URL%/}"

source "$(dirname "$0")/lib.sh"

HOST=$(workspace_host)
EXPECTED=$(cp_api GET "/api/v1/deployments/$DEP_ID" | json "['workspace_host']")
if [ "$(normalise_host "$HOST")" != "$(normalise_host "$EXPECTED")" ]; then
  echo "!! refusing: credentials reach $HOST but deployment $DEP_ID is registered for $EXPECTED" >&2
  exit 1
fi
echo "==> workspace verified: $HOST"

echo "==> issuing a new heartbeat token"
DEP_TOKEN=$(cp_api POST "/api/v1/deployments/$DEP_ID/rotate-token" | json "['deployment_token']")
mask "$DEP_TOKEN"

echo "==> writing credentials to secret scope '$SCOPE'"
if ! databricks secrets list-scopes "${DBX[@]}" -o json | "$PY" -c \
  "import sys,json; d=json.load(sys.stdin); d=d.get('scopes',d) if isinstance(d,dict) else d; sys.exit(0 if any(s['name']==sys.argv[1] for s in d or []) else 1)" "$SCOPE"; then
  databricks secrets create-scope "$SCOPE" "${DBX[@]}"
fi
# One JSON document; the portal reads it as CONTROL_PLANE_CONFIG (see
# data-plane/control_plane.py). Built in Python so no value needs shell quoting.
CONFIG=$(DEP_ID="$DEP_ID" DEP_TOKEN="$DEP_TOKEN" "$PY" -c '
import json, os
e = os.environ
cfg = {"url": e["CONTROL_PLANE_URL"], "deployment_id": e["DEP_ID"], "token": e["DEP_TOKEN"]}
if e.get("HEARTBEAT_CLIENT_ID"):
    cfg["oauth"] = {"host": e["CONTROL_PLANE_OAUTH_HOST"], "client_id": e["HEARTBEAT_CLIENT_ID"],
                    "client_secret": e["HEARTBEAT_CLIENT_SECRET"]}
print(json.dumps(cfg))')
databricks secrets put-secret "$SCOPE" control-plane --string-value "$CONFIG" "${DBX[@]}"
unset DEP_TOKEN CONFIG
echo "    done"
