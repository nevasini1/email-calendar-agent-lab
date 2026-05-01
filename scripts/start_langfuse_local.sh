#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANGFUSE_DIR="${ROOT_DIR}/.langfuse/langfuse"

mkdir -p "$(dirname "${LANGFUSE_DIR}")"

if [ ! -d "${LANGFUSE_DIR}/.git" ]; then
  git clone https://github.com/langfuse/langfuse.git "${LANGFUSE_DIR}"
fi

cd "${LANGFUSE_DIR}"

cat <<'MSG'
Starting local Langfuse via the official Docker Compose setup.

Before using this for anything beyond local testing, inspect docker-compose.yml
and replace all secrets marked CHANGEME as recommended by Langfuse.

When the web container is ready, open:
  http://localhost:3000

Create a project, copy its public/secret keys, then put them in:
  .env.langfuse.local
MSG

docker compose up
