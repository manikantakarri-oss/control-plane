#!/usr/bin/env bash
# Set up (or update) the Control Plane as a Databricks App in the PROVIDER
# workspace. Safe to re-run: existing service principals, secrets and the
# database are reused.
#
#   scripts/setup-provider.sh --profile <provider-profile> --github-repo <owner/repo> [--github-secrets]
#
# Creates, if missing:
#   * service principals  <app>-deployer  (GitHub Actions calls the Control Plane as it)
#                         <app>-heartbeat (customer portals send heartbeats as it)
#   * secret scope <scope> with admin-api-key (generated) and github-token
#   * the Lakebase instance and the app (control-plane/databricks.yml)
#
# --github-secrets  also issue fresh OAuth secrets for both service principals
#                   and write every CONTROL_PLANE_* / HEARTBEAT_* secret the
#                   deploy workflow needs into the GitHub repo (needs `gh`, with
#                   admin rights on the repo). Secrets never touch the terminal.
#
# The GitHub token the Control Plane dispatches deploys with is read from
# GITHUB_DISPATCH_TOKEN when set: a fine-grained token on the repo with
# "Actions: Read and write" only. Without it, deploys from the Control Plane
# stay disabled (503) until you store one:
#   printf %s "$TOKEN" | databricks secrets put-secret <scope> github-token -p <profile>
#   ...then re-run this script (it restarts the app).
set -euo pipefail

PROFILE="" REPO="" APP_NAME="control-plane" SCOPE="control-plane" INSTANCE="control-plane-db" GH_SECRETS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --github-repo) REPO="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --instance) INSTANCE="$2"; shift 2 ;;
    --github-secrets) GH_SECRETS=1; shift ;;
    *) echo "usage: $0 --profile P --github-repo owner/repo [--app-name N] [--scope S] [--instance I] [--github-secrets]" >&2; exit 2 ;;
  esac
done
[ -n "$PROFILE" ] && [ -n "$REPO" ] || { echo "--profile and --github-repo are required" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
HOST=$(workspace_host)
echo "==> provider workspace: $HOST"

# --- service principals ------------------------------------------------------

ensure_sp() { # display-name -> "<id> <application-id>"
  local name="$1" found
  found=$(databricks service-principals list "${DBX[@]}" --filter "displayName eq '$name'" -o json | "$PY" -c \
    "import sys,json; d=json.load(sys.stdin) or []; print(f\"{d[0]['id']} {d[0]['applicationId']}\" if d else '')")
  if [ -z "$found" ]; then
    echo "    creating service principal $name" >&2
    found=$(databricks service-principals create --display-name "$name" "${DBX[@]}" -o json | "$PY" -c \
      "import sys,json; d=json.load(sys.stdin); print(f\"{d['id']} {d['applicationId']}\")")
  fi
  echo "$found"
}

echo "==> service principals"
read -r DEPLOYER_ID DEPLOYER_APP < <(ensure_sp "$APP_NAME-deployer")
read -r HEARTBEAT_ID HEARTBEAT_APP < <(ensure_sp "$APP_NAME-heartbeat")
echo "    $APP_NAME-deployer  $DEPLOYER_APP"
echo "    $APP_NAME-heartbeat $HEARTBEAT_APP"

# --- secrets -----------------------------------------------------------------

echo "==> secret scope '$SCOPE'"
has_scope=$(databricks secrets list-scopes "${DBX[@]}" -o json | "$PY" -c \
  "import sys,json; d=json.load(sys.stdin) or []; d=d.get('scopes',d) if isinstance(d,dict) else d; print(any(s['name']=='$SCOPE' for s in d))")
[ "$has_scope" = "True" ] || databricks secrets create-scope "$SCOPE" "${DBX[@]}"
keys=$(databricks secrets list-secrets "$SCOPE" "${DBX[@]}" -o json | "$PY" -c \
  "import sys,json; d=json.load(sys.stdin) or []; d=d.get('secrets',d) if isinstance(d,dict) else d; print(' '.join(s['key'] for s in d))")

put() { printf %s "$2" | databricks secrets put-secret "$SCOPE" "$1" "${DBX[@]}"; }
get() { databricks secrets get-secret "$SCOPE" "$1" "${DBX[@]}" -o json | "$PY" -c \
  "import sys,json,base64; print(base64.b64decode(json.load(sys.stdin)['value']).decode())"; }

case " $keys " in
  *" admin-api-key "*) echo "    admin-api-key: exists" ;;
  *) put admin-api-key "$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"; echo "    admin-api-key: generated" ;;
esac
if [ -n "${GITHUB_DISPATCH_TOKEN:-}" ]; then
  put github-token "$GITHUB_DISPATCH_TOKEN"; echo "    github-token: updated"
else
  case " $keys " in
    *" github-token "*) echo "    github-token: exists" ;;
    # An empty value keeps the app deployable with deploys switched off.
    *) put github-token ""; echo "    github-token: empty - deploys from the Control Plane are disabled until set" ;;
  esac
fi

# --- database + app ----------------------------------------------------------

cd "$ROOT/control-plane"
BUNDLE=(-t prod "${DBX[@]}" --var "app_name=$APP_NAME" --var "secret_scope=$SCOPE" --var "lakebase_instance=$INSTANCE"
        --var "github_repo=$REPO" --var "deployer_sp=$DEPLOYER_APP" --var "heartbeat_sp=$HEARTBEAT_APP")
echo "==> deploying bundle (the first run creates the Lakebase instance; this takes several minutes)"
# The app's Postgres role is provisioned asynchronously after the app is
# created, so the very first deploy can fail on the database resource. Retry once.
databricks bundle deploy "${BUNDLE[@]}" || { echo "    retrying once..."; sleep 30; databricks bundle deploy "${BUNDLE[@]}"; }
echo "==> starting app"
databricks bundle run control_plane "${BUNDLE[@]}"

URL=$(databricks apps get "$APP_NAME" "${DBX[@]}" -o json | json "['url']")
echo "    url: $URL"

# --- GitHub ------------------------------------------------------------------

if [ "$GH_SECRETS" = 1 ]; then
  echo "==> writing deploy-workflow secrets to github.com/$REPO"
  sp_secret() { databricks service-principal-secrets-proxy create "$1" "${DBX[@]}" -o json | json "['secret']"; }
  gh_set() { printf %s "$2" | gh secret set "$1" --repo "$REPO" >/dev/null && echo "    $1"; }
  gh_set CONTROL_PLANE_URL "$URL"
  gh_set CONTROL_PLANE_OAUTH_HOST "$HOST"
  gh_set CONTROL_PLANE_ADMIN_KEY "$(get admin-api-key)"
  gh_set CONTROL_PLANE_CLIENT_ID "$DEPLOYER_APP"
  gh_set CONTROL_PLANE_CLIENT_SECRET "$(sp_secret "$DEPLOYER_ID")"
  gh_set HEARTBEAT_CLIENT_ID "$HEARTBEAT_APP"
  gh_set HEARTBEAT_CLIENT_SECRET "$(sp_secret "$HEARTBEAT_ID")"
fi

cat <<EOF

==> Control Plane is up: $URL
    Call it as yourself:
      export CONTROL_PLANE_URL=$URL CONTROL_PLANE_PROFILE=$PROFILE
      export CONTROL_PLANE_ADMIN_KEY=\$(databricks secrets get-secret $SCOPE admin-api-key -p $PROFILE -o json | python -c "import sys,json,base64;print(base64.b64decode(json.load(sys.stdin)['value']).decode())")
      source scripts/lib.sh && cp_api GET /api/v1/deployments
EOF
