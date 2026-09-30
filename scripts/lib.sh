# Shared helpers for the deploy scripts. Source after setting PROFILE (may be
# empty, in which case the Databricks CLI authenticates from DATABRICKS_* env).

# First interpreter that actually runs (on Windows `python3` can be a Store stub).
PY=""
for _p in python3 python; do
  if command -v "$_p" >/dev/null && "$_p" -c "" 2>/dev/null; then PY="$_p"; break; fi
done
[ -n "$PY" ] || { echo "!! python 3 is required" >&2; exit 1; }

# Extra CLI args selecting the workspace.
DBX=()
[ -n "${PROFILE:-}" ] && DBX=(-p "$PROFILE")

json() { "$PY" -c "import sys,json; print(json.load(sys.stdin)$1)"; }

normalise_host() {
  local h="${1#https://}"
  h="${h#http://}"
  h="${h%%/*}"
  echo "$h" | tr '[:upper:]' '[:lower:]'
}

workspace_host() {
  if [ -n "${PROFILE:-}" ]; then
    databricks auth env --profile "$PROFILE" -o json | json "['env']['DATABRICKS_HOST']"
  else
    : "${DATABRICKS_HOST:?set DATABRICKS_HOST or pass --profile}"
    echo "$DATABRICKS_HOST"
  fi
}

# Hide a secret from GitHub Actions logs; a no-op elsewhere.
mask() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::add-mask::$1"; return 0; }

# OAuth client-credentials token for a Databricks workspace: oauth_token HOST CLIENT_ID SECRET
oauth_token() {
  local host="${1%/}"
  case "$host" in http*://*) ;; *) host="https://$host" ;; esac
  curl -sSf -u "$2:$3" -d 'grant_type=client_credentials&scope=all-apis' "$host/oidc/v1/token" \
    | json "['access_token']"
}

# What goes in Authorization when calling the Control Plane. When it is hosted as
# a Databricks App, the Apps proxy requires a Databricks OAuth token for the
# provider workspace; the admin key then travels in X-Admin-Key.
#   CONTROL_PLANE_BEARER   a token already obtained (the deploy workflow sets it)
#   CONTROL_PLANE_CLIENT_ID + CONTROL_PLANE_CLIENT_SECRET + CONTROL_PLANE_OAUTH_HOST
#   CONTROL_PLANE_PROFILE  your own Databricks login for the provider workspace
#   none of these          the Control Plane is not behind Databricks; send the admin key
cp_auth() {
  if [ -n "${CONTROL_PLANE_BEARER:-}" ]; then
    echo "$CONTROL_PLANE_BEARER"
  elif [ -n "${CONTROL_PLANE_CLIENT_ID:-}" ]; then
    : "${CONTROL_PLANE_OAUTH_HOST:?set CONTROL_PLANE_OAUTH_HOST (the provider workspace URL)}"
    oauth_token "$CONTROL_PLANE_OAUTH_HOST" "$CONTROL_PLANE_CLIENT_ID" "${CONTROL_PLANE_CLIENT_SECRET:?}"
  elif [ -n "${CONTROL_PLANE_PROFILE:-}" ]; then
    databricks auth token -p "$CONTROL_PLANE_PROFILE" -o json | json "['access_token']"
  else
    echo "$CONTROL_PLANE_ADMIN_KEY"
  fi
}

# cp_api METHOD PATH [JSON-BODY] -> response body on 2xx, exits otherwise.
cp_api() {
  local method="$1" path="$2" body="${3:-}" out code auth
  auth=$(cp_auth) || return 1
  local args=(-sS -w '\n%{http_code}' -X "$method" "$CONTROL_PLANE_URL$path"
              -H "Authorization: Bearer $auth" -H "X-Admin-Key: $CONTROL_PLANE_ADMIN_KEY"
              -H 'Content-Type: application/json')
  [ -n "$body" ] && args+=(-d "$body")
  out=$(curl "${args[@]}")
  code=$(echo "$out" | tail -n1)
  out=$(echo "$out" | sed '$d')
  if [ "${code:0:1}" != "2" ]; then
    echo "!! Control Plane $method $path -> $code: $out" >&2
    return 1
  fi
  echo "$out"
}
