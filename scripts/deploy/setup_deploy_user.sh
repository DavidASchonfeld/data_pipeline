#!/bin/bash
# One-time helper: prepare a headless DEPLOY_USER so ./scripts/deploy.sh --snowflake-setup runs without MFA.
#
# What it AUTOMATES (locally, no Snowflake login needed):
#   - generates an RSA key pair for the deploy user (idempotent — reuses an existing key)
#   - prints the exact Snowsight SQL (with the public key already filled in) and the .env.deploy lines
#
# What it CANNOT do (creating a Snowflake user needs an ACCOUNTADMIN session, and the whole point is that the
# scripted ACCOUNTADMIN path is currently MFA-blocked): you run the printed SQL once in Snowsight, where the
# browser handles MFA. After that, every deploy is fully headless.
#
# Usage:  scripts/deploy/setup_deploy_user.sh [key_dir]   (key_dir defaults to ~/.snowflake)

set -euo pipefail

KEY_DIR="${1:-$HOME/.snowflake}"
KEY_PATH="$KEY_DIR/deploy_user_rsa_key.p8"
PUB_PATH="$KEY_DIR/deploy_user_rsa_key.pub"
DEPLOY_USER="DEPLOY_USER"

mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"

if [ -f "$KEY_PATH" ]; then
    echo "Reusing existing private key: $KEY_PATH"
    # Re-derive the public key in case only the private key is present.
    openssl rsa -in "$KEY_PATH" -pubout -out "$PUB_PATH" 2>/dev/null
else
    echo "Generating RSA key pair in $KEY_DIR ..."
    # Unencrypted PKCS#8 (-nocrypt) so the deploy needs no passphrase — matches the runtime service key.
    openssl genrsa 2048 2>/dev/null | openssl pkcs8 -topk8 -inform PEM -out "$KEY_PATH" -nocrypt
    openssl rsa -in "$KEY_PATH" -pubout -out "$PUB_PATH" 2>/dev/null
    chmod 600 "$KEY_PATH"
    echo "Created $KEY_PATH (private — keep it secret, chmod 600) and $PUB_PATH (public)."
fi

# Public-key body with the PEM header/footer and newlines stripped — the form Snowflake's RSA_PUBLIC_KEY wants.
PUB_BODY="$(grep -v 'PUBLIC KEY' "$PUB_PATH" | tr -d '\n')"

cat <<EOF

╔══════════════════════════════════════════════════════════════════════════════╗
║  DO THESE TWO MANUAL STEPS ONCE, THEN every deploy is headless (no MFA).      ║
╚══════════════════════════════════════════════════════════════════════════════╝

── STEP 1 — paste into a Snowsight worksheet, signed in as ACCOUNTADMIN ───────────
   (the browser handles your MFA; this is the only interactive login you need)

CREATE USER IF NOT EXISTS ${DEPLOY_USER}
  RSA_PUBLIC_KEY = '${PUB_BODY}'
  DEFAULT_ROLE   = ACCOUNTADMIN
  COMMENT = 'Headless bootstrap user for ./scripts/deploy.sh --snowflake-setup';
GRANT ROLE ACCOUNTADMIN TO USER ${DEPLOY_USER};
-- No password is set, so this user can ONLY authenticate with the key above — never an interactive login.
-- To rotate the key later: re-run this script, then
--   ALTER USER ${DEPLOY_USER} SET RSA_PUBLIC_KEY = '<new-public-key-body>';

── STEP 2 — set these in .env.deploy (replace the personal admin values) ──────────

SNOWFLAKE_ADMIN_USER=${DEPLOY_USER}
SNOWFLAKE_ADMIN_PRIVATE_KEY_PATH=${KEY_PATH}
# SNOWFLAKE_ADMIN_PASSWORD is no longer used and can be removed.

── THEN run it ────────────────────────────────────────────────────────────────────

./scripts/deploy.sh --snowflake-setup

EOF
