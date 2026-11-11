#!/usr/bin/env bash
# no-claude — 关闭 Claude Code CLI / Claude Desktop 的遥测回传
#
#   ./no-claude.sh status          查看哪些开关还开着
#   ./no-claude.sh apply           写入开关（幂等）
#   ./no-claude.sh verify          重启应用并做实测复验
#   ./no-claude.sh clean           归档已排队的崩溃上报和会话指纹
#   ./no-claude.sh guard install   安装守护，防止 CC Switch 切换供应商后开关被覆盖
#   ./no-claude.sh uninstall       全部回滚
set -uo pipefail

VERSION="0.1.0"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/tools/config.py"
STATE_DIR="$HOME/.local/share/no-claude"

DRY_RUN=0
GUARD=0
RESTART=0

c() { printf '\033[%sm%s\033[0m' "$1" "$2"; }

usage() {
  cat <<EOF
no-claude $VERSION — 关闭 Claude 的遥测回传

用法: ./no-claude.sh <命令> [选项]

命令:
  status       列出各处的遥测开关状态
  apply        写入关闭开关（可重复执行，只补差异）
  revert       移除本工具写入的开关
  verify       重启 Claude 并检查是否仍在生成遥测数据
  clean        归档 sentry 队列和 scope 文件（默认移到 archived 目录，不删除）
  guard        守护安装/卸载/状态：install | uninstall | status
  uninstall    等价于 revert + guard uninstall

选项:
  --dry-run    只显示将要做什么，不写任何文件
  --guard      apply 时顺带安装守护（默认不装）
  --restart    verify 时自动重启 Claude
  --json       status 输出 JSON
EOF
}

need_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "需要 python3（macOS 可执行: xcode-select --install）" >&2
    exit 1
  fi
}

# ---------------------------------------------------------------- guard

guard_install() {
  mkdir -p "$STATE_DIR"
  cp "$PY" "$STATE_DIR/config.py"
  chmod 700 "$STATE_DIR/config.py"
  cat > "$STATE_DIR/guard.sh" <<'SH'
#!/bin/zsh
# no-claude 守护：CC Switch 切换供应商、Claude 自动更新等场景会重写配置，
# 这里定期把关闭开关补回去。幂等、无网络、无 sudo。
STATE="$HOME/.local/share/no-claude"
[[ -f "$STATE/disabled" ]] && exit 0
/usr/bin/python3 "$STATE/config.py" apply --quiet >/dev/null 2>&1 || true
SH
  chmod 700 "$STATE_DIR/guard.sh"

  local plist="$HOME/Library/LaunchAgents/com.local.no-claude.guard.plist"
  cat > "$plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.local.no-claude.guard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$STATE_DIR/guard.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$STATE_DIR/guard.log</string>
  <key>StandardErrorPath</key><string>$STATE_DIR/guard.log</string>
</dict>
</plist>
PL
  launchctl unload "$plist" 2>/dev/null
  launchctl load "$plist" 2>/dev/null
  rm -f "$STATE_DIR/disabled"
  echo "$(c 32 '✓') 守护已安装：com.local.no-claude.guard（每 60 秒检查一次）"
  echo "  卸载: ./no-claude.sh guard uninstall"
  echo "  暂停: touch $STATE_DIR/disabled"
}

guard_uninstall() {
  local plist="$HOME/Library/LaunchAgents/com.local.no-claude.guard.plist"
  launchctl unload "$plist" 2>/dev/null
  rm -f "$plist" "$STATE_DIR/guard.sh" "$STATE_DIR/config.py"
  rmdir "$STATE_DIR" 2>/dev/null
  echo "$(c 32 '✓') 守护已卸载"
}

guard_status() {
  # 注意：不能写 `launchctl list | grep -q ...`，grep -q 提前退出会让 launchctl
  # 收到 SIGPIPE，在 pipefail 下整条管道被判为失败。
  local out state
  out=$(launchctl list 2>/dev/null || true)
  case "$out" in
    *com.local.no-claude.guard*)
      state=$(printf '%s\n' "$out" | awk '/com.local.no-claude.guard/{print $2}')
      echo "$(c 32 '✓') 守护已加载（最近退出码 ${state:-0}）"
      if [[ -f "$STATE_DIR/disabled" ]]; then
        echo "  $(c 33 '!') 但已被 $STATE_DIR/disabled 暂停"
      fi
      ;;
    *)
      echo "$(c 2 '-') 守护未安装"
      ;;
  esac
}

# ---------------------------------------------------------------- clean

desktop_dirs() {
  local base="$HOME/Library/Application Support"
  [[ -d "$base" ]] || return 0
  for d in "$base"/Claude "$base"/Claude-*; do
    [[ -d "$d" ]] || continue
    [[ -f "$d/config.json" || -d "$d/configLibrary" ]] && printf '%s\n' "$d"
  done
}

