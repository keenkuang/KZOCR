#!/usr/bin/env bash
# Regenerate the proofread UI Tailwind CSS from the templates.
#
# The generated static/vendor/tailwind.min.css is committed, so the Python-only
# CI and the PyInstaller desktop bundle need no Node toolchain. Run this script
# only when you change Tailwind utility classes inside kzocr/proofread/templates/*.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
npx -y tailwindcss@3 -c tailwind.config.js \
  -i kzocr/proofread/static/src/input.css \
  -o kzocr/proofread/static/vendor/tailwind.min.css --minify
echo "Generated kzocr/proofread/static/vendor/tailwind.min.css"
