#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and set GROQ_API_KEY." >&2
  exit 1
fi

set -a
source .env
set +a

python3 -m signal_stream dashboard --config configs/ai_tech.toml