cmd_clean() {
  local stamp
  stamp=$(date +%Y%m%d-%H%M%S)
  local n=0
  while IFS= read -r d; do
    [[ -n "$d" ]] || continue
    local s="$d/sentry"
    [[ -d "$s" ]] || continue

    # scope 文件里有设备指纹、时区、locale、用户 ID，重启后会重建，删掉即可
    for f in "$s"/scope_v*.json; do
      [[ -f "$f" ]] || continue
      if [[ $DRY_RUN -eq 1 ]]; then
        echo "  将归档 $(basename "$f")"
      else
        mv "$f" "$s/$(basename "$f").archived-$stamp"
      fi
      n=$((n + 1))
    done

    # 队列里是已经打包好、还没发出去的事件
    if [[ -d "$s/queue" ]]; then
      for f in "$s"/queue/*; do
        [[ -f "$f" ]] || continue
        [[ "$(basename "$f")" == "queue-v2.json" ]] && continue
        if [[ $DRY_RUN -eq 1 ]]; then
          echo "  将归档 $(basename "$f")"
          n=$((n + 1))
        else
          mkdir -p "$s/queue-archived-$stamp"
          mv "$f" "$s/queue-archived-$stamp/"
          n=$((n + 1))
        fi
      done
    fi
    echo "  $(c 2 '-') $(basename "$d")"
  done < <(desktop_dirs)

  if [[ $n -eq 0 ]]; then
    echo "$(c 32 '✓') 没有待回传的遥测数据"
  elif [[ $DRY_RUN -eq 1 ]]; then
    echo "$(c 36 '…') 将归档 $n 个文件（保留在 sentry/ 下，加 .archived- 后缀）"
  else
    echo "$(c 32 '✓') 已归档 $n 个文件（保留在 sentry/ 下，加 .archived-$stamp 后缀）"
  fi
}

# ---------------------------------------------------------------- verify

sentry_artifacts() {
  while IFS= read -r d; do
    [[ -n "$d" ]] || continue
    local s="$d/sentry"
    [[ -d "$s" ]] || continue
    local scope q
    scope=$(ls "$s"/scope_v*.json 2>/dev/null | head -1)
    q=$([[ -d "$s/queue" ]] && find "$s/queue" -type f ! -name 'queue-v2.json' 2>/dev/null | wc -l | tr -d ' ')
    printf '%s|%s|%s\n' "$(basename "$d")" "${scope:+yes}" "${q:-0}"
  done < <(desktop_dirs)
}

cmd_verify() {
  echo
  echo "$(c 1 '1) 开关状态')"
  python3 "$PY" status || true

  if [[ $RESTART -eq 1 ]]; then
    echo
    echo "$(c 1 '2) 重启 Claude Desktop 并观察是否生成遥测数据')"
    pkill -f "/Applications/Claude.app" 2>/dev/null
    sleep 3
    while IFS= read -r d; do
      [[ -n "$d" ]] || continue
      rm -f "$d"/sentry/scope_v*.json 2>/dev/null
    done < <(desktop_dirs)
    open -a "/Applications/Claude.app" 2>/dev/null || echo "  未找到 Claude.app"
    sleep 45
  fi

  echo
  echo "$(c 1 '遥测落盘检查')"
  local bad=0
  while IFS='|' read -r name scope q; do
    [[ -n "$name" ]] || continue
    if [[ -n "$scope" ]]; then
      echo "  $(c 31 '✗') $name: sentry scope 已生成（遥测还在跑）"
      bad=1
    elif [[ "${q:-0}" -gt 0 ]]; then
      echo "  $(c 31 '✗') $name: 队列里有 $q 个待上报事件"
      bad=1
    else
      echo "  $(c 32 '✓') $name: 无 scope、无待发队列"
    fi
  done < <(sentry_artifacts)

  echo
  if [[ $bad -eq 0 ]]; then
    echo "$(c 32 '结论：未见遥测复现。')"
    echo "如需看网络层实证，用 tools/egress-watch.py 抓一次真实外联（见 README）。"
  else
    echo "$(c 31 '结论：仍有遥测。') 把上面的路径和 ./no-claude.sh status 的输出开个 issue。"
    return 1
  fi
}

# ---------------------------------------------------------------- main

need_python

cmd="${1:-status}"
shift || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --guard)   GUARD=1 ;;
    --restart) RESTART=1 ;;
    --json)    JSON=1 ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
  shift
done

case "$cmd" in
  status)
    if [[ "${JSON:-0}" -eq 1 ]]; then
      python3 "$PY" status --json
    else
      python3 "$PY" status
    fi
    ;;
  apply)
    if [[ $DRY_RUN -eq 1 ]]; then
      python3 "$PY" apply --dry-run
      exit 0
    fi
    python3 "$PY" apply
    if [[ $GUARD -eq 1 ]]; then
      echo
      guard_install
    else
      echo
      echo "$(c 33 '提示') 如果你用 CC Switch 切换供应商，它重写配置时可能覆盖这些开关。"
      echo "     需要自动补回就执行: ./no-claude.sh guard install"
    fi
    ;;
  revert|uninstall)
    if [[ $DRY_RUN -eq 1 ]]; then
      python3 "$PY" revert --dry-run
    else
      python3 "$PY" revert
    fi
    [[ "$cmd" == "uninstall" && $DRY_RUN -eq 0 ]] && guard_uninstall
    ;;
  verify)
    cmd_verify
    ;;
  clean)
    [[ $DRY_RUN -eq 1 ]] && { echo "dry-run:"; }
    cmd_clean
    ;;
  guard)
    case "${1:-status}" in
      install)   guard_install ;;
      uninstall) guard_uninstall ;;
      status)    guard_status ;;
      *) echo "用法: ./no-claude.sh guard {install|uninstall|status}" >&2; exit 2 ;;
    esac
    ;;
  -h|--help|help) usage ;;
  *) usage; exit 2 ;;
esac
