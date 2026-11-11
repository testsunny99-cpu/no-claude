# No Claude

关掉 **Claude Code CLI** 和 **Claude Desktop** 的遥测回传：崩溃上报、使用分析、功能开关拉取、自动更新检查。

只改本地配置文件，不重打包应用、不劫持流量、不碰对话内容。

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

`./no-claude.sh clean` 只把 `sentry/` 下的队列和 scope 文件改名归档（加 `.archived-<时间>`），**不删除**。

## 为什么不直接改 hosts / 封域名

因为那样会误伤：

- `api.anthropic.com` 同时也是官方 API 的入口。你用中转网关（`ANTHROPIC_BASE_URL`）时它才没用，直接封会连带打断正常调用。
- 桌面端和 CLI 走系统代理，域名级阻断要靠 Clash/mihomo 规则，而 mihomo 的 `PATCH /configs` 是整表替换，容易把用户现有规则清空。
- 遥测是应用自己发起的，把发起方关掉比在网络上堵更干净，也不会因为节点切换而失效。

如果你确实想加一层网络阻断，只拦这几类：`*.sentry.io`、`*.ingest.us.sentry.io`、`*.statsigapi.net`、`claude.ai/api/v2/rum`、`api.anthropic.com/api/event_logging/`。

## 回滚

```bash
./no-claude.sh revert              # 移除本工具写入的开关
./no-claude.sh uninstall           # 上面的 + 卸载守护
```

不会动你的其他配置。备份文件可按需恢复。

## 局限

- **只管遥测，不管对话内容。** 对话本身仍然会经过你配置的 `ANTHROPIC_BASE_URL`。如果那是第三方网关，网关能看到内容 —— 这不在本项目范围内。
- **版本会变。** 本项目针对 Claude Code CLI 2.1.x 与 Claude Desktop 5.x 验证过。官方改键名后 `./no-claude.sh verify` 会失败，届时提 issue。
- **不提供位置伪造。** 地理位置由出口 IP 决定，属于代理/VPN 的事，不是配置开关能解决的。遥测关掉后，Anthropic 少了 Sentry scope 里的时区、locale、设备指纹这几条线索。
- **macOS 优先。** Linux/Windows 的桌面端路径不同，`tools/config.py` 可以单独用（只处理 CLI），但桌面端未适配。

## 参考

- 逆向依据与代码定位：[docs/findings.md](docs/findings.md)
- 纯 Python 解包 Electron `app.asar`：[tools/asar-extract.py](tools/asar-extract.py)

## License

MIT
