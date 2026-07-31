#!/usr/bin/env bash
# Usage: bash .agents/scripts/fast-mode.sh on [hours] | off | status
set -u

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P) || exit 1
RUNTIME="$ROOT/.codex/runtime/harness.mjs"
ACTION=${1-status}
HOURS=${2-24}

if [ "$#" -gt 2 ]; then
  echo "usage: bash .agents/scripts/fast-mode.sh on [hours] | off | status" >&2
  exit 2
fi

exec node "$RUNTIME" fast "$ACTION" --hours "$HOURS" --root "$ROOT"
