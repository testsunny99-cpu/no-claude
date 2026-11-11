#!/usr/bin/env bash
# 验证：检查开关状态并复验遥测是否仍被生成。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./no-claude.sh verify "$@"