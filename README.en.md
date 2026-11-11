# No Claude

Turn off telemetry from **Claude Code CLI** and **Claude Desktop**: crash reporting, usage analytics, feature-flag polling, and update checks.

Local configuration only. No repackaging, no traffic interception, no touching conversation data.

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

`clean` never deletes: it renames queued Sentry files with an `.archived-<timestamp>` suffix.

## Why not hosts / domain blocking

- `api.anthropic.com` is also the official API endpoint; blocking it breaks normal use when you talk to Anthropic directly.
- Desktop respects the system proxy, so domain blocking means editing Clash/mihomo rules — and mihomo's `PATCH /configs` replaces the whole rule table.
- Telemetry is app-initiated. Turning it off at the source is cleaner and survives node switches.

If you still want a network layer, block only: `*.sentry.io`, `*.ingest.us.sentry.io`, `*.statsigapi.net`, `claude.ai/api/v2/rum`, `api.anthropic.com/api/event_logging/`.

## Rollback

```bash
./no-claude.sh revert      # remove switches written by this tool
./no-claude.sh uninstall   # revert + remove guard
```

## Limitations

- **Telemetry only, not content.** Conversations still travel through your `ANTHROPIC_BASE_URL`; if that is a third-party gateway, it sees the content.
- **Versions change.** Verified against Claude Code CLI 2.1.x and Claude Desktop 5.x. If Anthropic renames a key, `verify` will fail.
- **No location spoofing.** Geolocation comes from your egress IP; that is a proxy/VPN concern. Disabling telemetry does remove timezone, locale and device fingerprints from Sentry scopes.
- **macOS first.** `tools/config.py` works standalone for the CLI on any platform; desktop paths are macOS-specific.

## License

MIT
