#!/usr/bin/env bash
set -e

pip install -r requirements.txt

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
