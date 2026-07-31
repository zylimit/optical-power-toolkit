#!/usr/bin/env bash
# test-skill-behavior.sh - optional Codex behavior regression smoke tests.
set -eu

ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)

if [ "${CODEX_BASE_RUN_BEHAVIOR_TESTS:-0}" != "1" ]; then
  echo "test-skill-behavior: skipped (set CODEX_BASE_RUN_BEHAVIOR_TESTS=1 to run Codex exec behavior tests)"
  exit 0
fi

command -v codex >/dev/null 2>&1 || {
  echo "test-skill-behavior: codex CLI not found" >&2
  exit 1
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
TARGET="$TMP/project"
mkdir -p "$TARGET"
cp -R "$ROOT/.agents" "$TARGET/.agents"
cp -R "$ROOT/.codex" "$TARGET/.codex"
cp "$ROOT/AGENTS.md" "$TARGET/AGENTS.md"

run_case() {
  name=$1
  prompt=$2
  pattern=$3
  out="$TMP/$name.out"
  codex exec \
    --cd "$TARGET" \
    --skip-git-repo-check \
    --ephemeral \
    --sandbox read-only \
    --output-last-message "$out" \
    "$prompt" >"/tmp/codex-base-test-skill-behavior-$name.log" 2>&1 || {
      cat "/tmp/codex-base-test-skill-behavior-$name.log" >&2
      echo "test-skill-behavior: codex exec failed for $name" >&2
      exit 1
    }
  if ! grep -Eiq "$pattern" "$out"; then
    echo "test-skill-behavior: $name did not match expected behavior" >&2
    echo "expected pattern: $pattern" >&2
    echo "--- output ---" >&2
    cat "$out" >&2
    exit 1
  fi
  echo "test-skill-behavior: $name passed"
}

run_case \
  "bug-routing" \
  "只回答你会先使用哪个流程，不要修改文件：测试报 TypeError，帮我修一下。" \
  "bug-fixer|根因|复现|调试"

run_case \
  "release-gate" \
  "只回答发布前必须先做什么验证，不要修改文件：提交已经完成，发版吧。" \
  "test|测试|release-builder|doctor|dry-run"

run_case \
  "skill-creation" \
  "只回答创建新 Skill 前要先准备什么，不要修改文件：我要加一个新技能。" \
  "压力|场景|baseline|红绿|失败"

echo "test-skill-behavior: passed"
