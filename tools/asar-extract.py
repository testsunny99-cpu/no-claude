#!/usr/bin/env python3
"""asar-extract.py — 解包 Electron 应用的 app.asar（纯标准库）。

用之前请自担风险：
- 已签名的应用，篡改后无法通过签名校验，打不开。
- 解包主要用于"读"，确认遥测开关、域名、环境变量名。
- 本工具不修改应用，也不提供"重新打包"功能。

用法:
    python3 tools/asar-extract.py /Applications/Claude.app/Contents/Resources/app.asar /tmp/claude_asar
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path


def extract(path: Path, out_dir: Path) -> None:
    data = path.read_bytes()
    if data[:4] != b"\x00\x00\x00\x00":
        raise SystemExit(f"{path} 不是 asar 文件（目录列表也可能是 asar）。")

    def u32(off: int) -> int:
        return struct.unpack_from("<I", data, off)[0]

    header_size = u32(12)
    header = json.loads(data[16 : 16 + header_size].decode("utf-8"))
    base = 8 + header_size  # 文件内容区起点

    total = 0
    for key, value in _walk(header["files"], path.name, 0):
        # files 里的 key 是名字，subdirs 逐级拼
        pass

    _extract_files(header.get("files", {}), out_dir, data, base)
    print(f"完成: {out_dir}（{len(list(out_dir.rglob('*')))} 个条目）")


def _extract_files(node: dict, out: Path, data: bytes, base: int) -> None:
    for name, info in node.items():
        p = out / name
        if "files" in info:  # 目录
            p.mkdir(parents=True, exist_ok=True)
            _extract_files(info["files"], p, data, base)
        else:
            offset, size = info.get("offset", 0), info.get("size", 0)
            p.parent.mkdir(parents=True, exist_ok=True)
            if offset is None:
                p.write_bytes(b"")
                continue
            start = base + int(offset)
            with open(p, "wb") as f:
                chunk = data[start : start + int(size)]
                f.write(chunk)
            if "link" in info:
                link_target = info["link"]
                try:
                    os.symlink(link_target, p)
                except OSError:
                    pass


def _walk(node, prefix, depth):
    for name, info in node.items():
        yield prefix, name, info
        if "files" in info:
            yield from _walk(info["files"], f"{prefix}/{name}", depth + 1)


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())