# No Claude

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey.svg)](#platform-support)
[![Telemetry](https://img.shields.io/badge/telemetry-off-brightgreen.svg)](#)

Turn off telemetry from **Claude Code CLI** and **Claude Desktop**: crash reporting, usage analytics, feature-flag polling, and update checks.

Local configuration only. No repackaging, no traffic interception, no touching conversation data.

> **Disclaimer**
>
> This project is solely for **protecting the user's own privacy**, on their own machine and their own account.
> It **must not** be used to harm Anthropic/Claude or any other party's legitimate interests: no bypassing billing
> or quotas, no evading abuse controls, no attacking the service, no impersonation, and no blocking traffic that the
> software legitimately needs to function. You are responsible for compliance with local law and Claude's Terms of
> Service; the authors take no responsibility for misuse.

```bash
./no-claude.sh apply --guard      # apply
./no-claude.sh verify --restart   # verify
```

## What it turns off

| Data | Channel / host | CLI | Desktop |
|---|---|---|---|
| Crashes & errors | Sentry (`*.sentry.io`) | `DISABLE_ERROR_REPORTING=1` | `disableEssentialTelemetry` |
| Usage / session metrics | Datadog RUM (`claude.ai/api/v2/rum`) | `DISABLE_TELEMETRY=1` | `disableEssentialTelemetry` |
| Non-essential traffic (model catalog, prefetch, feedback) | `api.anthropic.com`, `claude.ai` | `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | `disableNonessentialTelemetry`<br>`disableNonessentialServices` |
| Update checks | `downloads.claude.ai` | `DISABLE_AUTOUPDATER=1` | `disableAutoUpdates` |
| `/bug` uploads | `api.anthropic.com/api/claude_cli_feedback` | `DISABLE_BUG_COMMAND=1` | — |
| Global opt-out | — | `DO_NOT_TRACK=1` | — |

The key finding: on desktop, `disableEssentialTelemetry` gates **both** Sentry and Datadog RUM. Details in [docs/findings.md](docs/findings.md).

## Quick start

Requires macOS and `python3`.

```bash
git clone https://github.com/testsunny99-cpu/no-claude.git
cd no-claude

./no-claude.sh status
./no-claude.sh apply            # idempotent
./no-claude.sh clean            # archive queued-but-unsent telemetry
./no-claude.sh verify --restart # relaunch Claude and check for new telemetry
./no-claude.sh block install    # optional: add REJECT rules to Clash Verge
```

Optional guard (re-applies the switches after CC Switch rewrites configs):

```bash
./no-claude.sh apply --guard
```

## Measured before / after

Same machine, same command (`claude -p "reply with just OK"`):

| Scenario | Outbound hosts |
|---|---|
| Unprotected (empty `CLAUDE_CONFIG_DIR`) | `api.anthropic.com` ×4, `flaios.com` |
| Protected | `flaios.com` only |

Desktop was verified with a control experiment: remove the switches → `claude.ai/api/v2/rum` and `user.id` reappear in the Sentry scope; restore them → `sentry/scope_v3.json` is never recreated.

Reproduce it with the bundled logging proxy:

```bash
./tools/egress-watch.py --port 8889 --upstream 127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:8889 claude -p "say OK"
```

## Files it touches

No sudo needed. A `<file>.no-claude.bak` is kept before the first modification.

| Path | Purpose |
|---|---|
| `~/.claude/settings.json` | CLI environment variables |
| `~/.cc-switch/cc-switch.db` → `common_config_claude` | keeps switches when CC Switch changes providers |
| `~/Library/Application Support/Claude*/configLibrary/<id>.json` | the managed config layer desktop actually reads |
| `~/Library/LaunchAgents/com.local.no-claude.guard.plist` | optional guard (`--guard`) |
| `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/profiles/<rules>.yaml` | optional network layer: `prepend` in Clash Verge's rules enhancement (`block install`) |

`clean` never deletes: it renames queued Sentry files with an `.archived-<timestamp>` suffix.

## Network layer: block telemetry hosts only (optional)

```bash
./no-claude.sh block install    # idempotent
./no-claude.sh status           # shows app layer + network layer
./no-claude.sh block uninstall
```

It appends a marked `# no-claude:begin/end` block to the `prepend` list of your **Clash Verge rules enhancement file**
(backup: `.no-claude.bak`). It never touches subscriptions, proxies, groups, or `PATCH /configs` (which replaces the whole table).

Blocked (all `REJECT`): `sentry.io`, `statsigapi.net`, `statsig.anthropic.com`, `datadoghq.com`, `datadoghq.eu`.

**Not blocked:** `anthropic.com` and `claude.ai` — they are needed for normal operation, so `claude.ai/api/v2/rum` is only
turn-off-able at the app layer (verified working). Path-level rules such as `api.anthropic.com/api/event_logging/` are not
possible with domain matching.

You must click **"re-apply configuration"** once in Clash Verge for the rules to reach the core; `./no-claude.sh status`
reads `/rules` over the mihomo unix socket and tells you whether they are live. Re-run `block install` after a
subscription update if Clash Verge regenerates the enhancement file.

## Platform support

| Platform | Entry point | App layer (CLI / CC Switch / Desktop) | Network layer | Verified |
|---|---|---|---|---|
| macOS | `./no-claude.sh` | ✅ | ✅ Clash Verge rules | on real hardware |
| Windows | `platform/windows/no-claude.ps1` | ⚠️ adapted, not tested | ⚠️ writes rules only | unverified |
| Linux | `python3 tools/config.py` | ⚠️ CLI only | ⚠️ writes rules only | unverified |

Paths and design rationale: [platform/README.md](platform/README.md).

## Rollback

```bash
./no-claude.sh revert      # remove switches written by this tool
./no-claude.sh uninstall   # revert + remove network rules + remove guard
```

## Limitations

- **Telemetry only, not content.** Conversations still travel through your `ANTHROPIC_BASE_URL`; if that is a third-party gateway, it sees the content.
- **Versions change.** Verified against Claude Code CLI 2.1.x and Claude Desktop 5.x. If Anthropic renames a key, `verify` will fail.
- **No location spoofing.** Geolocation comes from your egress IP; that is a proxy/VPN concern. Disabling telemetry does remove timezone, locale and device fingerprints from Sentry scopes.
- **macOS first.** All four layers are verified on macOS. Windows has a thin wrapper (`platform/windows/no-claude.ps1`) over the same Python core, but it is **not verified on a real Windows machine** — see [platform/README.md](platform/README.md).

## License

[MIT](LICENSE) © 2026 No Claude contributors

Use it, modify it, redistribute it, ship it commercially — just keep the copyright and license notice. This project contains no Claude code or assets; it only reads and writes configuration files on your own machine.
