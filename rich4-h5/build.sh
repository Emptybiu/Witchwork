#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "==> Checking out unchanged original gameplay source"
rm -rf upstream
git clone --depth 1 --branch v1.2.4 https://github.com/iam567/rich4-renew-app.git upstream
echo "==> Applying source compatibility + synchronized start fixes"
python3 rich4-h5/patch.py upstream
echo "==> Installing original game dependencies"
(cd upstream/game && npm ci --no-audit --no-fund)
echo "==> Installing private relay dependency"
(cd rich4-h5 && npm install --omit=dev --no-audit --no-fund)
echo "==> Compiling original Vite/TypeScript game"
export RICH4_NO_ASSETS=1
export VITE_RICH4_RELAY_URL="wss://${RICH4_HOSTNAME:-rich4-h5-private-20261119.onrender.com}/relay"
(cd upstream/game && npm run build)
if [ "${RICH4_FETCH_ASSETS:-0}" = 1 ]; then
  echo "==> Fetching author's private-use asset pack (only to access-controlled server)"
  mkdir -p upstream/game/public
  curl --retry 4 --retry-delay 3 -fL -o /tmp/rich4-assets.tar https://github.com/iam567/rich4-renew-app/releases/download/assets/rich4-assets.tar
  curl --retry 4 --retry-delay 3 -fL -o /tmp/rich4-assets.tar.sha256 https://github.com/iam567/rich4-renew-app/releases/download/assets/rich4-assets.tar.sha256
  (cd /tmp && sha256sum -c rich4-assets.tar.sha256)
  tar -xf /tmp/rich4-assets.tar -C upstream/game/public
  rm -f /tmp/rich4-assets.tar /tmp/rich4-assets.tar.sha256
  test -s upstream/game/public/assets/maps/maps.json || { echo "Asset layout unexpected"; exit 1; }
fi
echo "Original Rich4 web code built. Private assets installed? ${RICH4_FETCH_ASSETS:-0}"
