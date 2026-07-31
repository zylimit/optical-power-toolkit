#!/usr/bin/env bash
# skill-description-lint.sh - validate skill discovery metadata.
set -eu

ROOT=$(cd "$(dirname "$0")/../../../.." && pwd)

python3 - "$ROOT" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
failures = []

def fail(path, message):
    failures.append(f"{path.relative_to(root)}: {message}")

for path in sorted((root / ".agents" / "skills").glob("*/SKILL.md")):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        fail(path, "missing frontmatter")
        continue

    fields = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            fail(path, f"invalid frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')

    name = fields.get("name", "")
    desc = fields.get("description", "")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        fail(path, f"invalid name: {name!r}")
    if not desc:
        fail(path, "missing description")
        continue
    if len(desc) > 180:
        fail(path, f"description too long: {len(desc)} chars")
    if not (desc.startswith("当") or desc.startswith("由")):
        fail(path, "description should describe trigger conditions first")

    # Discovery metadata should not be a mini workflow. If it summarizes the
    # process, models may follow the summary instead of loading the full skill.
    workflow_tokens = ["通过", "分阶段", "输出", "生成", "执行", "支持", "内置", "维护"]
    hits = [token for token in workflow_tokens if token in desc]
    if hits:
        fail(path, "description contains workflow summary token(s): " + ", ".join(hits))

if failures:
    print("skill-description-lint: failed", file=sys.stderr)
    for item in failures:
        print(f"- {item}", file=sys.stderr)
    raise SystemExit(1)

print("skill-description-lint: passed")
PY
