#!/usr/bin/env bash
set -e

pip install -r requirements.txt

rm -rf potprovider node
git clone --depth 1 --single-branch --branch 2.0.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git potprovider
cd potprovider/server
(npm ci || npm install)
npx tsc
cd ../..

curl -fsSL -o node.tar.xz https://nodejs.org/dist/v22.12.0/node-v22.12.0-linux-x64.tar.xz
tar -xJf node.tar.xz
mv node-v22.12.0-linux-x64 node
rm node.tar.xz