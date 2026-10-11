#!/usr/bin/env bash
set -e

pip install -r requirements.txt

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$APP_ROOT/.render-runtime"
JAVA_VERSION="17.0.20.1_1"
JAVA_ARCHIVE="OpenJDK17U-jre_x64_linux_hotspot_${JAVA_VERSION}.tar.gz"
JAVA_URL="https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/${JAVA_ARCHIVE}"
JAVA_SHA256="0b2b640e3046b64c8ec504de0ab9d91bb5610182bda21fad454681ce54d45a62"
LAVALINK_VERSION="4.2.2"
LAVALINK_SHA256="8cb801e591072c3689fafd71ccf571a95a4ead3cc35dfc045e157d763d89119a"

mkdir -p "$RUNTIME_DIR/jre"
curl -fsSL "$JAVA_URL" -o /tmp/lavalink-jre.tar.gz
echo "$JAVA_SHA256  /tmp/lavalink-jre.tar.gz" | sha256sum -c -
tar -xzf /tmp/lavalink-jre.tar.gz -C "$RUNTIME_DIR/jre" --strip-components=1
rm -f /tmp/lavalink-jre.tar.gz
"$RUNTIME_DIR/jre/bin/java" -version

curl -fsSL \
  "https://github.com/lavalink-devs/Lavalink/releases/download/${LAVALINK_VERSION}/Lavalink.jar" \
  -o "$APP_ROOT/Lavalink.jar"
echo "$LAVALINK_SHA256  $APP_ROOT/Lavalink.jar" | sha256sum -c -
echo "Lavalink ${LAVALINK_VERSION} downloaded and verified"

# yt-dlp needs Node 22+ to solve YouTube's player challenges, and bgutil's
# PO-token helper needs it too. Render's native runtimes ship Node, but the
# version isn't guaranteed - check it and drop a local copy under ./node when
# the system one is too old or missing (cogs/music.py looks there first after
# PATH).
NODE_MAJOR_MIN=22
need_node=1
if command -v node >/dev/null 2>&1; then
  echo "node: $(node --version)"
  if node -e "process.exit(Number(process.versions.node.split('.')[0]) >= ${NODE_MAJOR_MIN} ? 0 : 1)"; then
    need_node=0
  else
    echo "node on PATH is older than ${NODE_MAJOR_MIN}; fetching a local copy"
  fi
else
  echo "node not found on PATH; fetching a local copy"
fi

if [ "$need_node" = "1" ]; then
  NODE_VERSION=v22.14.0
  NODE_DIST="node-${NODE_VERSION}-linux-x64"
  curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/${NODE_DIST}.tar.gz" -o /tmp/node.tar.gz
  mkdir -p node
  tar -xzf /tmp/node.tar.gz -C node --strip-components=1
  rm -f /tmp/node.tar.gz
  echo "node: $(node/bin/node --version) (installed to ./node)"
fi

# Optional: the PO-token provider. A datacenter IP often gets a sign-in
# challenge from YouTube; this helper mints proof-of-origin tokens locally,
# which yt-dlp then attaches to its requests. Failure is not fatal - the bot
# still runs, it just may hit that challenge more often.
if [ ! -f potprovider/server/build/generate_once.js ]; then
  (
    set -e
    rm -rf potprovider
    git clone --depth 1 --single-branch --branch 2.0.1 \
      https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git potprovider
    cd potprovider/server
    npm ci --no-audit --no-fund
    npx tsc
  ) || echo "WARN: PO-token provider build skipped; YouTube may ask the server to sign in"
else
  echo "PO-token provider already built"
fi
