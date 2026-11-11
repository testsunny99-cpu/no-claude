# Windows 适配（未实机验证）

> ⚠️ **这份脚本没有在真实 Windows 机器上跑过。**
> 逻辑复用 macOS 上已验证过的 `tools/config.py` / `tools/block.py`，
> Windows 专属的部分（路径、计划任务、PowerShell 包裹）只做过代码审查。
> 跑出问题请开 issue 并附 `powershell -File .\no-claude.ps1 status` 的输出。

## 前提

- Windows 10 / 11
- Python 3.9+：<https://www.python.org/downloads/windows/>（安装时勾选 **Add python.exe to PATH**）
- 已安装 Claude Desktop（要改桌面端开关）和/或 CC Switch（要改 CC Switch 的数据库）
- 可选：Clash Verge Rev（要加网络层规则）

## 用法

```powershell
git clone https://github.com/testsunny99-cpu/no-claude.git
cd no-claude

powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 status
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 apply
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 clean
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 block install
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 apply -Guard
```

`-DryRun` 只打印将要做什么，不写文件。

## 和 macOS 的差异

| 项目 | macOS | Windows |
|---|---|---|
| 守护 | launchd，每 60 秒 | 计划任务 `no-claude-guard`，每 1 分钟 |
| 守护暂停 | `touch ~/.local/share/no-claude/disabled` | 新建 `%LOCALAPPDATA%\no-claude\disabled` |
| 桌面端重启 | `pkill` + `open -a Claude` | 未实现 `verify --restart`，请手动退出并重开 Claude |
| 内核规则检查 | 读 mihomo unix socket `/rules` | 未实现（Windows 版 mihomo 走 TCP + secret），只报告"已写入规则文件" |

## 已知风险

1. **`claude_desktop_config.json` 可能不是生效层。** macOS 上实测桌面端真正读的是
   `configLibrary/<appliedId>.json`，新版会清掉 `claude_desktop_config.json` 里的未知键。
   Windows 版目录结构一致，`config.py` 会同时写两层，但哪层生效**未验证**。
2. **CC Switch 占用数据库。** 运行中的 CC Switch 会用内存状态覆盖文件。改之前先退出 CC Switch，
   改完再打开。
3. **脚本签名。** 用 `-ExecutionPolicy Bypass` 绕过的只是本次进程的策略，系统策略没变。
4. **网络层仍然需要手动点一次“重新应用配置”**，和 macOS 一样。

## 回滚

```powershell
powershell -ExecutionPolicy Bypass -File .\platform\windows\no-claude.ps1 uninstall
```

会移除开关、移除写入 Clash Verge 的规则、删除计划任务。`.no-claude.bak` 备份文件保留，可手动还原。
