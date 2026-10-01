#!/usr/bin/env bash
# tests/fixtures/shots/build.sh — bundle entry.jsx for the screenshot harness.
# Needs a node_modules holding react, react-dom and esbuild (the repo ships no
# node dependencies; point RU at any checkout that has them).
# Usage: RU=/path/to/node_modules bash tests/fixtures/shots/build.sh
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
: "${RU:?set RU to a node_modules dir containing react, react-dom, esbuild}"
cd "$here"
NODE_PATH="$RU" "$RU/.bin/esbuild" entry.jsx --bundle --minify \
  --format=iife --loader:.json=json \
  --define:process.env.NODE_ENV='"production"' --jsx=automatic \
  --outfile=out/app.js
echo "bundled out/app.js ($(wc -c < out/app.js) bytes)"
