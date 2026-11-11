# 跨平台说明 / Platform notes

主力且完整验证的平台是 **macOS**。这里放的是其他平台的适配层。

| 平台 | 入口 | 应用层（CLI / CC Switch / 桌面端） | 网络层 | 验证状态 |
|---|---|---|---|---|
| macOS | `./no-claude.sh` | ✅ | ✅ Clash Verge 规则 | 实机验证 |
| Windows | `platform/windows/no-claude.ps1` | ⚠️ 代码已适配，未实机验证 | ⚠️ 仅写规则文件 | 未验证 |
| Linux | `python3 tools/config.py` | ⚠️ 仅 CLI | ⚠️ 仅写规则文件 | 未验证 |

## 设计取舍

所有业务逻辑都在 `tools/config.py`（应用层）和 `tools/block.py`（网络层）里，按 `sys.platform` 选路径。

各平台的入口脚本只做三件事：找 `python3`、转发参数、装/卸守护。这样：

- Windows 不需要维护第二套判断逻辑，也就不会出现"Mac 修了、Windows 还错着"。
- 想加 Linux/systemd 适配时，只写一个薄封装即可。

## 各平台路径

| 用途 | macOS | Windows | Linux |
|---|---|---|---|
| Claude 桌面端数据 | `~/Library/Application Support/Claude*` | `%APPDATA%\Claude*` | `~/.config/Claude*` |
| CC Switch 数据库 | `~/.cc-switch/cc-switch.db` | `%APPDATA%\cc-switch\cc-switch.db` | `~/.cc-switch/cc-switch.db` |
| Clash Verge 配置 | `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev` | `%APPDATA%\io.github.clash-verge-rev.clash-verge-rev` | `~/.config/io.github.clash-verge-rev.clash-verge-rev` |
| 守护 | launchd（`~/Library/LaunchAgents`） | 计划任务（`schtasks`） | 未实现 |

## Windows 快速用法

```powershell
git clone https://github.com/testsunny99-cpu/no-claude.git
cd no-claude
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 status
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 apply
```

需要 Python 3.9+（[python.org](https://www.python.org/downloads/windows/) 安装时勾选 *Add python.exe to PATH*）。

细节和已知风险见 [windows/README.md](windows/README.md)。
