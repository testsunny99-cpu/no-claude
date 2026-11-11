# 逆向依据

本文记录"为什么是这些开关"，方便版本变化时重新定位。

所有结论都来自读取本地安装的应用包（`app.asar`）与实际抓包，不涉及任何服务端行为推测。

## 1. CLI：开关就在环境变量里

`claude` 二进制里能直接找到这些字符串，并且在运行时按以下优先级决定遥测模式：

```js
function cu() {
  return process.env.CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
    ? "essential-traffic"
    : process.env.DISABLE_TELEMETRY || DO_NOT_TRACK
      ? "no-telemetry"
      : "default";
}
```

也就是说：

- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` 是第一优先级，设为任意非空值即关掉全部非必要流量。
- `DISABLE_TELEMETRY` / `DO_NOT_TRACK` 是第二优先级，只关遥测。
- 其余 `DISABLE_ERROR_REPORTING`、`DISABLE_AUTOUPDATER`、`DISABLE_BUG_COMMAND` 各自作用在不同子系统。

实测（空 `CLAUDE_CONFIG_DIR`，只有 API 变量）：

```
CONNECT  api.anthropic.com:443   ×4
CONNECT  flaios.com:443
```

加上 `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 后：

```
CONNECT  flaios.com:443
```

## 2. 桌面端：三层配置，只有一层算数

Claude Desktop 的配置按优先级分三层：

1. **`configLibrary/<appliedId>.json`** —— 托管配置层（managed config）。
   - `_meta.json` 里记录 `appliedId`，指向真正生效的那个 JSON。
   - CC Switch 写的就是这里。
2. **`claude_desktop_config.json`** —— 旧的本地层。
   - 新版应用启动后会把不认识的键清掉，所以往这里写等于白写。
3. MDM plist（`/Library/Managed Preferences/...`）—— 企业环境，需要管理员权限。

启动参数里可以直接看到最终生效的托管配置，验证时很有用：

```
--desktop-managed-config={"disableEssentialTelemetry":true,
                          "disableNonessentialTelemetry":true,
                          "disableAutoUpdates":true,
                          "deploymentMode":"3p", ...}
```

## 3. 关键发现：一个开关管两条上报通道

桌面端配置 schema 里，Sentry 与 Datadog RUM 端点共用同一个 toggle：

```js
sentry: {
  toggleKey: "disableEssentialTelemetry",
  endpoints: [
    { host: "*.sentry.io", firewallAlso: ["*.ingest.us.sentry.io"] },
    { host: "sentry.io", ... }
  ]
},
rum: {
  toggleKey: "disableEssentialTelemetry",
  endpoints: [{ host: anthropicHost, path: "/api/v2/rum", exactPath: true, ... }]
}
```

读取逻辑是平铺键名：

```js
case "disableEssentialTelemetry":
  return (e.telemetry?.disableEssential ?? e.disableEssentialTelemetry) === true;
```

所以写顶层键 `disableEssentialTelemetry: true` 就够了，不需要构造嵌套的 `telemetry` 对象。

## 4. 桌面端的"自证"信号：Sentry scope 文件

关掉遥测后，应用**不再创建** `sentry/scope_v3.json`。这是最可靠的本地判据，比看日志和抓包都直接。

该文件里有什么（用于说明为什么值得关）：

- `user.id`、`organization_id`
- 设备内存、CPU 型号、屏幕分辨率
- `{"locale":"en-US","timezone":"America/Los_Angeles"}`
- 最近 100 条请求 URL（含 `claude.ai/api/v2/rum`）

## 5. 对照实验

| 步骤 | `sentry/scope_v3.json` | 记录的 RUM 请求 |
|---|---|---|
| 删除开关后启动 | 创建（15.6 KB） | `https://claude.ai/api/v2/rum`，带 `user.id` |
| 加回开关后启动 | 不创建 | 无 |

## 6. CC Switch 会覆盖配置

CC Switch 把 claude 的公共 env 存在 SQLite 的 `settings.common_config_claude`，并在启动/切换供应商时把它合并进 `~/.claude/settings.json`。

两个副作用：

1. 直接改 `settings.json` 会被运行中的 CC Switch 用它内存里的旧状态覆盖回去（约 1 秒内）。
2. 只改 DB 不改文件、或只改文件不改 DB，都会在下次切换时丢掉一半。

所以本项目**两处都写**，并提供 `--guard` 让 launchd 定期校正。

## 7. 重新定位开关的方法

升级后开关失效时：

```bash
python3 tools/asar-extract.py \
  /Applications/Claude.app/Contents/Resources/app.asar /tmp/claude_asar

grep -ao 'disableEssentialTelemetry\|disableNonessential[A-Za-z]*\|disableAutoUpdates' \
  /tmp/claude_asar/.vite/build/*.js | sort -u

grep -ao 'CLAUDE_[A-Z_]*TELEMETRY[A-Z_]*\|DISABLE_[A-Z_]*' \
  /tmp/claude_asar/.vite/build/*.js | sort -u
```

CLI 侧：

```bash
strings -a "$(readlink -f /opt/homebrew/bin/claude)" |
  grep -oE 'DISABLE_[A-Z_]+|CLAUDE_CODE_[A-Z_]*TELEMETRY[A-Z_]*' | sort -u
```

组合键名 `disable` + 分类名，通常能直接命中新的键。

## 8. 测试环境

| 组件 | 版本 |
|---|---|
| Claude Code CLI | 2.1.285 |
| Claude Desktop | 5.1231.2（Electron / macOS，`3p` 部署模式）|
| macOS | 27.0 (Apple Silicon) |
| 代理 | Clash Verge (mihomo 1.19.x, mixed-port 7897) |
