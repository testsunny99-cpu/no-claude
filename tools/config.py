#!/usr/bin/env python3
"""no-claude — 关闭 Claude Code CLI / Claude Desktop 的遥测回传。

只做一件事：把官方提供的遥测开关写到官方会去读的位置，然后报告状态。
不做网络劫持，不改二进制，不碰对话内容。

用法:
    config.py status  [--json]
    config.py apply   [--dry-run] [--cli] [--desktop] [--ccswitch] [--quiet]
    config.py revert  [--dry-run] [--cli] [--desktop] [--ccswitch] [--quiet]

默认三个目标全部处理，若不存在则自动跳过。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

VERSION = "0.2.0"

# ---------------------------------------------------------------------------
# 要写入的开关
# ---------------------------------------------------------------------------

# Claude Code CLI: ~/.claude/settings.json 的 env 段
CLI_ENV = {
    # 一条顶全部：关掉所有非必要流量（自动更新、模型目录轮询、反馈、遥测）
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    # 显式关闭遥测与错误上报，覆盖没有读上一条的旧版本
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_BUG_COMMAND": "1",
    "DO_NOT_TRACK": "1",
}

# Claude Desktop: 托管配置的平铺键名（configLibrary 或 claude_desktop_config.json）
DESKTOP_KEYS = {
    # Sentry 崩溃上报 + Datadog RUM，同一个开关
    "disableEssentialTelemetry": True,
    # 使用分析（不含对话内容）
    "disableNonessentialTelemetry": True,
    # 预取连接器图标、预览帧、词典等
    "disableNonessentialServices": True,
    "disableAutoUpdates": True,
}

# CC Switch 里 claude 的公共配置片段，避免切换供应商时覆盖 ~/.claude/settings.json
def ccswitch_db() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return base / "cc-switch" / "cc-switch.db"
    return home / ".cc-switch" / "cc-switch.db"
CCSWITCH_KEY = "common_config_claude"

CLAUDE_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
CLI_SETTINGS = CLAUDE_CONFIG_DIR / "settings.json"

def support_roots() -> list[Path]:
    """各平台存放 Claude 桌面端数据的根目录。"""
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support"]
    if sys.platform == "win32":
        roots = []
        if os.environ.get("APPDATA"):
            roots.append(Path(os.environ["APPDATA"]))
        roots.append(home / "AppData" / "Roaming")
        return roots
    return [home / ".config", home / ".local" / "share"]

QUIET = False


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def say(msg: str = "") -> None:
    if not QUIET:
        print(msg)


def head(msg: str) -> None:
    if not QUIET:
        print(f"\n\033[1m{msg}\033[0m")


def ok(msg: str) -> None:
    if not QUIET:
        print(f"  \033[32m✓\033[0m {msg}")


def changed(msg: str) -> None:
    if not QUIET:
        print(f"  \033[36m+\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}", file=sys.stderr)


def skip(msg: str) -> None:
    if not QUIET:
        print(f"  \033[2m-\033[0m {msg}")


# ---------------------------------------------------------------------------
# 通用 JSON 读写
# ---------------------------------------------------------------------------

def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def backup_once(path: Path) -> Path | None:
    """首次修改前留一份 .no-claude.bak，之后不再覆盖。"""
    bak = path.with_name(path.name + ".no-claude.bak")
    if bak.exists():
        return None
    shutil.copy2(path, bak)
    return bak


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.chmod(tmp, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Claude Code CLI
# ---------------------------------------------------------------------------

def cli_state() -> dict:
    """返回 {'path':..., 'present':bool, 'off':bool, 'keys':{k:值}}"""
    st = {"path": str(CLI_SETTINGS), "present": CLI_SETTINGS.exists(), "off": False, "keys": {}}
    if not st["present"]:
        return st
    try:
        data = read_json(CLI_SETTINGS)
    except Exception as e:  # 配置坏了也要如实报告，不能假装成功
        st["error"] = f"无法解析: {e}"
        return st
    env = data.get("env") or {}
    st["keys"] = {k: env.get(k) for k in CLI_ENV}
    st["off"] = all(str(env.get(k, "")) == "1" for k in CLI_ENV)
    return st


def cli_apply(dry_run: bool) -> None:
    head("Claude Code CLI")
    if not CLI_SETTINGS.exists():
        skip(f"{CLI_SETTINGS} 不存在（未安装或未登录过），跳过")
        return
    try:
        data = read_json(CLI_SETTINGS)
    except Exception as e:
        warn(f"无法解析 {CLI_SETTINGS}: {e}")
        return

    env = data.setdefault("env", {})
    todo = {k: v for k, v in CLI_ENV.items() if str(env.get(k, "")) != v}

    if not todo:
        ok(f"已全部关闭（{CLI_SETTINGS}）")
        return

    for k, v in todo.items():
        print(f"  \033[36m+\033[0m {k} = {v}" if not QUIET else "", end="\n" if not QUIET else "")

    if dry_run:
        warn("dry-run，未写入")
        return

    env.update(CLI_ENV)
    write_json(CLI_SETTINGS, data)
    say(f"  \033[32m✓\033[0m 已写入 {CLI_SETTINGS}")


def cli_revert(dry_run: bool) -> None:
    if not CLI_SETTINGS.exists():
        return
    try:
        data = read_json(CLI_SETTINGS)
    except Exception:
        return
    env = data.get("env") or {}
    removed = [k for k in CLI_ENV if k in env]
    if not removed:
        skip("CLI: 无本工具写入的键")
        return
    for k in removed:
        del env[k]
    if dry_run:
        warn(f"dry-run，将移除 {', '.join(removed)}")
        return
    write_json(CLI_SETTINGS, data)
    ok(f"CLI: 已移除 {', '.join(removed)}")


# ---------------------------------------------------------------------------
# CC Switch（可选，避免切换供应商时被覆盖）
# ---------------------------------------------------------------------------

def ccswitch_state() -> dict:
    db = ccswitch_db()
    st = {"path": str(db), "present": db.exists(), "off": False, "keys": {}}
    if not st["present"]:
        return st
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute("select value from settings where key=?", (CCSWITCH_KEY,)).fetchone()
        con.close()
    except Exception as e:
        st["error"] = str(e)
        return st
    if not row:
        st["present"] = False
        return st
    try:
        cfg = json.loads(row[0])
    except Exception:
        return st
    env = cfg.get("env") or {}
    st["keys"] = {k: env.get(k) for k in CLI_ENV}
    st["off"] = all(str(env.get(k, "")) == "1" for k in CLI_ENV)
    return st


def ccswitch_apply(dry_run: bool) -> None:
    head("CC Switch 公共配置（可选）")
    db = ccswitch_db()
    if not db.exists():
        skip("未安装 CC Switch，跳过")
        return
    try:
        con = sqlite3.connect(db)
        row = con.execute("select value from settings where key=?", (CCSWITCH_KEY,)).fetchone()
        if not row:
            con.close()
            skip(f"没有 {CCSWITCH_KEY}，跳过")
            return
        cfg = json.loads(row[0])
        env = cfg.setdefault("env", {})
        todo = {k: v for k, v in CLI_ENV.items() if str(env.get(k, "")) != v}
        if not todo:
            con.close()
            ok("已全部关闭")
            return
        env.update(CLI_ENV)
        if dry_run:
            con.close()
            warn(f"dry-run，将写入 {', '.join(todo)}")
            return
        con.execute(
            "update settings set value=? where key=?",
            (json.dumps(cfg, ensure_ascii=False, indent=2), CCSWITCH_KEY),
        )
        con.commit()
        con.close()
        ok(f"已写入 {len(todo)} 个键（切换供应商不再丢失）")
    except Exception as e:
        warn(f"处理失败，跳过: {e}")


def ccswitch_revert(dry_run: bool) -> None:
    db = ccswitch_db()
    if not db.exists():
        return
    try:
        con = sqlite3.connect(db)
        row = con.execute("select value from settings where key=?", (CCSWITCH_KEY,)).fetchone()
        if not row:
            con.close()
            return
        cfg = json.loads(row[0])
        env = cfg.get("env") or {}
        removed = [k for k in CLI_ENV if k in env]
        if not removed:
            con.close()
            return
        for k in removed:
            del env[k]
        if dry_run:
            con.close()
            warn(f"dry-run，CC Switch 将移除 {', '.join(removed)}")
            return
        con.execute(
            "update settings set value=? where key=?",
            (json.dumps(cfg, ensure_ascii=False, indent=2), CCSWITCH_KEY),
        )
        con.commit()
        con.close()
        ok(f"CC Switch: 已移除 {', '.join(removed)}")
    except Exception as e:
        warn(f"回滚失败: {e}")


# ---------------------------------------------------------------------------
# Claude Desktop
# ---------------------------------------------------------------------------

def desktop_dirs() -> list[Path]:
    """Claude / Claude-3p / Claude-<mode> 都可能存在，只保留看起来像数据目录的。"""
    out: list[Path] = []
    seen: set[Path] = set()
    for root in support_roots():
        if not root.exists():
            continue
        for p in sorted(root.iterdir()):
            if not p.is_dir() or not p.name.startswith("Claude"):
                continue
            if p.name != "Claude" and not p.name.startswith("Claude-"):
                continue
            if p in seen:
                continue
            if (p / "config.json").exists() or (p / "configLibrary").is_dir():
                out.append(p)
                seen.add(p)
    return out


def desktop_targets(d: Path) -> list[Path]:
    """返回该数据目录下需要打补丁的 JSON 文件。

    桌面端有几层配置，优先级从高到低：

      1. configLibrary/<appliedId>.json  — 托管配置层，App 真正读取的地方，
         CC Switch 也写这里。写了有效。
      2. claude_desktop_config.json      — 旧版本的本地层。新版本会把这里的
         未知键清掉，只在没有第 1 层时才值得写。

    所以有第 1 层就只写第 1 层，避免写出"看起来改了其实没用"的假象。
    """
    lib = d / "configLibrary"
    meta = lib / "_meta.json"
    if meta.exists():
        try:
            applied = read_json(meta).get("appliedId")
        except Exception:
            applied = None
        if applied:
            f = lib / f"{applied}.json"
            if f.exists():
                return [f]

    legacy = d / "claude_desktop_config.json"
    return [legacy] if legacy.exists() else []


def desktop_state() -> list[dict]:
    out = []
    for d in desktop_dirs():
        for f in desktop_targets(d):
            st = {"path": str(f), "present": True, "off": False, "keys": {}}
            try:
                data = read_json(f)
            except Exception as e:
                st["error"] = f"无法解析: {e}"
                out.append(st)
                continue
            st["keys"] = {k: data.get(k) for k in DESKTOP_KEYS}
            st["off"] = all(data.get(k) is v for k, v in DESKTOP_KEYS.items())
            out.append(st)
    # 生效层排前面：configLibrary 优先展示
    out.sort(key=lambda s: ("configLibrary" not in s["path"], s["path"]))
    return out


def desktop_apply(dry_run: bool) -> None:
    head("Claude Desktop")
    dirs = desktop_dirs()
    if not dirs:
        skip("未找到 Claude Desktop 数据目录，跳过")
        return

    for d in dirs:
        files = desktop_targets(d)
        if not files:
            skip(f"{d.name}: 没有可写的配置文件")
            continue
        for f in files:
            try:
                data = read_json(f)
            except Exception as e:
                warn(f"{f}: 无法解析，跳过（{e}）")
                continue
            todo = {k: v for k, v in DESKTOP_KEYS.items() if data.get(k) is not v}
            label = str(f).replace(str(Path.home()), "~")
            if not todo:
                ok(f"已全部关闭  {label}")
                continue
            if dry_run:
                warn(f"dry-run，将在 {label} 写入 {len(todo)} 个键")
                continue
            bak = backup_once(f)
            data.update(DESKTOP_KEYS)
            write_json(f, data)
            note = f"（备份 {bak.name}）" if bak else ""
            ok(f"已写入 {len(todo)} 个键  {label} {note}")


def desktop_revert(dry_run: bool) -> None:
    for d in desktop_dirs():
        for f in desktop_targets(d):
            try:
                data = read_json(f)
            except Exception:
                continue
            removed = [k for k in DESKTOP_KEYS if k in data]
            if not removed:
                continue
            for k in removed:
                del data[k]
            if dry_run:
                warn(f"dry-run，将从 {f.name} 移除 {', '.join(removed)}")
                continue
            write_json(f, data)
            ok(f"Desktop: 从 {f.name} 移除 {', '.join(removed)}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(as_json: bool) -> int:
    cli = cli_state()
    ccs = ccswitch_state()
    desk = desktop_state()

    if as_json:
        print(json.dumps({"version": VERSION, "cli": cli, "ccswitch": ccs, "desktop": desk},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"no-claude {VERSION}\n")

    def line(name: str, st: dict, label: str) -> None:
        if st.get("error"):
            print(f"  \033[33m!\033[0m {name:<10} {label} — {st['error']}")
        elif not st.get("present"):
            print(f"  \033[2m-\033[0m {name:<10} {label} — 未安装")
        elif st.get("off"):
            print(f"  \033[32m✓\033[0m {name:<10} {label} — 已关闭")
        else:
            missing = [k for k, v in st.get("keys", {}).items() if str(v) != "1" and v is not True]
            print(f"  \033[31m✗\033[0m {name:<10} {label} — 未关闭（缺 {', '.join(missing[:3])}{'…' if len(missing) > 3 else ''}）")

    line("CLI", cli, "~/.claude/settings.json")
    line("CC Switch", ccs, "common_config_claude")
    for st in desk:
        p = st["path"].replace(str(Path.home()), "~")
        label = p.split("/Application Support/")[-1] if "/Application Support/" in p else p
        line("Desktop", st, label)

    targets = [cli, ccs, *desk]
    done = sum(1 for t in targets if t.get("present") and t.get("off"))
    total = sum(1 for t in targets if t.get("present"))
    print()
    if total == 0:
        print("未发现任何 Claude 安装。")
        return 1
    if done == total:
        print(f"\033[32m全部 {total} 个目标已关闭。\033[0m 重启 Claude 后生效。")
        return 0
    print(f"\033[31m{done}/{total} 已关闭。\033[0m 运行 ./no-claude.sh apply")
    return 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    global QUIET

    ap = argparse.ArgumentParser(prog="config.py", description="关闭 Claude 遥测回传")
    ap.add_argument("command", choices=["status", "apply", "revert"])
    ap.add_argument("--dry-run", action="store_true", help="只报告将做什么，不写文件")
    ap.add_argument("--json", action="store_true", help="status 以 JSON 输出")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--cli", action="store_true", help="只处理 CLI")
    ap.add_argument("--desktop", action="store_true", help="只处理 Desktop")
    ap.add_argument("--ccswitch", action="store_true", help="只处理 CC Switch")
    args = ap.parse_args()
    QUIET = args.quiet

    explicit = args.cli or args.desktop or args.ccswitch
    do_cli = args.cli or not explicit
    do_desk = args.desktop or not explicit
    do_ccs = args.ccswitch or not explicit

    if args.command == "status":
        return cmd_status(args.json)

    if args.command == "apply":
        if do_cli:
            cli_apply(args.dry_run)
        if do_ccs:
            ccswitch_apply(args.dry_run)
        if do_desk:
            desktop_apply(args.dry_run)
        say()
        return cmd_status(False) if not args.dry_run else 0

    if args.command == "revert":
        head("回滚")
        if do_cli:
            cli_revert(args.dry_run)
        if do_ccs:
            ccswitch_revert(args.dry_run)
        if do_desk:
            desktop_revert(args.dry_run)
        say()
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
