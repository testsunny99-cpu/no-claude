#!/usr/bin/env bash
# 一键安装：写入遥测关闭开关。相当于 ./no-claude.sh apply [--guard]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./no-claude.sh apply "$@"