#!/usr/bin/env bash
# 卸载：移除本工具写入的开关并卸载守护。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./no-claude.sh uninstall "$@"