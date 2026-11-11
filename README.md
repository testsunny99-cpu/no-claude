# No Claude

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey.svg)](#平台支持)
[![Telemetry](https://img.shields.io/badge/telemetry-off-brightgreen.svg)](#)

关掉 **Claude Code CLI** 和 **Claude Desktop** 的遥测回传：崩溃上报、使用分析、功能开关拉取、自动更新检查。

只改本地配置文件，不重打包应用、不劫持流量、不碰对话内容。

> **声明｜Disclaimer**
>
> 本项目只用于**保护使用者本人的隐私**，仅关闭我自己设备上、我自己账号产生的遥测与崩溃上报。
> **不得**用于伤害 Anthropic / Claude，或任何其他方的合法权益：不用于绕过计费与额度、不用于规避风控、
> 不用于攻击 Service、不用于伪造身份或规避服务条款，也不用于阻断开源项目本身正常工作所必需的网络请求。
> 使用前请自行确认符合你所在地法律与 Claude 的服务条款；因滥用造成的后果与作者无关。
>
> This project is solely for **protecting the user's own privacy** on their own machine and account.
> It **must not** be used to harm Anthropic/Claude or any other party's legitimate interests:
> no bypassing billing or quotas, no evading abuse controls, no attacking the service,
> no impersonation, and no blocking traffic that the software legitimately needs to function.
> You are responsible for compliance with local law and Claude's Terms of Service.

```
./no-claude.sh apply --guard     # 关闭
./no-claude.sh verify --restart  # 验证
```

---

## 它到底关了什么

Claude 官方确实提供了这些开关，但默认是开的，而且分散在三个地方：CLI 的环境变量、桌面端的托管配置、以及 CC Switch 这类工具会重写配置文件。

| 数据 | 通道 / 域名 | CLI 开关 | 桌面端开关 |
|---|---|---|---|
| 崩溃与异常上报 | Sentry `*.sentry.io`、`o1158394.ingest.us.sentry.io` | `DISABLE_ERROR_REPORTING=1` | `disableEssentialTelemetry` |
| 使用与会话指标 | Datadog RUM `claude.ai/api/v2/rum` | `DISABLE_TELEMETRY=1` | `disableEssentialTelemetry` |
| 非必要流量（模型目录轮询、图标/词典预取、反馈） | `api.anthropic.com`、`claude.ai` | `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | `disableNonessentialTelemetry`<br>`disableNonessentialServices` |
| 自动更新检查与下载 | `downloads.claude.ai` | `DISABLE_AUTOUPDATER=1` | `disableAutoUpdates` |
| `/bug` 反馈上传 | `api.anthropic.com/api/claude_cli_feedback` | `DISABLE_BUG_COMMAND=1` | — |
| 全局"别追踪我" | — | `DO_NOT_TRACK=1` | — |

桌面端的 `disableEssentialTelemetry` 一个开关同时管 Sentry 和 Datadog RUM —— 这是本项目最关键的发现，见 [docs/findings.md](docs/findings.md)。

## 快速开始

要求：macOS + `python3`（系统自带；没有就 `xcode-select --install`）。

```bash
git clone https://github.com/testsunny99-cpu/no-claude.git
cd no-claude

./no-claude.sh status          # 先看哪些还开着
./no-claude.sh apply           # 写入开关（幂等，可反复执行）
./no-claude.sh clean           # 归档已排队但还没发出去的遥测数据
./no-claude.sh verify --restart  # 重启 Claude 并检查是否还在生成遥测
./no-claude.sh block install     # 可选：把遥测域名写进 Clash Verge 的 REJECT 规则
```

想让开关在 CC Switch 切换供应商后自动补回：

```bash
./no-claude.sh apply --guard   # 安装 launchd 守护，每 60 秒检查一次
```

## 实测结果

同一台机器、同一个命令（`claude -p "reply with just OK"`），只差配置：

| 场景 | 实际外联域名 |
|---|---|
| 未加固（空 `CLAUDE_CONFIG_DIR`） | `api.anthropic.com` ×4、`flaios.com` |
| 已加固（本项目写入的 `settings.json`） | `flaios.com` |

桌面端做的是对照实验：删掉开关 → `claude.ai/api/v2/rum` 请求和 `user.id` 重新出现在 Sentry scope 里；加回开关 → 连 `sentry/scope_v3.json` 都不再创建。

复现方法（不需要抓包工具，本仓库自带一个只记日志的 CONNECT 代理）：

```bash
./tools/egress-watch.py --port 8889 --upstream 127.0.0.1:7897

# 另一个终端
HTTPS_PROXY=http://127.0.0.1:8889 claude -p "say OK"
```

## 会改哪些文件

全部在用户目录下，不需要 sudo。首次修改前会留 `<文件名>.no-claude.bak`。

| 路径 | 作用 |
|---|---|
| `~/.claude/settings.json` | CLI 的环境变量 |
| `~/.cc-switch/cc-switch.db` → `common_config_claude` | 让 CC Switch 切换供应商时不丢开关 |
| `~/Library/Application Support/Claude*/configLibrary/<id>.json` | 桌面端真正读取的托管配置层 |
| `~/Library/LaunchAgents/com.local.no-claude.guard.plist` | 可选守护（`--guard`） |
| `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/profiles/<rules>.yaml` | 可选网络层：Clash Verge 规则增强文件的 `prepend`（`block install`） |

`./no-claude.sh clean` 只把 `sentry/` 下的队列和 scope 文件改名归档（加 `.archived-<时间>`），**不删除**。

## 网络层：只封遥测域名（可选）

应用层关掉开关最干净，但官方可能改键名。想再加一道网，用：

```bash
./no-claude.sh block install    # 写入 Clash Verge 规则，幂等
./no-claude.sh status           # 同时显示应用层和网络层
./no-claude.sh block uninstall  # 移除
```

它只往 **Clash Verge 的“规则”增强文件** 的 `prepend` 里加一段带 `# no-claude:begin/end` 标记的规则（改前留 `.no-claude.bak`），
不碰你的订阅、节点、策略组，也不用 sudo，更不会调用 mihomo 的 `PATCH /configs`（那是整表替换，容易清空现有规则）。

封的是这 5 条，全部直接 `REJECT`：

```
DOMAIN-SUFFIX,sentry.io,REJECT
DOMAIN-SUFFIX,statsigapi.net,REJECT
DOMAIN-SUFFIX,statsig.anthropic.com,REJECT
DOMAIN-SUFFIX,datadoghq.com,REJECT
DOMAIN-SUFFIX,datadoghq.eu,REJECT
```

**不封** `anthropic.com` 和 `claude.ai` —— 它们是正常业务域名，封了会打断调用。所以 `claude.ai/api/v2/rum` 这条 RUM 上报只能靠应用层开关关掉（本项目已验证有效）。
同理 `api.anthropic.com/api/event_logging/` 是路径级接口，mihomo 只能按域名匹配，做不到只封路径。

写完后规则还没进内核，需要你在 Clash Verge 里点一次 **“重新应用配置”**（或点一下当前订阅）。
`./no-claude.sh status` 会通过 mihomo 的 unix socket 读 `/rules`，告诉你规则到底加载没有。

注意：换订阅/更新订阅后，如果 Clash Verge 重建了规则增强文件，重新执行 `block install` 即可。

## 平台支持

| 平台 | 入口 | 应用层（CLI / CC Switch / 桌面端） | 网络层 | 验证状态 |
|---|---|---|---|---|
| macOS | `./no-claude.sh` | ✅ | ✅ Clash Verge 规则 | 实机验证 |
| Windows | `platform/windows/no-claude.ps1` | ⚠️ 已适配，未实机验证 | ⚠️ 只写规则文件 | 未验证 |
| Linux | `python3 tools/config.py` | ⚠️ 仅 CLI | ⚠️ 只写规则文件 | 未验证 |

路径对照与设计取舍见 [platform/README.md](platform/README.md)。

## 回滚

```bash
./no-claude.sh revert              # 移除本工具写入的开关
./no-claude.sh uninstall           # 上面的 + 移除网络层规则 + 卸载守护
```

不会动你的其他配置。备份文件可按需恢复。

## 局限

- **只管遥测，不管对话内容。** 对话本身仍然会经过你配置的 `ANTHROPIC_BASE_URL`。如果那是第三方网关，网关能看到内容 —— 这不在本项目范围内。
- **版本会变。** 本项目针对 Claude Code CLI 2.1.x 与 Claude Desktop 5.x 验证过。官方改键名后 `./no-claude.sh verify` 会失败，届时提 issue。
- **不提供位置伪造。** 地理位置由出口 IP 决定，属于代理/VPN 的事，不是配置开关能解决的。遥测关掉后，Anthropic 少了 Sentry scope 里的时区、locale、设备指纹这几条线索。
- **macOS 优先。** macOS 上四层（CLI、CC Switch、桌面端、Clash Verge）都验证过；Windows 有薄封装 `platform/windows/no-claude.ps1`，复用同一份 Python 核心，但**未在 Windows 实机验证**，见 [platform/README.md](platform/README.md)。

## 参考

- 逆向依据与代码定位：[docs/findings.md](docs/findings.md)
- 纯 Python 解包 Electron `app.asar`：[tools/asar-extract.py](tools/asar-extract.py)

## License

[MIT](LICENSE) © 2026 No Claude contributors

可自由使用、修改、分发、商用，保留版权与许可声明即可。本项目不含任何 Claude 的代码或资源，只在本地读写你自己机器上的配置文件。
