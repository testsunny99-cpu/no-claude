#!/usr/bin/env python3
"""no-claude 网络层：把遥测域名写进 Clash Verge 的规则增强文件（只 REJECT 遥测域）。

只做三件事：
  1. 找到 Clash Verge 的 profiles.yaml，顺着 current -> option.rules 找到增强文件
  2. 在该文件的 prepend 里加一段带标记的 REJECT 规则（幂等增删）
  3. 通过 mihomo 的 unix socket 读 /rules，判断规则是否真的加载了

不改 mihomo 的内核配置，不动用户的代理节点、策略组和远程订阅。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from pathlib import Path

VERSION = "0.1.0"

# 只封遥测。anthropic.com / claude.ai 是业务域名，不在这里。
RULES = [
    "DOMAIN-SUFFIX,sentry.io,REJECT",
    "DOMAIN-SUFFIX,statsigapi.net,REJECT",
    "DOMAIN-SUFFIX,statsig.anthropic.com,REJECT",
    "DOMAIN-SUFFIX,datadoghq.com,REJECT",
    "DOMAIN-SUFFIX,datadoghq.eu,REJECT",
]

BEGIN = "# no-claude:begin"
END = "# no-claude:end"

APP_IDS = [
    "io.github.clash-verge-rev.clash-verge-rev",
    "io.github.clash-verge-rev.clash-verge",
    "clash-verge",
]


# ------------------------------------------------------------------ locate

def verge_roots() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        base = [home / "Library" / "Application Support"]
    elif sys.platform == "win32":
        base = []
        appdata = os.environ.get("APPDATA")
        if appdata:
            base.append(Path(appdata))
        base.append(home / "AppData" / "Roaming")
        base.append(home / "AppData" / "Local")
    else:
        base = [
            Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")),
            home / ".local" / "share",
        ]
    out = []
    for b in base:
        for app in APP_IDS:
            p = b / app
            if p.is_dir() and p not in out:
                out.append(p)
    return out


def verge_root() -> Path | None:
    for p in verge_roots():
        if (p / "profiles.yaml").is_file():
            return p
    return None


def parse_profiles(text: str) -> tuple[str | None, dict[str, dict]]:
    """行解析 profiles.yaml：拿 current 和每个 item 的 type/file/option.rules。

    profiles.yaml 是 Clash Verge 自己生成的，结构固定，不值得为一件事引入 PyYAML。
    """
    current = None
    items: dict[str, dict] = {}
    cur_uid = None
    section = None

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "-", "\t")):
            key, _, val = raw.partition(":")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key == "current":
                current = val or None
            section = key
            cur_uid = None
            continue
        if section != "items":
            continue

        line = raw.strip()
        if line.startswith("- "):
            # 新的 item，形如 "- uid: xxx"
            line = line[2:].strip()
            if line.startswith("uid:"):
                uid = line.split(":", 1)[1].strip().strip("'\"")
                cur_uid = uid
                items[uid] = {"uid": uid}
            else:
                cur_uid = None
            # 同一行可能还有别的键，忽略（Clash Verge 不会这么写）
            continue
        if cur_uid is None:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key == "rules":
            items[cur_uid]["rules"] = val
        elif key == "file":
            items[cur_uid]["file"] = val
        elif key == "type":
            items[cur_uid]["type"] = val

    return current, items


def target_file(root: Path) -> tuple[Path | None, str]:
    pf = root / "profiles.yaml"
    current, items = parse_profiles(pf.read_text(encoding="utf-8"))

    if not current:
        return None, "profiles.yaml 里没有 current 配置"
    cur = items.get(current)
    if not cur:
        return None, f"找不到当前配置 {current}"

    rules_uid = cur.get("rules")
    if not rules_uid:
        return None, (
            f"当前配置 {current} 没有规则增强文件。\n"
            "  打开 Clash Verge → 订阅 → 右键当前配置 → 编辑规则，保存一次后再执行本命令。"
        )

    rules_item = items.get(rules_uid, {})
    fname = rules_item.get("file") or f"{rules_uid}.yaml"
    path = root / "profiles" / fname
    if not path.is_file():
        return None, f"规则增强文件不存在：{path}"
    return path, ""


# ------------------------------------------------------------------ edit

def marker_lines(indent: str) -> list[str]:
    return [f"{indent}{BEGIN}"] + [f"{indent}- {r}" for r in RULES] + [f"{indent}{END}"]


def strip_managed(lines: list[str]) -> list[str]:
    out, inside = [], False
    for line in lines:
        s = line.strip()
        if s == BEGIN:
            inside = True
            continue
        if s == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def _block_range(lines: list[str], start: int) -> tuple[int, str]:
    """返回 prepend 列表的结束行下标（不含）和现有条目缩进。"""
    indent = "  "
    end = start + 1
    for idx in range(start + 1, len(lines)):
        raw = lines[idx]
        s = raw.strip()
        if not s or s.startswith("#"):
            end = idx + 1
            continue
        if not raw.startswith((" ", "\t", "-")):
            break
        if s.startswith("-"):
            indent = raw[: len(raw) - len(raw.lstrip())]
        end = idx + 1
    return end, indent


def render(text: str) -> tuple[str, str]:
    """把标记块写进 prepend，返回 (新内容, 动作说明)。"""
    lines = strip_managed(text.splitlines())
    start = None
    for idx, raw in enumerate(lines):
        if re.match(r"^prepend\s*:", raw):
            start = idx
            break

    if start is None:
        body = ["prepend:"] + marker_lines("  ")
        if lines and lines[-1].strip():
            lines.append("")
        return "\n".join(lines + body) + "\n", "新增 prepend 段"

    end, indent = _block_range(lines, start)
    body = lines[start + 1 : end]

    head = lines[start]
    if head.split(":", 1)[1].strip() not in ("", "[]"):
        # 少见：prepend 写成行内列表，直接换行重建
        indent = "  "
        body = []

    lines[start] = "prepend:"
    merged = lines[: start + 1] + marker_lines(indent) + body + lines[end:]
    return "\n".join(merged) + "\n", "写入 prepend"


def drop(text: str) -> tuple[str, bool]:
    lines = text.splitlines()
    new = strip_managed(lines)
    changed = new != lines
    for idx, raw in enumerate(new):
        if re.match(r"^prepend\s*:\s*$", raw):
            # prepend 空了就还原成 `prepend: []`，但保留原来的空行
            end, _ = _block_range(new, idx)
            body = new[idx + 1 : end]
            if not any(l.strip() for l in body):
                trailing = len(body)
                while trailing and not body[trailing - 1].strip():
                    trailing -= 1
                new = new[: idx + 1] + body[trailing:] + new[end:]
                new[idx] = "prepend: []"
            break
    return "\n".join(new) + ("\n" if new else ""), changed


# ------------------------------------------------------------------ live

def mihomo_socket() -> Path | None:
    if sys.platform == "win32":
        return None
    cands = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid is not None:
        cands.append(Path(f"/var/run/clash-verge-service/users/{uid}/verge-mihomo.sock"))
    for root in verge_roots():
        cands.append(root / "verge-mihomo.sock")
    for p in cands:
        if p.exists():
            return p
    return None


def _dechunk(body: bytes) -> bytes:
    """mihomo 用 chunked 编码回包，手工解一下，免得依赖 http.client 的 socket 适配。"""
    out, i = [], 0
    while i < len(body):
        j = body.find(b"\r\n", i)
        if j < 0:
            break
        try:
            size = int(body[i:j].split(b";")[0], 16)
        except ValueError:
            return body
        if size == 0:
            break
        start = j + 2
        out.append(body[start : start + size])
        i = start + size + 2
    return b"".join(out)


def live_rules() -> list[str] | None:
    sock_path = mihomo_socket()
    if not sock_path:
        return None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(str(sock_path))
        s.sendall(b"GET /rules HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
        s.close()
        raw = b"".join(chunks)
        head, _, body = raw.partition(b"\r\n\r\n")
        if b"chunked" in head.lower():
            body = _dechunk(body)
        data = json.loads(body.decode("utf-8", "replace"))
        return [r.get("payload", "") for r in data.get("rules", [])]
    except Exception:
        return None


def live_status() -> tuple[bool | None, str]:
    rules = live_rules()
    if rules is None:
        return None, "读不到内核规则（需要 Clash Verge 在运行，且是 macOS 服务模式）"
    present = sum(1 for r in RULES if r in rules)
    if present == len(RULES):
        return True, f"内核已加载 {present}/{len(RULES)} 条拦截规则"
    if present == 0:
        return False, "内核里没有这些规则（改完需要在 Clash Verge 里点一次“重新应用配置”）"
    return False, f"内核里只有 {present}/{len(RULES)} 条（可能被上游订阅覆盖，重新应用一次）"


# ------------------------------------------------------------------ cli

def cmd_status(as_json: bool) -> int:
    root = verge_root()
    info = {"verge": str(root) if root else None, "file": None, "installed": False,
            "live": None, "live_hint": "", "rules": RULES}

    if root:
        path, err = target_file(root)
        if path:
            info["file"] = str(path)
            info["installed"] = BEGIN in path.read_text(encoding="utf-8")
        else:
            info["live_hint"] = err

    ok, hint = live_status()
    info["live"] = ok
    info["live_hint"] = hint

    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print("网络层（Clash Verge 规则）")
    if not root:
        print("  - 未检测到 Clash Verge，跳过（不影响前两层）")
        return 0
    if not info["file"]:
        print(f"  ! {info['live_hint']}")
        print("  - 只封遥测域名：sentry.io / statsigapi.net / statsig.anthropic.com / datadoghq.com")
        return 0
    if info["installed"]:
        print(f"  ✓ {info['file']} — 已写入 {len(RULES)} 条 REJECT")
    else:
        print(f"  - {info['file']} — 未写入（执行 ./no-claude.sh block install）")
    mark = "✓" if ok else ("!" if ok is False else "-")
    print(f"  {mark} {hint}")
    return 0


def cmd_install(dry: bool) -> int:
    root = verge_root()
    if not root:
        print("未检测到 Clash Verge，跳过网络层。")
        print("（如果你用别的代理工具，可以手动把下面这些域名设为 REJECT）")
        for r in RULES:
            print("  " + r)
        return 0

    path, err = target_file(root)
    if not path:
        print("!" + err, file=sys.stderr)
        return 1

    old = path.read_text(encoding="utf-8")
    new, action = render(old)
    if new == old:
        print(f"✓ 已是最新：{path}")
    elif dry:
        print(f"[dry-run] 将修改 {path}（{action}，{len(RULES)} 条规则）")
    else:
        bak = path.with_suffix(path.suffix + ".no-claude.bak")
        if not bak.exists():
            bak.write_text(old, encoding="utf-8")
        path.write_text(new, encoding="utf-8")
        print(f"✓ 已写入 {path}（备份 {bak.name}）")

    print()
    for r in RULES:
        print("  " + r)
    print()
    ok, hint = live_status()
    if ok:
        print(f"✓ {hint}")
    else:
        print(f"! {hint}")
        print("  打开 Clash Verge → 订阅 → 点一下当前配置（或“重新应用配置”），规则才会进内核。")
        print("  之后用 ./no-claude.sh status 复查。")
    return 0


def cmd_uninstall(dry: bool) -> int:
    root = verge_root()
    if not root:
        print("未检测到 Clash Verge，无需处理。")
        return 0
    path, err = target_file(root)
    if not path:
        print("!" + err, file=sys.stderr)
        return 1

    old = path.read_text(encoding="utf-8")
    if BEGIN not in old:
        print("没有 no-claude 写入的规则，无需处理。")
        return 0
    new, _ = drop(old)
    if dry:
        print(f"[dry-run] 将从 {path} 移除规则")
        return 0
    path.write_text(new, encoding="utf-8")
    print(f"✓ 已从 {path} 移除 no-claude 规则")
    print("  规则要等下一次“重新应用配置”才会离开内核。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="block.py", description="把遥测域名写进 Clash Verge 的 REJECT 规则")
    ap.add_argument("cmd", nargs="?", default="status", choices=["status", "install", "uninstall"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--version", action="version", version=f"no-claude block {VERSION}")
    a = ap.parse_args()

    if a.cmd == "status":
        return cmd_status(a.json)
    if a.cmd == "install":
        return cmd_install(a.dry_run)
    return cmd_uninstall(a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
